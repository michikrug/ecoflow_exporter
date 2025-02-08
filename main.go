package main

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/joho/godotenv"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

var Divisors = map[string]float64{
	"ac_set_watts":             10,
	"anti_back_flow_flag":      10,
	"bat_error_inv_load_limit": 10,
	"bat_input_volt":           10,
	"bat_input_watts":          10,
	"bat_op_volt":              10,
	"bat_output_load_limit":    10,
	"bat_temp":                 10,
	"dynamic_watts":            10,
	"fload_limit_out":          10,
	"gene_watt":                10,
	"grid_cons_watts":          10,
	"inv_brightness":           10,
	"inv_demand_watts":         10,
	"inv_freq":                 10,
	"inv_input_volt":           10,
	"inv_op_volt":              10,
	"inv_output_load_limit":    10,
	"inv_output_watts":         10,
	"inv_temp":                 10,
	"inv_to_other_watts":       10,
	"inv_to_plug_watts":        10,
	"llc_input_volt":           10,
	"llc_op_volt":              100,
	"llc_temp":                 10,
	"permanent_watts":          10,
	"plug_total_watts":         10,
	"pv_power_limit_ac_power":  10,
	"pv_to_inv_watts":          10,
	"pv1_input_cur":            10,
	"pv1_input_volt":           10,
	"pv1_input_watts":          10,
	"pv1_op_volt":              100,
	"pv1_temp":                 10,
	"pv2_input_cur":            10,
	"pv2_input_volt":           10,
	"pv2_input_watts":          10,
	"pv2_op_volt":              100,
	"pv2_temp":                 10,
	"rated_power":              10,
	"space_demand_watts":       10,
}

type EcoflowResponse struct {
	Data map[string]interface{} `json:"data"`
}

type Metric struct {
	gauge      prometheus.Gauge
	lastValue  float64
	lastUpdate time.Time
	expired    bool
}

type Worker struct {
	client              *http.Client
	ecoflowEndpoint     string
	ecoflowAccessKey    string
	ecoflowSecretKey    string
	deviceSN            string
	deviceName          string
	metricsRegistry     *prometheus.Registry
	metricsCollector    map[string]*Metric
	collectingInterval  int
	expirationThreshold int
}

var invalidChars = regexp.MustCompile(`[^a-zA-Z0-9_]`)
var prefixPattern = regexp.MustCompile(`^[0-9]+_[0-9]+\.`)
var camelCaseToSnake = regexp.MustCompile(`([a-z0-9])([A-Z])`)

func sanitizeMetricName(key string) string {
	// Strip the prefix pattern if present
	key = prefixPattern.ReplaceAllString(key, "")
	// Convert camelCase to snake_case
	key = camelCaseToSnake.ReplaceAllString(key, "${1}_${2}")
	// Replace "Statue" with "Status"
	key = strings.ReplaceAll(key, "Statue", "Status")
	// Convert all invalid characters to underscores
	key = invalidChars.ReplaceAllString(key, "_")
	// Ensure it starts with a valid character
	if !regexp.MustCompile(`^[a-zA-Z_]`).MatchString(key) {
		key = "metric_" + key
	}
	return strings.ToLower(key)
}

func NewWorker(endpoint, accessKey, secretKey, deviceSN, deviceName string, interval, expiration int) *Worker {
	return &Worker{
		client:              &http.Client{},
		ecoflowEndpoint:     endpoint,
		ecoflowAccessKey:    accessKey,
		ecoflowSecretKey:    secretKey,
		deviceSN:            deviceSN,
		deviceName:          deviceName,
		metricsRegistry:     prometheus.NewRegistry(),
		metricsCollector:    make(map[string]*Metric),
		collectingInterval:  interval,
		expirationThreshold: expiration,
	}
}

func (w *Worker) generateSignature(timestamp string) (string, string) {
	nonce := base64.StdEncoding.EncodeToString([]byte(timestamp))[:len(timestamp)]
	signatureData := fmt.Sprintf("accessKey=%s&nonce=%s&timestamp=%s", w.ecoflowAccessKey, nonce, timestamp)
	h := hmac.New(sha256.New, []byte(w.ecoflowSecretKey))
	h.Write([]byte(signatureData))
	return nonce, hex.EncodeToString(h.Sum(nil))
}

func (w *Worker) fetchData() (*EcoflowResponse, error) {
	timestamp := fmt.Sprintf("%d", time.Now().UnixMilli())
	nonce, sign := w.generateSignature(timestamp)
	url := fmt.Sprintf("https://%s/iot-open/sign/device/quota/all?sn=%s", w.ecoflowEndpoint, w.deviceSN)

	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json;charset=UTF-8")
	req.Header.Set("accessKey", w.ecoflowAccessKey)
	req.Header.Set("nonce", nonce)
	req.Header.Set("timestamp", timestamp)
	req.Header.Set("sign", sign)

	resp, err := w.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("failed to fetch data, status code: %d", resp.StatusCode)
	}

	var data EcoflowResponse
	if err := json.NewDecoder(resp.Body).Decode(&data); err != nil {
		return nil, err
	}

	return &data, nil
}

