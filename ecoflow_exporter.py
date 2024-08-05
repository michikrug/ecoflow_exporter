import base64
import json
import logging as log
import os
import re
import signal
import sys
import time
import hashlib
import hmac

import requests
from prometheus_client import REGISTRY, Gauge, start_http_server

from dotenv import load_dotenv

load_dotenv()

DIVISORS = {
    'ecoflow_ac_set_watts': 10,
    'ecoflow_bat_error_inv_load_limit': 10,
    'ecoflow_bat_input_cur': 10,
    'ecoflow_bat_input_volt': 10,
    'ecoflow_bat_input_watts': 10,
    'ecoflow_bat_op_volt': 10,
    'ecoflow_bat_output_load_limit': 10,
    'ecoflow_bat_temp': 10,
    'ecoflow_dynamic_watts': 10,
    'ecoflow_fload_limit_out': 10,
    'ecoflow_gene_watt': 10,
    'ecoflow_grid_cons_watts': 10,
    'ecoflow_inv_brightness': 10,
    'ecoflow_inv_dc_cur': 10,
    'ecoflow_inv_demand_watts': 10,
    'ecoflow_inv_freq': 10,
    'ecoflow_inv_input_volt': 100,
    'ecoflow_inv_op_volt': 10,
    'ecoflow_inv_output_cur': 100,
    'ecoflow_inv_output_load_limit': 10,
    'ecoflow_inv_output_watts': 10,
    'ecoflow_inv_temp': 10,
    'ecoflow_inv_to_other_watts': 10,
    'ecoflow_inv_to_plug_watts': 10,
    'ecoflow_llc_input_volt': 10,
    'ecoflow_llc_op_volt': 100,
    'ecoflow_llc_temp': 10,
    'ecoflow_permanent_watts': 10,
    'ecoflow_plug_total_watts': 10,
    'ecoflow_pv_power_limit_ac_power': 10,
    'ecoflow_pv_to_inv_watts': 10,
    'ecoflow_pv1_input_cur': 10,
    'ecoflow_pv1_input_volt': 10,
    'ecoflow_pv1_input_watts': 10,
    'ecoflow_pv1_op_volt':  100,
    'ecoflow_pv1_temp': 10,
    'ecoflow_pv2_input_cur': 10,
    'ecoflow_pv2_input_volt': 10,
    'ecoflow_pv2_input_watts': 10,
    'ecoflow_pv2_op_volt': 100,
    'ecoflow_pv2_temp': 10,
    'ecoflow_rated_power': 10,
    'ecoflow_space_demand_watts': 10
}


class EcoflowMetricException(Exception):
    pass


class EcoflowApi:
    def __init__(self, api_endpoint, accesskey, secretkey, device_sn):
        self.accesskey = accesskey
        self.secretkey = secretkey
        self.api_endpoint = api_endpoint
        self.device_sn = device_sn

    def get_quota(self):
        timestamp = str(int(time.time() * 1000))
        nonce = base64.b64encode(hashlib.sha256(timestamp.encode()).digest()).decode()[:-1]
        to_sign = f'accessKey={self.accesskey}&nonce={nonce}&timestamp={timestamp}'
        sign = hmac.new(self.secretkey.encode(),to_sign.encode(), hashlib.sha256).hexdigest()
        url = f"https://{self.api_endpoint}/iot-open/sign/device/quota/all?sn={self.device_sn}"

        headers = {
            'Content-Type': 'application/json;charset=UTF-8',
            'accessKey': self.accesskey,
            'nonce': nonce,
            'timestamp': timestamp,
            'sign': sign,
        }

        log.info(f"Getting payload from {url}")
        request = requests.get(url, headers=headers)
        return self.get_json_response(request)

    def get_json_response(self, request):
        if request.status_code != 200:
            raise Exception(f"Got HTTP status code {request.status_code}: {request.text}")

        try:
            response = json.loads(request.text)
            response_message = response["message"]
        except KeyError as key:
            raise Exception(f"Failed to extract key {key} from {response}")
        except Exception as error:
            raise Exception(f"Failed to parse response: {request.text} Error: {error}")

        if response_message.lower() != "success":
            raise Exception(f"{response_message}")

        return response["data"]


class EcoflowMetric:
    def __init__(self, ecoflow_payload_key, device_name, documentation=None):
        self.ecoflow_payload_key = ecoflow_payload_key
        self.device_name = device_name
        self.name = f"ecoflow_{self.convert_ecoflow_key_to_prometheus_name()}"
        self.metric = Gauge(self.name, documentation or f"value from API object key {ecoflow_payload_key}", labelnames=["device"])
        self.value = None
        self.last_update_time = None

    def convert_ecoflow_key_to_prometheus_name(self):
        # bms_bmsStatus.maxCellTemp -> bms_bms_status_max_cell_temp
        # pd.ext4p8Port -> pd_ext4p8_port
        key = self.ecoflow_payload_key.split('.')[1].replace('.', '_').replace('Statue', 'Status')
        new = key[0].lower()
        for character in key[1:]:
            if character.isupper() and not new[-1] == '_':
                new += '_'
            new += character.lower()
        # Check that metric name complies with the data model for valid characters
        # https://prometheus.io/docs/concepts/data_model/#metric-names-and-labels
        if not re.match("[a-zA-Z_:][a-zA-Z0-9_:]*", new):
            raise EcoflowMetricException(f"Cannot convert payload key {self.ecoflow_payload_key} to comply with the Prometheus data model. Please, raise an issue!")
        return new

    def set(self, value):
        if self.name in DIVISORS:
            value = value / DIVISORS[self.name]
        log.debug(f"Set {self.name} = {value}")
        if self.value != value:
            self.metric.labels(device=self.device_name).set(value)
            self.value = value
            self.last_update_time = time.time()

    def clear(self):
        log.debug(f"Clear {self.name}")
        self.metric.clear()
        self.value = None


