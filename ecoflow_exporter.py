import base64
import json
import logging as log
import os
import re
import signal
import ssl
import sys
import time
import uuid
from enum import IntEnum
from queue import Queue
from threading import Timer

import paho.mqtt.client as mqtt
import requests
from prometheus_client import REGISTRY, Counter, Gauge, start_http_server

import protos.platform_pb2 as platform
import protos.powerstream_pb2 as powerstream

from dotenv import load_dotenv

load_dotenv()

class CmdFuncs(IntEnum):
    DEFAULT = 0
    POWERSTREAM = 20
    SMART_PLUG = 2
    REPORTS = 254


class CmdIds(IntEnum):
    # powerstream
    HEARTBEAT = 1
    HEARTBEAT2 = 4
    SET_PERMANENT_WATTS = 129
    SET_SUPPLY_PRIORITY = 130
    SET_UNKNOWN_131 = 131
    SET_BAT_LOWER = 132
    SET_BAT_UPPER = 133
    SET_UNKNOWN_134 = 134
    SET_BRIGHTNESS = 135
    SET_UNKNOWN_136 = 136
    SET_UNKNOWN_137 = 137
    SET_UNKNOWN_138 = 138
    SET_FEED_PRIORITY = 143
    # cmd_func 254
    ENERGY_TOTAL_REPORT = 32


class RepeatTimer(Timer):
    def run(self):
        while not self.finished.wait(self.interval):
            self.function(*self.args, **self.kwargs)


class EcoflowMetricException(Exception):
    pass


class EcoflowAuthentication:
    def __init__(self, ecoflow_username, ecoflow_password):
        self.ecoflow_username = ecoflow_username
        self.ecoflow_password = ecoflow_password
        self.mqtt_url = "mqtt.ecoflow.com"
        self.mqtt_port = 8883
        self.mqtt_username = None
        self.mqtt_password = None
        self.mqtt_client_id = None
        self.authorize()

    def authorize(self):
        url = "https://api.ecoflow.com/auth/login"
        headers = {"lang": "en_US", "content-type": "application/json"}
        data = {"email": self.ecoflow_username,
                "password": base64.b64encode(self.ecoflow_password.encode()).decode(),
                "scene": "IOT_APP",
                "userType": "ECOFLOW"}

        log.info(f"Login to EcoFlow API {url}")
        request = requests.post(url, json=data, headers=headers)
        response = self.get_json_response(request)

        try:
            token = response["data"]["token"]
            user_id = response["data"]["user"]["userId"]
            user_name = response["data"]["user"]["name"]
        except KeyError as key:
            raise Exception(f"Failed to extract key {key} from response: {response}")

        log.info(f"Successfully logged in: {user_name}")

        url = "https://api.ecoflow.com/iot-auth/app/certification"
        headers = {"lang": "en_US", "authorization": f"Bearer {token}"}
        data = {"userId": user_id}

        log.info(f"Requesting IoT MQTT credentials {url}")
        request = requests.get(url, data=data, headers=headers)
        response = self.get_json_response(request)

        try:
            self.mqtt_url = response["data"]["url"]
            self.mqtt_port = int(response["data"]["port"])
            self.mqtt_username = response["data"]["certificateAccount"]
            self.mqtt_password = response["data"]["certificatePassword"]
            self.mqtt_client_id = f"ANDROID_{str(uuid.uuid4()).upper()}_{user_id}"
        except KeyError as key:
            raise Exception(f"Failed to extract key {key} from {response}")

        log.info(f"Successfully extracted account: {self.mqtt_username}")

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

        return response