func (w *Worker) updateMetrics() {
	for {
		data, err := w.fetchData()
		if err != nil {
			log.Println("Error fetching data:", err)
		} else {
			for key, value := range data.Data {
				// Skip metrics that do not start with "20_1."
				if !strings.HasPrefix(key, "20_1.") {
					continue
				}

				// Special handling for time-based metrics, e.g., "20_1.updateTime"
				if key == "20_1.updateTime" {
					if strValue, ok := value.(string); ok {
						// Parse the timestamp assuming the format "2006-01-02 15:04:05"
						t, err := time.Parse("2006-01-02 15:04:05", strValue)
						if err != nil {
							log.Printf("Invalid time format for %s: %s", key, strValue)
							continue
						}
						// Convert time to Unix timestamp (seconds) and update the metric
						w.setMetric(key, float64(t.Unix()))
					} else {
						log.Printf("Invalid type for time metric %s: %v", key, value)
					}
					continue
				}

				// Normal numeric metric handling
				if numValue, ok := value.(float64); ok {
					w.setMetric(key, numValue)
				} else {
					log.Printf("Skipping non-numeric metric: %s = %v", key, value)
				}
			}
		}
		w.clearExpiredMetrics()
		time.Sleep(time.Duration(w.collectingInterval) * time.Second)
	}
}

func (w *Worker) setMetric(key string, value float64) {
	metricKey := sanitizeMetricName(key)
	if divisor, exists := Divisors[metricKey]; exists {
		value /= divisor
	}
	if metric, exists := w.metricsCollector[metricKey]; exists {
		// Only update the metric if the value has changed
		if metric.lastValue != value {
			if metric.expired {
				w.metricsRegistry.MustRegister(metric.gauge) // Re-register expired metric
				metric.expired = false
				log.Printf("Re-registered expired metric: %s", metricKey)
			}
			metric.gauge.Set(value)
			metric.lastValue = value
			metric.lastUpdate = time.Now()
		}
	} else {
		gauge := prometheus.NewGauge(prometheus.GaugeOpts{
			Name:        fmt.Sprintf("ecoflow_%s", metricKey),
			Help:        fmt.Sprintf("Metric from EcoFlow API: %s.%s", w.deviceName, key),
			ConstLabels: prometheus.Labels{"device": w.deviceName},
		})
		w.metricsRegistry.MustRegister(gauge)
		gauge.Set(value)
		w.metricsCollector[metricKey] = &Metric{gauge: gauge, lastValue: value, lastUpdate: time.Now(), expired: false}
		log.Printf("Registered new metric: %s", metricKey)
	}
}

func (w *Worker) clearExpiredMetrics() {
	now := time.Now()
	for key, metric := range w.metricsCollector {
		if now.Sub(metric.lastUpdate).Seconds() > float64(w.expirationThreshold) {
			w.metricsRegistry.Unregister(metric.gauge) // Remove metric from Prometheus
			metric.expired = true
			metric.lastUpdate = now
			log.Printf("Marked metric as expired: %s", key)
		}
	}
}

// Check if all required environment variables are set
func checkEnvVars(vars []string) {
	for _, v := range vars {
		if os.Getenv(v) == "" {
			log.Fatalf("Missing required environment variable: %s", v)
		}
	}
}

func main() {
	if err := godotenv.Load(); err != nil {
		log.Println("No .env file found, relying on environment variables")
	}

	// Required environment variables
	requiredVars := []string{"DEVICE_SN", "ECOFLOW_ACCESSKEY", "ECOFLOW_SECRETKEY"}
	checkEnvVars(requiredVars)

	ecoflowAccessKey := os.Getenv("ECOFLOW_ACCESSKEY")
	ecoflowSecretKey := os.Getenv("ECOFLOW_SECRETKEY")

	deviceSN := os.Getenv("DEVICE_SN")
	deviceName := os.Getenv("DEVICE_NAME")
	if deviceName == "" {
		deviceName = deviceSN
	}

	ecoflowEndpoint := "api-e.ecoflow.com"
	if endpoint, exists := os.LookupEnv("ECOFLOW_API_ENDPOINT"); exists {
		ecoflowEndpoint = endpoint
	}

	collectingInterval := 30
	if interval, exists := os.LookupEnv("COLLECTING_INTERVAL"); exists {
		if i, err := strconv.Atoi(interval); err == nil {
			collectingInterval = i
		} else {
			log.Printf("Invalid COLLECTING_INTERVAL: %s", interval)
		}
	}

	expirationThreshold := 900
	if threshold, exists := os.LookupEnv("EXPIRATION_THRESHOLD"); exists {
		if t, err := strconv.Atoi(threshold); err == nil {
			expirationThreshold = t
		} else {
			log.Printf("Invalid EXPIRATION_THRESHOLD: %s", threshold)
		}
	}

	exporterPort := "9090"
	if port, exists := os.LookupEnv("EXPORTER_PORT"); exists {
		exporterPort = port
	}

	worker := NewWorker(ecoflowEndpoint, ecoflowAccessKey, ecoflowSecretKey, deviceSN, deviceName, collectingInterval, expirationThreshold)
	go worker.updateMetrics()

	http.Handle("/metrics", promhttp.HandlerFor(worker.metricsRegistry, promhttp.HandlerOpts{}))
	server := &http.Server{Addr: ":" + exporterPort}
	go func() {
		log.Printf("Starting HTTP server on port %s", exporterPort)
		if err := server.ListenAndServe(); err != nil {
			log.Fatalf("HTTP server failed: %s", err)
		}
	}()

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)
	<-sigChan

	log.Println("Shutting down...")
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := server.Shutdown(ctx); err != nil {
		log.Fatalf("Server shutdown failed: %s", err)
	}
}
