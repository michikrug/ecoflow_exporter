# EcoFlow Exporter

EcoFlow Exporter is a Prometheus exporter that collects metrics from EcoFlow devices via the EcoFlow API and exposes them in a format that Prometheus can scrape.

## Features

- Fetches real-time data from EcoFlow devices
- Exposes Prometheus-compatible metrics
- Configurable via environment variables
- Automatic filtering of non-relevant metrics
- Handles metric expiration to prevent stale data
- Runs as a standalone HTTP server

## Grafana Dashboard

[Dashboard JSON for Import](https://gist.githubusercontent.com/michikrug/dbe55182c90056d4bc1ba661b4f79818/raw/7aba18613249c9eb962b5de81fa2623dbe6d3e27/ecoflow.json)

![Dashboard 1](images/GrafanaDashboard1.png?raw=true)
![Dashboard 2](images/GrafanaDashboard2.png?raw=true)
![Dashboard 3](images/GrafanaDashboard3.png?raw=true)

## Repository

This project is hosted at [github.com/michikrug/ecoflow-exporter](https://github.com/michikrug/ecoflow-exporter).

## Requirements

- Go 1.18+
- An EcoFlow account with API access
- Prometheus for scraping metrics

## Installation

Clone the repository and build the exporter:

```sh
git clone https://github.com/michikrug/ecoflow-exporter.git
cd ecoflow-exporter
go build -o ecoflow-exporter
```

## Configuration

Create a `.env` file or use environment variables:

```ini
ECOFLOW_ACCESSKEY=your_access_key
ECOFLOW_SECRETKEY=your_secret_key
DEVICE_SN=your_device_serial_number
DEVICE_NAME=your_device_name
ECOFLOW_API_ENDPOINT=api-e.ecoflow.com
EXPORTER_PORT=9090
COLLECTING_INTERVAL=30s
EXPIRATION_THRESHOLD=15m
```

### Running the Exporter

```sh
./ecoflow-exporter
```

### Running with Docker

```sh
docker build -t ecoflow-exporter .
docker run --env-file .env -p 9090:9090 ecoflow-exporter
```

## Metrics

The exporter exposes metrics at:

```sh
http://localhost:9090/metrics
```

Example metrics:

```sh
# HELP ecoflow_ac_set_watts AC set power in watts
# TYPE ecoflow_ac_set_watts gauge
ecoflow_ac_set_watts{device="MyEcoFlow"} 250.0
```

### List of payload metrics

_Hint: Some do not provide useful data_

- `ac_off_flag`
- `ac_set_watts`
- `anti_back_flow_flag`
- `bat_err_code`
- `bat_error_inv_load_limit`
- `bat_input_cur`
- `bat_input_volt`
- `bat_input_watts`
- `bat_load_limit_flag`
- `bat_off_flag`
- `bat_op_volt`
- `bat_output_load_limit`
- `bat_soc`
- `bat_statue`
- `bat_system`
- `bat_temp`
- `bat_warning_code`
- `bms_req_chg_amp`
- `bms_req_chg_vol`
- `bp_type`
- `chg_remain_time`
- `cons_num`
- `cons_watt`
- `dsg_remain_time`
- `dynamic_watts`
- `esp_tempsensor`
- `event_info_adjacent_channel_count`
- `event_info_bandwith`
- `event_info_bssid_count`
- `event_info_bt_state`
- `event_info_channel_count`
- `event_info_communication_channel`
- `event_info_connect_time`
- `event_info_cpu`
- `event_info_is_hidden`
- `event_info_mqtt_error_count`
- `event_info_mqtt_success_count`
- `event_info_type`
- `event_info_wifi_error_count`
- `event_info_wifi_standard`
- `event_info_wifi_success_count`
- `feed_protect`
- `fiso_rxyz`
- `fload_limit_out`
- `gene_num`
- `gene_watt`
- `grid_cons_watts`
- `grid_ovp_cnt`
- `heartbeat_frequency`
- `heartbeat_type2_frequency`
- `history_bat_input_watts`
- `history_grid_cons_watts`
- `history_inv_output_watts`
- `history_inv_to_plug_watts`
- `history_permanent_watts`
- `history_plug_total_watts`
- `history_pv_to_inv_watts`
- `install_country`
- `install_town`
- `interface_conn_flag`
- `inv_brightness`
- `inv_demand_watts`
- `inv_err_code`
- `inv_freq`
- `inv_input_volt`
- `inv_load_limit_flag`
- `inv_on_off`
- `inv_op_volt`
- `inv_output_cur`
- `inv_output_load_limit`
- `inv_output_watts`
- `inv_relay_status`
- `inv_statue`
- `inv_temp`
- `inv_to_other_watts`
- `inv_to_plug_watts`
- `inv_warn_code`
- `llc_err_code`
- `llc_input_volt`
- `llc_off_flag`
- `llc_op_volt`
- `llc_statue`
- `llc_temp`
- `llc_warning_code`
- `lower_limit`
- `mesh_id`
- `mesh_layel`
- `mqtt_connect_return_code`
- `mqtt_err`
- `mqtt_err_time`
- `mqtt_last_dis_reason`
- `mqtt_sock_errno`
- `mqtt_tls_last_err`
- `mqtt_tls_stack_err`
- `noise_floor`
- `parent_mac`
- `permanent_watts`
- `plug_total_watts`
- `pv_power_limit_ac_power`
- `pv_to_inv_watts`
- `pv1_ctrl_mppt_off_flag`
- `pv1_err_code`
- `pv1_input_cur`
- `pv1_input_volt`
- `pv1_input_watts`
- `pv1_op_volt`
- `pv1_relay_status`
- `pv1_statue`
- `pv1_temp`
- `pv1_warn_code`
- `pv2_ctrl_mppt_off_flag`
- `pv2_err_code`
- `pv2_input_cur`
- `pv2_input_volt`
- `pv2_input_watts`
- `pv2_op_volt`
- `pv2_relay_status`
- `pv2_statue`
- `pv2_temp`
- `pv2_warning_code`
- `rated_power`
- `reset_count`
- `reset_reason`
- `rssi_threshold`
- `rssi_variance`
- `rst_brownout`
- `rst_deepsleep`
- `rst_ext`
- `rst_int_wdt`
- `rst_panic`
- `rst_poweron`
- `rst_sdio`
- `rst_sw`
- `rst_task_wdt`
- `rst_unknow`
- `rst_wdt`
- `self_mac`
- `space_demand_watts`
- `sta_ip_addr`
- `stack_free`
- `stack_min_free`
- `supply_priority`
- `update_time`
- `upper_limit`
- `uwload_limit_flag`
- `uwlow_light_flag`
- `uwsoc_flag`
- `wifi_connect_channel`
- `wifi_encrypt_mode`
- `wifi_err`
- `wifi_err_time`
- `wifi_firmware_version`
- `wifi_rssi`
- `wireless_err_code`
- `wireless_warn_code`

## License

This project is licensed under the GNU General Public License v3. See the [LICENSE](LICENSE) file for details.

## Contributions

Contributions are welcome! Feel free to open an issue or submit a pull request.

## Contact

For questions or support, open an issue on [GitHub](https://github.com/michikrug/ecoflow-exporter/issues).