class EcoflowMQTT():

    def __init__(self, message_queue, device_sn, username, password, addr, port, client_id, timeout_seconds):
        self.message_queue = message_queue
        self.addr = addr
        self.port = port
        self.username = username
        self.password = password
        self.client_id = client_id
        self.topic = f"/app/device/property/{device_sn}"
        self.timeout_seconds = timeout_seconds
        self.last_message_time = None
        self.client = None

        self.connect()

        self.idle_timer = RepeatTimer(10, self.idle_reconnect)
        self.idle_timer.daemon = True
        self.idle_timer.start()

        self.pdata_messages = {
            CmdFuncs.POWERSTREAM: {
                CmdIds.HEARTBEAT: powerstream.InverterHeartbeat(),
                CmdIds.HEARTBEAT2: powerstream.InverterHeartbeat2(),
                CmdIds.SET_PERMANENT_WATTS: powerstream.PermanentWattsPack(),
                CmdIds.SET_SUPPLY_PRIORITY: powerstream.SupplyPriorityPack(),
                CmdIds.SET_BAT_LOWER: powerstream.BatLowerPack(),
                CmdIds.SET_BAT_UPPER: powerstream.BatUpperPack(),
                CmdIds.SET_BRIGHTNESS: powerstream.InvBrightnessPack(),
                CmdIds.SET_FEED_PRIORITY: powerstream.FeedPriorityPack(),
                CmdIds.SET_UNKNOWN_131: powerstream.SetValue(),
                CmdIds.SET_UNKNOWN_134: powerstream.SetValue(),
                CmdIds.SET_UNKNOWN_136: powerstream.SetValue(),
                CmdIds.SET_UNKNOWN_137: powerstream.SetValue(),
                CmdIds.SET_UNKNOWN_138: powerstream.SetValue()
            },
            CmdFuncs.REPORTS: {
                CmdIds.ENERGY_TOTAL_REPORT: platform.BatchEnergyTotalReport()
            }
        }

    def connect(self):
        try:
            # Stop and disconnect existing client if it exists
            if self.client:
                self.client.loop_stop()
                self.client.disconnect()

            # Create a new MQTT client
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5, client_id=self.client_id)
            
            # Set username and password for the client
            self.client.username_pw_set(self.username, self.password)
            
            # Set TLS configuration
            self.client.tls_set(certfile=None, keyfile=None, cert_reqs=ssl.CERT_REQUIRED)
            self.client.tls_insecure_set(False)
            
            # Set the callback functions
            self.client.on_connect = self.on_connect
            self.client.on_disconnect = self.on_disconnect
            self.client.on_message = self.on_bytes_message

            log.info(f"Connecting to MQTT Broker {self.addr}:{self.port} using client id {self.client_id}")
            
            # Connect to the broker
            self.client.connect(self.addr, self.port)
            
            # Start the loop to process network traffic and dispatch callbacks
            self.client.loop_start()
            
        except Exception as e:
            log.error(f"Failed to connect to MQTT Broker {self.addr}:{self.port}. Error: {e}")

    def idle_reconnect(self):
        if self.last_message_time and time.time() - self.last_message_time > self.timeout_seconds:
            log.warning(f"No messages received for {self.timeout_seconds} seconds. Attempting to reconnect to MQTT.")
            
            while True:
                try:
                    log.info("Starting reconnection process.")
                    self.last_message_time = None
                    self.connect()
                    # Wait for connection to be established
                    time.sleep(5)
                    if self.client.is_connected():
                        log.info("Reconnection successful. Resuming normal operation.")
                        break

                    log.warning("Reconnection process timed out. Retrying...")

                except Exception as e:
                    log.error(f"Exception occurred during reconnection: {e}")

                finally:
                    time.sleep(5)  # Sleep to avoid busy-waiting and give time before retrying

    def on_connect(self, client, userdata, flags, reason_code, properties):
        # Initialize the time of last message at least once upon connection so that other things that rely on that to be
        # set (like idle_reconnect) work
        self.last_message_time = time.time()
        match reason_code:
            case 0:
                self.client.subscribe(self.topic)
                log.info(f"Subscribed to MQTT topic {self.topic}")
            case -1:
                log.error("Failed to connect to MQTT: connection timed out")
            case 1:
                log.error("Failed to connect to MQTT: incorrect protocol version")
            case 2:
                log.error("Failed to connect to MQTT: invalid client identifier")
            case 3:
                log.error("Failed to connect to MQTT: server unavailable")
            case 4:
                log.error("Failed to connect to MQTT: bad username or password")
            case 5:
                log.error("Failed to connect to MQTT: not authorised")
            case _:
                log.error(f"Failed to connect to MQTT: another error occured: {reason_code}")

        return client

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        if reason_code > 0:
            log.error(f"Unexpected MQTT disconnection: {reason_code}. Will auto-reconnect")
            time.sleep(5)

    def on_json_message(self, client, userdata, message):
        self.message_queue.put(message.payload.decode("utf-8"))
        self.last_message_time = time.time()

    def on_bytes_message(self, client, userdata, message):
        try:
            payload = message.payload
            payload_length = len(payload)
            
            while payload:
                packet = powerstream.SendHeaderMsg()
                packet.ParseFromString(payload)
                
                for msg in packet.msg:
                    cmd_id = msg.cmd_id if msg.HasField("cmd_id") else 0
                    cmd_func = msg.cmd_func if msg.HasField("cmd_func") else 0
                    
                    if cmd_func == CmdFuncs.REPORTS:
                        log.info(f"Skipping energy report message")
                        continue

                    if cmd_func not in self.pdata_messages or cmd_id not in self.pdata_messages[cmd_func]:
                        log.warning(f"No processor for cmd_func: {cmd_func} and cmd_id: {cmd_id} found")
                        continue
                    
                    pdata = self.pdata_messages[cmd_func][cmd_id]
                    pdata.ParseFromString(msg.pdata)
                    raw = {"params": {}}
                    
                    for descriptor, val in pdata.ListFields():
                        if val is not None:
                            name = descriptor.name
                            if name == "value":
                                name = f"set_{cmd_id}_value"
                            divisor = descriptor.GetOptions().Extensions[powerstream.mapping_options].divisor
                            val = val / divisor if divisor > 1 else val
                            raw["params"][name] = val
                    
                    log.info("Found %u fields", len(raw["params"]))
                    self.message_queue.put(json.dumps(raw))
                    self.last_message_time = time.time()
                
                # Break if the packet size matches the payload length
                if packet.ByteSize() >= payload_length:
                    break

                log.info("Found another frame in payload")

                # Update payload to the remaining part
                payload = payload[packet.ByteSize():]
                payload_length = len(payload)

        except Exception as error:
            log.error(f"Error processing bytes message: {error}")


