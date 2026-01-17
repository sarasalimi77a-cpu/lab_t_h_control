# controller/cu_instancer.py

# controller/cu_instancer.py

from __future__ import annotations

import json
import os
import threading
import logging
import time
from typing import Dict, List, Optional

from Device_connectors import actuator_bridge, sensor_bridge
from Device_connectors.mqtt_client import MqttClient
from controller import rules, state_memory as sm
from controller.control_unit import ControlUnit
from logging_setup import configure_logging

configure_logging()


_BASE_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_CATALOG_DIR = os.path.join(_BASE_DIR, "catalog")
_LABS_PATH = os.path.join(_CATALOG_DIR, "labs.json")
_THRESHOLDS_PATH = os.path.join(_CATALOG_DIR, "thresholds.json")

DEFAULT_THRESHOLDS = {
    "t_high": 28.0,
    "t_low": 26.5,
    "h_high": 70.0,
    "h_low": 40.0,
    "off_delay_sec": 60.0,
    "hysteresis": 2.0,
}


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_catalog_labs(path: str = _LABS_PATH) -> List[dict]:
    data = _read_json(path)
    return list(data.get("labs", []))


def load_thresholds(path: str = _THRESHOLDS_PATH) -> Dict[str, dict]:
    data = _read_json(path)
    per_lab = data.get("per_lab", {})
    default = data.get("default") or DEFAULT_THRESHOLDS
    out = {}
    for lab_id, overrides in per_lab.items():
        merged = dict(DEFAULT_THRESHOLDS)
        merged.update(default)
        merged.update(overrides)
        out[lab_id] = merged
    out["_default"] = dict(DEFAULT_THRESHOLDS)
    out["_default"].update(default)
    return out