class Worker:
    def __init__(self, ecoflow_api, device_name, collecting_interval_seconds=10, expiration_threshold=300):
        self.ecoflow_api = ecoflow_api
        self.device_name = device_name
        self.collecting_interval_seconds = collecting_interval_seconds
        self.metrics_collector = []
        self.expiration_threshold = expiration_threshold

    def loop(self):
        while True:
            self.process_payload(self.ecoflow_api.get_quota())
            self.clear_expired_metrics()
            time.sleep(self.collecting_interval_seconds)

    def clear_expired_metrics(self):
        current_time = time.time()
        for metric in self.metrics_collector:
            if metric.value is not None and current_time - metric.last_update_time > self.expiration_threshold:
                metric.clear()
                log.info(f"Cleared expired metric {metric.name}")

    def get_metric_by_ecoflow_payload_key(self, ecoflow_payload_key):
        metric = next((metric for metric in self.metrics_collector if metric.ecoflow_payload_key == ecoflow_payload_key), None)
        if metric:
            log.debug(f"Found metric {metric.name} linked to {ecoflow_payload_key}")
        else:
            log.debug(f"Cannot find metric linked to {ecoflow_payload_key}")
        return metric

    def process_payload(self, params):
        log.debug(f"Processing params: {params}")
        for ecoflow_payload_key in filter(lambda key: key.startswith('20_1.'), params.keys()):
            ecoflow_payload_value = params[ecoflow_payload_key]
            if not isinstance(ecoflow_payload_value, (int, float)):
                log.warning(f"Skipping unsupported metric {ecoflow_payload_key}: {ecoflow_payload_value}")
                continue

            metric = self.get_metric_by_ecoflow_payload_key(ecoflow_payload_key)
            if not metric:
                try:
                    metric = EcoflowMetric(ecoflow_payload_key, self.device_name)
                except EcoflowMetricException as error:
                    log.error(error)
                    continue
                log.info(f"Created new metric from payload key {metric.ecoflow_payload_key} -> {metric.name}")
                self.metrics_collector.append(metric)

            metric.set(ecoflow_payload_value)


def signal_handler(signum, frame):
    log.info(f"Received signal {signum}. Exiting...")
    sys.exit(0)


def main():
    # Register the signal handler for SIGTERM
    signal.signal(signal.SIGTERM, signal_handler)

    # Disable Process and Platform collectors
    for coll in list(REGISTRY._collector_to_names.keys()):
        REGISTRY.unregister(coll)

    log_level = os.getenv("LOG_LEVEL", "INFO")

    match log_level:
        case "DEBUG":
            log_level = log.DEBUG
        case "INFO":
            log_level = log.INFO
        case "WARNING":
            log_level = log.WARNING
        case "ERROR":
            log_level = log.ERROR
        case _:
            log_level = log.INFO

    log.basicConfig(stream=sys.stdout, level=log_level, format='%(asctime)s %(levelname)-7s %(message)s')

    device_sn = os.getenv("DEVICE_SN")
    device_name = os.getenv("DEVICE_NAME") or device_sn
    ecoflow_accesskey = os.getenv("ECOFLOW_ACCESSKEY")
    ecoflow_secretkey = os.getenv("ECOFLOW_SECRETKEY")
    ecoflow_api_endpoint = os.getenv("ECOFLOW_API_ENDPOINT", "api-e.ecoflow.com")
    exporter_port = int(os.getenv("EXPORTER_PORT", "9090"))
    collecting_interval_seconds = int(os.getenv("COLLECTING_INTERVAL", "30"))
    expiration_threshold = int(os.getenv("EXPIRATION_THRESHOLD", "300"))

    if (not device_sn or not ecoflow_accesskey or not ecoflow_secretkey):
        log.error("Please, provide all required environment variables: DEVICE_SN, ECOFLOW_ACCESSKEY, ECOFLOW_SECRETKEY")
        sys.exit(1)

    ecoflow_api = EcoflowApi(ecoflow_api_endpoint, ecoflow_accesskey, ecoflow_secretkey, device_sn)
    metrics = Worker(ecoflow_api, device_name, collecting_interval_seconds, expiration_threshold)

    start_http_server(exporter_port)

    try:
        metrics.loop()

    except KeyboardInterrupt:
        log.info("Received KeyboardInterrupt. Exiting...")
        sys.exit(0)


if __name__ == '__main__':
    main()