class EcoflowMetric:
    def __init__(self, ecoflow_payload_key, device_name):
        self.ecoflow_payload_key = ecoflow_payload_key
        self.device_name = device_name
        self.name = f"ecoflow_{self.convert_ecoflow_key_to_prometheus_name()}"
        self.metric = Gauge(self.name, f"value from MQTT object key {ecoflow_payload_key}", labelnames=["device"])

    def convert_ecoflow_key_to_prometheus_name(self):
        # bms_bmsStatus.maxCellTemp -> bms_bms_status_max_cell_temp
        # pd.ext4p8Port -> pd_ext4p8_port
        key = self.ecoflow_payload_key.replace('.', '_')
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
        log.debug(f"Set {self.name} = {value}")
        self.metric.labels(device=self.device_name).set(value)

    def clear(self):
        log.debug(f"Clear {self.name}")
        self.metric.clear()


class Worker:
    def __init__(self, message_queue, device_name, collecting_interval_seconds=10):
        self.message_queue = message_queue
        self.device_name = device_name
        self.collecting_interval_seconds = collecting_interval_seconds
        self.metrics_collector = []
        self.online = Gauge("ecoflow_online", "1 if device is online", labelnames=["device"])
        self.mqtt_messages_receive_total = Counter("ecoflow_mqtt_messages_receive_total", "total MQTT messages", labelnames=["device"])

    def loop(self):
        time.sleep(self.collecting_interval_seconds)
        while True:
            queue_size = self.message_queue.qsize()
            if queue_size > 0:
                log.info(f"Processing {queue_size} event(s) from the message queue")
                self.online.labels(device=self.device_name).set(1)
                self.mqtt_messages_receive_total.labels(device=self.device_name).inc(queue_size)
            else:
                log.info("Message queue is empty. Assuming that the device is offline")
                self.online.labels(device=self.device_name).set(0)
                # Clear metrics for NaN (No data) instead of last value
                for metric in self.metrics_collector:
                    metric.clear()

            while not self.message_queue.empty():
                payload = self.message_queue.get()
                log.debug(f"Recived payload: {payload}")
                if payload is None:
                    continue

                try:
                    payload = json.loads(payload)
                    params = payload['params']
                except KeyError as key:
                    log.error(f"Failed to extract key {key} from payload: {payload}")
                except Exception as error:
                    log.error(f"Failed to parse MQTT payload: {payload} Error: {error}")
                    continue
                self.process_payload(params)

            time.sleep(self.collecting_interval_seconds)

    def get_metric_by_ecoflow_payload_key(self, ecoflow_payload_key):
        for metric in self.metrics_collector:
            if metric.ecoflow_payload_key == ecoflow_payload_key:
                log.debug(f"Found metric {metric.name} linked to {ecoflow_payload_key}")
                return metric
        log.debug(f"Cannot find metric linked to {ecoflow_payload_key}")
        return False

    def process_payload(self, params):
        log.debug(f"Processing params: {params}")
        for ecoflow_payload_key in params.keys():
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

            if ecoflow_payload_key == 'inv.acInVol' and ecoflow_payload_value == 0:
                ac_in_current = self.get_metric_by_ecoflow_payload_key('inv.acInAmp')
                if ac_in_current:
                    log.debug("Set AC inverter input current to zero because of zero inverter voltage")
                    ac_in_current.set(0)


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
    ecoflow_username = os.getenv("ECOFLOW_USERNAME")
    ecoflow_password = os.getenv("ECOFLOW_PASSWORD")
    exporter_port = int(os.getenv("EXPORTER_PORT", "9090"))
    collecting_interval_seconds = int(os.getenv("COLLECTING_INTERVAL", "10"))
    timeout_seconds = int(os.getenv("MQTT_TIMEOUT", "20"))

    if (not device_sn or not ecoflow_username or not ecoflow_password):
        log.error("Please, provide all required environment variables: DEVICE_SN, ECOFLOW_USERNAME, ECOFLOW_PASSWORD")
        sys.exit(1)

    try:
        auth = EcoflowAuthentication(ecoflow_username, ecoflow_password)
    except Exception as error:
        log.error(error)
        sys.exit(1)

    message_queue = Queue()

    EcoflowMQTT(message_queue, device_sn, auth.mqtt_username, auth.mqtt_password, auth.mqtt_url, auth.mqtt_port, auth.mqtt_client_id, timeout_seconds)

    metrics = Worker(message_queue, device_name, collecting_interval_seconds)

    start_http_server(exporter_port)

    try:
        metrics.loop()

    except KeyboardInterrupt:
        log.info("Received KeyboardInterrupt. Exiting...")
        sys.exit(0)


if __name__ == '__main__':
    main()
