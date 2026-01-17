"""Local lab simulator that publishes sensor data and echoes actuator feedback."""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from typing import Dict, List

from Device_connectors.mqtt_client import MqttClient

BASE_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
DEVICES_PATH = os.path.join(BASE_DIR, "catalog", "devices.json")


logger = logging.getLogger("LabSimulator")


class LabSimulator:
    def __init__(self, mqtt_host: str, mqtt_port: int, loop_sec: int = 10):
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.loop_sec = loop_sec
        self._mqtt = MqttClient(client_id="lab_simulator", host=mqtt_host, port=mqtt_port)
        self._lock = threading.Lock()
        self._labs: Dict[str, Dict] = {}
        self._devices_mtime = 0.0
        self._stop = threading.Event()

    # ------------------------------------------------------------------ setup
    def start(self):
        logging.getLogger("paho").setLevel(logging.WARNING)
        self._reload_devices(force=True)
        self._mqtt.connect()
        self._mqtt.subscribe("labs/+/actuators/+/cmd", self._on_actuator_command)
        logger.info("Simulator connected to MQTT %s:%s", self.mqtt_host, self.mqtt_port)
        # Publish initial OFF states for all actuators so dashboards are not blank
        now = int(time.time())
        for lab_id, lab in self._labs.items():
            for actuator in lab.get("actuators", []):
                aid = actuator["actuator_id"]
                state_topic = f"labs/{lab_id}/actuators/{aid}/state"
                state_payload = {"state": "OFF", "ts": now, "actuator_id": aid}
                self._mqtt.publish_json(state_topic, state_payload, retain=True)

    # ------------------------------------------------------------------ catalog handling
    def _reload_devices(self, force: bool = False):
        try:
            mtime = os.path.getmtime(DEVICES_PATH)
        except OSError:
            logger.warning("devices.json not found at %s", DEVICES_PATH)
            return
        if not force and mtime == self._devices_mtime:
            return
        with open(DEVICES_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        labs: Dict[str, Dict] = {}
        for sensor in data.get("sensors", []):
            lab_id = sensor["lab_id"]
            labs.setdefault(lab_id, {"sensors": [], "actuators": [], "temp": 26.0, "hum": 50.0})
            labs[lab_id]["sensors"].append(sensor)
        for actuator in data.get("actuators", []):
            lab_id = actuator["lab_id"]
            labs.setdefault(lab_id, {"sensors": [], "actuators": [], "temp": 26.0, "hum": 50.0})
            labs[lab_id]["actuators"].append(actuator)
        with self._lock:
            for lab_id, lab in labs.items():
                existing = self._labs.get(lab_id)
                if existing:
                    lab["temp"] = existing.get("temp", 26.0)
                    lab["hum"] = existing.get("hum", 50.0)
                    states = existing.get("actuator_state", {})
                else:
                    lab["temp"] = random.uniform(25.0, 27.0)
                    lab["hum"] = random.uniform(45.0, 55.0)
                    states = {}
                actuator_state = {}
                for actuator in lab["actuators"]:
                    aid = actuator["actuator_id"]
                    actuator_state[aid] = states.get(aid, "OFF")
                lab["actuator_state"] = actuator_state
            self._labs = labs
        self._devices_mtime = mtime
        logger.info("Loaded catalog for %d labs", len(self._labs))

    # ------------------------------------------------------------------ actuator callback
    def _on_actuator_command(self, topic: str, payload: dict):
        parts = topic.split("/")
        if len(parts) < 5:
            return
        lab_id, actuator_id = parts[1], parts[3]
        action = str(payload.get("action", "OFF")).upper()
        ts = int(payload.get("ts", time.time()))
        with self._lock:
            lab = self._labs.get(lab_id)
            if not lab:
                return
            lab.setdefault("actuator_state", {})[actuator_id] = action
        state_topic = f"labs/{lab_id}/actuators/{actuator_id}/state"
        state_payload = {"state": action, "ts": ts, "actuator_id": actuator_id}
        self._mqtt.publish_json(state_topic, state_payload, retain=True)
        logger.info("Actuator %s -> %s", actuator_id, action)

    # ------------------------------------------------------------------ simulation loop
    def run_forever(self):
        while not self._stop.is_set():
            self._reload_devices()
            now = int(time.time())
            with self._lock:
                labs_snapshot = json.loads(json.dumps(self._labs))
            for lab_id, lab in labs_snapshot.items():
                temp = lab.get("temp", 26.0)
                hum = lab.get("hum", 50.0)
                states = lab.get("actuator_state", {})

                # Apply actuator effects
                fan_on = any(
                    action == "ON" and actuator.get("type") == "fan"
                    for actuator, action in self._iter_states(lab, states)
                )
                if fan_on:
                    temp -= 0.3
                    hum -= 0.5
                for act, action in self._iter_states(lab, states):
                    if action != "ON":
                        continue
                    atype = act.get("type")
                    if atype == "heater":
                        temp += 0.5
                    elif atype == "humidifier":
                        hum += 1.0
                    elif atype == "dehumidifier":
                        hum -= 1.2

                # Occasionally push humidity upward so dehumidifier has to work
                if random.random() < 0.1:
                    hum += random.uniform(2.0, 6.0)

                temp += random.uniform(-0.2, 0.2)
                hum += random.uniform(-0.5, 0.5)
                temp = max(18.0, min(35.0, temp))
                hum = max(20.0, min(90.0, hum))

                with self._lock:
                    if lab_id in self._labs:
                        self._labs[lab_id]["temp"] = temp
                        self._labs[lab_id]["hum"] = hum

                for sensor in lab.get("sensors", []):
                    topic = f"labs/{lab_id}/sensors/{sensor['sensor_id']}/state"
                    if sensor.get("type") == "temp":
                        t_val = temp + random.uniform(-0.3, 0.3)
                        h_val = hum + random.uniform(-0.2, 0.2)
                    elif sensor.get("type") == "hum":
                        t_val = temp + random.uniform(-0.2, 0.2)
                        h_val = hum + random.uniform(-0.6, 0.6)
                    else:
                        t_val = temp + random.uniform(-0.3, 0.3)
                        h_val = hum + random.uniform(-0.6, 0.6)
                    payload = {"t": t_val, "h": h_val, "ts": now, "sensor_id": sensor["sensor_id"]}
                    self._mqtt.publish_json(topic, payload, retain=True)
                    logger.info("Published sensor %s lab=%s t=%.2f h=%.2f", sensor["sensor_id"], lab_id, t_val, h_val)

            sleep_for = max(1.0, self.loop_sec + random.uniform(-1.0, 1.0))
            if self._stop.wait(sleep_for):
                break

    def _iter_states(self, lab: Dict, states: Dict[str, str]):
        for actuator in lab.get("actuators", []):
            aid = actuator["actuator_id"]
            yield actuator, states.get(aid, "OFF")

    def stop(self):
        self._stop.set()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    mqtt_host = os.getenv("MQTT_HOST", "localhost")
    mqtt_port = int(os.getenv("MQTT_PORT", "1883"))
    loop_sec = int(os.getenv("SIM_LOOP_SEC", "5"))
    simulator = LabSimulator(mqtt_host, mqtt_port, loop_sec=loop_sec)
    simulator.start()
    simulator.run_forever()


if __name__ == "__main__":
    main()