class ControllerManager:
    """Manage MQTT connection, state memory, and per-lab control units."""

    def __init__(self, mqtt_host: str = "localhost", mqtt_port: int = 1883, loop_sec: int = 2):
        self.logger = logging.getLogger("ControllerManager")
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.loop_sec = loop_sec
        self._mqtt: Optional[MqttClient] = None
        self._control_units: Dict[str, ControlUnit] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._watchdog_thread = None
        self._started = False
        self._catalog_thread: Optional[threading.Thread] = None
        self._watch_interval = int(os.getenv("CATALOG_REFRESH_SEC", "10"))
        self._sim_thread: Optional[threading.Thread] = None
        self._simulator = None

    # ------------------------------------------------------------------ startup
    def start(self):
        if self._started:
            return

        self.logger.info("Starting controller manager mqtt=%s:%s", self.mqtt_host, self.mqtt_port)
        labs = load_catalog_labs()
        lab_ids = [lab["lab_id"] for lab in labs]
        sm.init_labs(lab_ids)
        thresholds_map = load_thresholds()
        for lab_id in lab_ids:
            sm.set_thresholds(lab_id, self._resolve_thresholds(lab_id, thresholds_map))

        self._watchdog_thread = sm.run_watchdog(publish_interval_sec=30)

        self._mqtt = MqttClient(client_id="controller_hub", host=self.mqtt_host, port=self.mqtt_port)
        self._mqtt.connect()
        sensor_bridge.wire(self._mqtt)
        actuator_bridge.wire(self._mqtt)
        rules.load_device_catalog(os.path.join(_CATALOG_DIR, "devices.json"))
        self.logger.info("MQTT connected and bridges wired")

        for lab_id in lab_ids:
            self.ensure_lab(lab_id, thresholds_map.get(lab_id))

        self._started = True
        self.logger.info("Controller manager started with labs=%s", lab_ids)
        self._start_catalog_watcher()
        self._start_simulator_if_enabled()

    # ------------------------------------------------------------------ helpers
    def _resolve_thresholds(self, lab_id: str, thresholds_map: Optional[Dict[str, dict]] = None) -> dict:
        thresholds_map = thresholds_map or load_thresholds()
        if lab_id in thresholds_map:
            return dict(thresholds_map[lab_id])
        return dict(thresholds_map.get("_default", DEFAULT_THRESHOLDS))

    def ensure_lab(self, lab_id: str, thresholds: Optional[dict] = None):
        """Ensure a control unit exists for lab (create if missing)."""
        thresholds = thresholds or self._resolve_thresholds(lab_id)
        with self._lock:
            if lab_id in self._control_units:
                self.update_thresholds(lab_id, thresholds)
                return
            if self._mqtt is None:
                raise RuntimeError("ControllerManager.start() must be called first")
            sm.init_labs([lab_id])
            sm.set_thresholds(lab_id, thresholds)
            control = ControlUnit(lab_id, self._mqtt, thresholds, loop_sec=self.loop_sec)
            thread = threading.Thread(target=control.loop_forever, name=f"control_unit_{lab_id}", daemon=True)
            thread.start()
            self._control_units[lab_id] = control
            self._threads[lab_id] = thread
            self.logger.info("Control unit launched lab=%s", lab_id)

    def remove_lab(self, lab_id: str):
        with self._lock:
            control = self._control_units.pop(lab_id, None)
            thread = self._threads.pop(lab_id, None)
        if control:
            control.stop()
        if thread:
            thread.join(timeout=1)
        self.logger.info("Control unit removed lab=%s", lab_id)

    def reload_devices(self):
        rules.load_device_catalog(os.path.join(_CATALOG_DIR, "devices.json"))

    def send_command(self, lab_id: str, actuator_id: str, action: str, source: str = "manual"):
        if self._mqtt is None:
            raise RuntimeError("ControllerManager not started")
        actuator_bridge.send_command(self._mqtt, lab_id, actuator_id, action, source=source)

    def update_thresholds(self, lab_id: str, thresholds: dict):
        with self._lock:
            control = self._control_units.get(lab_id)
        current = sm.get_lab(lab_id).get("thresholds", {})
        if control and current != thresholds:
            control.update_thresholds(thresholds)
        sm.set_thresholds(lab_id, thresholds)

    def _start_catalog_watcher(self):
        if self._catalog_thread:
            return

        def _loop():
            while True:
                try:
                    labs = load_catalog_labs()
                    thresholds_map = load_thresholds()
                    wanted_ids = [lab["lab_id"] for lab in labs]
                    for lab_id in wanted_ids:
                        th = self._resolve_thresholds(lab_id, thresholds_map)
                        self.ensure_lab(lab_id, th)
                    for lab_id in list(self._control_units.keys()):
                        if lab_id not in wanted_ids:
                            self.remove_lab(lab_id)
                    for lab_id in list(self._control_units.keys()):
                        th = self._resolve_thresholds(lab_id, thresholds_map)
                        self.update_thresholds(lab_id, th)
                    rules.load_device_catalog(os.path.join(_CATALOG_DIR, "devices.json"))
                except Exception as exc:
                    self.logger.error("Catalog watcher error: %s", exc)
                time.sleep(self._watch_interval)

        self._catalog_thread = threading.Thread(target=_loop, name="catalog_watcher", daemon=True)
        self._catalog_thread.start()

    def _start_simulator_if_enabled(self):
        flag = os.getenv("MOCK_SENSORS", "0").lower()
        if flag not in ("1", "true", "yes"):
            return
        if self._sim_thread:
            return
        try:
            from simulators.lab_simulator import LabSimulator
        except Exception as exc:
            self.logger.error("Simulator import failed: %s", exc)
            return
        try:
            sim_loop = int(os.getenv("SIM_LOOP_SEC", "5"))
        except ValueError:
            sim_loop = 5
        simulator = LabSimulator(self.mqtt_host, self.mqtt_port, loop_sec=sim_loop)
        try:
            simulator.start()
        except Exception as exc:
            self.logger.error("Simulator start failed: %s", exc)
            return

        def _run():
            try:
                simulator.run_forever()
            except Exception as exc:
                self.logger.error("Simulator crashed: %s", exc)

        self._simulator = simulator
        self._sim_thread = threading.Thread(target=_run, name="lab_simulator", daemon=True)
        self._sim_thread.start()
        self.logger.info("Mock sensor simulator started (interval=%ss)", sim_loop)

    # ------------------------------------------------------------------ teardown
    def stop(self):
        with self._lock:
            controls = list(self._control_units.items())
            self._control_units.clear()
            threads = list(self._threads.items())
            self._threads.clear()
        for _, control in controls:
            control.stop()
        for _, thread in threads:
            thread.join(timeout=1)
        if self._mqtt:
            self._mqtt.disconnect()
            self._mqtt = None
        self._started = False
        self.logger.info("Controller manager stopped")
        if self._simulator and hasattr(self._simulator, "stop"):
            try:
                self._simulator.stop()
            except Exception:
                pass
        self._simulator = None
        self._sim_thread = None


_GLOBAL_MANAGER: Optional[ControllerManager] = None


def get_manager() -> ControllerManager:
    global _GLOBAL_MANAGER
    if _GLOBAL_MANAGER is None:
        host = os.getenv("MQTT_HOST", "localhost")
        port = int(os.getenv("MQTT_PORT", "1883"))
        loop_sec = int(os.getenv("CONTROL_LOOP_SEC", "2"))
        _GLOBAL_MANAGER = ControllerManager(mqtt_host=host, mqtt_port=port, loop_sec=loop_sec)
        _GLOBAL_MANAGER.start()
    return _GLOBAL_MANAGER


def start_system():
    """Legacy entry-point: start manager and block forever."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    manager = get_manager()
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        manager.stop()


if __name__ == "__main__":
    start_system()
