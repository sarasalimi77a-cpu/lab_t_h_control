"""CherryPy registry service exposing catalog CRUD plus live status data."""

from __future__ import annotations

import os
import threading
from datetime import datetime
from typing import Dict, List, Optional

import cherrypy
import logging
import requests

from catalog.catalog_store import bump_top_last_update, read_json, write_json
from catalog_registry.validators import (
    validate_actuator,
    validate_command,
    validate_lab,
    validate_sensor,
    validate_thresholds_patch,
)
from controller import state_memory as sm
from controller.cu_instancer import DEFAULT_THRESHOLDS, get_manager
from Device_connectors.mqtt_client import MqttClient
from Device_connectors import actuator_bridge
from logging_setup import configure_logging

configure_logging()


CATALOG_DIR = os.path.join(os.path.dirname(__file__), "..", "catalog")
LABS_PATH = os.path.join(CATALOG_DIR, "labs.json")
DEVICES_PATH = os.path.join(CATALOG_DIR, "devices.json")
THRESHOLDS_PATH = os.path.join(CATALOG_DIR, "thresholds.json")
PERMISSIONS_PATH = os.path.join(CATALOG_DIR, "permissions.json")
CONTROLLER_URL = os.getenv("CONTROLLER_URL", "http://controller:8081")
REGISTRY_ENABLE_CONTROLLER = os.getenv("REGISTRY_ENABLE_CONTROLLER", "1").lower() not in ("0", "false", "no")


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class RegistryAPI:
    exposed = True

    def __init__(self):
        self.logger = logging.getLogger("RegistryAPI")
        self._lock = threading.RLock()
        self.manager = None
        self._command_client: Optional[MqttClient] = None
        self._load_catalogs()
        self.controller_enabled = REGISTRY_ENABLE_CONTROLLER
        if self.controller_enabled:
            self.manager = get_manager()
        else:
            host = os.getenv("MQTT_HOST", "localhost")
            port = int(os.getenv("MQTT_PORT", "1884"))
            self._command_client = MqttClient(client_id="registry_publisher", host=host, port=port)
            self._command_client.connect()
            self.logger.info("Registry running without embedded controller; using controller at %s", CONTROLLER_URL)

    # ------------------------------------------------------------------ helpers
    def _load_catalogs(self):
        self.labs = read_json(LABS_PATH)
        self.devices = read_json(DEVICES_PATH)
        self.thresholds = read_json(THRESHOLDS_PATH)
        self.permissions = read_json(PERMISSIONS_PATH)
        self.devices.setdefault("sensors", [])
        self.devices.setdefault("actuators", [])
        self.thresholds.setdefault("per_lab", {})

    def _save_labs(self):
        write_json(LABS_PATH, self.labs)

    def _save_devices(self):
        write_json(DEVICES_PATH, self.devices, touch_ts_keys=["last_update"])
        if self.manager:
            self.manager.reload_devices()

    def _save_thresholds(self):
        write_json(THRESHOLDS_PATH, self.thresholds, touch_ts_keys=["last_update"])

    def _save_permissions(self):
        write_json(PERMISSIONS_PATH, self.permissions, touch_ts_keys=["last_update"])

    def _default_thresholds(self) -> Dict[str, float]:
        defaults = dict(DEFAULT_THRESHOLDS)
        defaults.update(self.thresholds.get("default", {}))
        return defaults

    def _threshold_for_lab(self, lab_id: str) -> Dict[str, float]:
        merged = self._default_thresholds()
        overrides = self.thresholds.get("per_lab", {}).get(lab_id)
        if overrides:
            merged.update(overrides)
        return merged

    def _ensure_threshold_entry(self, lab_id: str):
        per_lab = self.thresholds.setdefault("per_lab", {})
        if lab_id not in per_lab:
            per_lab[lab_id] = dict(self._default_thresholds())
            self._save_thresholds()

    def _find_lab(self, lab_id: str) -> Optional[dict]:
        return next((lab for lab in self.labs.get("labs", []) if lab["lab_id"] == lab_id), None)

    def _sensor_index(self, sensor_id: str) -> Optional[int]:
        for idx, sensor in enumerate(self.devices.get("sensors", [])):
            if sensor["sensor_id"] == sensor_id:
                return idx
        return None

    def _actuator_index(self, actuator_id: str) -> Optional[int]:
        for idx, actuator in enumerate(self.devices.get("actuators", [])):
            if actuator["actuator_id"] == actuator_id:
                return idx
        return None

    def _controller_snapshot(self) -> Optional[dict]:
        try:
            resp = requests.get(f"{CONTROLLER_URL.rstrip('/')}/snapshot", timeout=2)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                return data
        except Exception as exc:
            self.logger.warning("Controller snapshot fetch failed: %s", exc)
        return None

    def _status_payload(self) -> dict:
        if self.controller_enabled:
            snapshot = sm.get_snapshot()
        else:
            snapshot = self._controller_snapshot() or sm.get_snapshot()
        labs_payload: List[dict] = []
        for lab in self.labs.get("labs", []):
            lab_id = lab["lab_id"]
            lab_snapshot = snapshot.get(lab_id, {})
            lab_sensors = []
            for sensor in [s for s in self.devices.get("sensors", []) if s["lab_id"] == lab_id]:
                reading = lab_snapshot.get("sensors", {}).get(sensor["sensor_id"], {})
                lab_sensors.append(
                    {
                        "sensor_id": sensor["sensor_id"],
                        "type": sensor.get("type"),
                        "lab_id": lab_id,
                        "reading": reading or {},
                    }
                )
            lab_actuators = []
            for actuator in [a for a in self.devices.get("actuators", []) if a["lab_id"] == lab_id]:
                state = lab_snapshot.get("actuators", {}).get(actuator["actuator_id"], {})
                lab_actuators.append(
                    {
                        "actuator_id": actuator["actuator_id"],
                        "type": actuator.get("type"),
                        "lab_id": lab_id,
                        "state": state or {},
                    }
                )
            labs_payload.append(
                {
                    "lab_id": lab_id,
                    "name": lab.get("name", ""),
                    "notes": lab.get("notes", ""),
                    "thresholds": lab_snapshot.get("thresholds", self._threshold_for_lab(lab_id)),
                    "sensors": lab_sensors,
                    "actuators": lab_actuators,
                    "alerts": lab_snapshot.get("alerts", {}),
                    "last_sensor_seen": lab_snapshot.get("last_sensor_seen"),
                }
            )
        return {"labs": labs_payload, "ts": _ts()}

    # ------------------------------------------------------------------ handlers
    @cherrypy.tools.json_out()
    def OPTIONS(self, *uri, **params):
        cherrypy.response.status = 204
        return {}

    @cherrypy.tools.json_out()
    def GET(self, *uri, **params):
        if not uri:
            return {
                "ok": True,
                "endpoints": [
                    "/health",
                    "/labs",
                    "/lab/{lab_id}",
                    "/sensors",
                    "/sensor/{sensor_id}",
                    "/actuators",
                    "/actuator/{actuator_id}",
                    "/thresholds",
                    "/threshold/{lab_id}",
                    "/permissions",
                    "/status",
                ],
            }

        path = uri[0].lower()

        if path == "health":
            info = {"ok": True, "ts": _ts()}
            if not self.controller_enabled:
                try:
                    r = requests.get(f"{CONTROLLER_URL.rstrip('/')}/health", timeout=2)
                    info["controller_ok"] = r.ok
                except Exception:
                    info["controller_ok"] = False
            return info

        if path == "labs":
            return self.labs

        if path == "lab":
            if len(uri) < 2:
                return {"error": "lab_id missing"}
            lab = self._find_lab(uri[1])
            return lab or {"error": f"lab '{uri[1]}' not found"}

        if path == "sensors":
            lab_filter = params.get("lab_id")
            sensors = self.devices.get("sensors", [])
            if lab_filter:
                sensors = [s for s in sensors if s["lab_id"] == lab_filter]
            return sensors

        if path == "sensor":
            if len(uri) < 2:
                return {"error": "sensor_id missing"}
            idx = self._sensor_index(uri[1])
            return self.devices["sensors"][idx] if idx is not None else {"error": "not found"}

        if path == "actuators":
            lab_filter = params.get("lab_id")
            actuators = self.devices.get("actuators", [])
            if lab_filter:
                actuators = [a for a in actuators if a["lab_id"] == lab_filter]
            return actuators

        if path == "actuator":
            if len(uri) < 2:
                return {"error": "actuator_id missing"}
            idx = self._actuator_index(uri[1])
            return self.devices["actuators"][idx] if idx is not None else {"error": "not found"}

        if path == "thresholds":
            return self.thresholds

        if path == "threshold":
            if len(uri) < 2:
                return {"error": "lab_id missing"}
            lab_id = uri[1]
            if not self._find_lab(lab_id):
                return {"error": "lab not found"}
            return {"lab_id": lab_id, "thresholds": self._threshold_for_lab(lab_id)}

        if path == "permissions":
            return self.permissions

        if path == "status":
            return self._status_payload()

        return {"error": "invalid endpoint"}

    @cherrypy.tools.json_in()
    @cherrypy.tools.json_out()
    def POST(self, *uri, **_params):
        if not uri:
            return {"error": "use /labs, /sensors, /actuators, or /command"}

        path = uri[0].lower()
        body = cherrypy.request.json

        if path == "labs":
            err = validate_lab(body)
            if err:
                return {"error": err}
            with self._lock:
                if self._find_lab(body["lab_id"]):
                    return {"error": "lab_id already exists"}
                self.labs.setdefault("labs", []).append(
                    {"lab_id": body["lab_id"], "name": body["name"], "notes": body.get("notes", "")}
                )
                self.labs["last_update"] = _ts()
                self._save_labs()
                self._ensure_threshold_entry(body["lab_id"])
            self.logger.info("Lab created %s", body["lab_id"])
            if self.controller_enabled and self.manager:
                self.manager.ensure_lab(body["lab_id"], self._threshold_for_lab(body["lab_id"]))
            return {"ok": True, "msg": "lab created"}

        if path == "sensors":
            err = validate_sensor(body)
            if err:
                return {"error": err}
            with self._lock:
                if not self._find_lab(body["lab_id"]):
                    return {"error": "lab_id does not exist"}
                if self._sensor_index(body["sensor_id"]) is not None:
                    return {"error": "sensor_id already exists"}
                self.devices.setdefault("sensors", []).append(
                    {"sensor_id": body["sensor_id"], "lab_id": body["lab_id"], "type": body["type"]}
                )
                bump_top_last_update(self.devices)
                self._save_devices()
            self.logger.info("Sensor created %s lab=%s", body["sensor_id"], body["lab_id"])
            return {"ok": True, "msg": "sensor created"}

        if path == "actuators":
            err = validate_actuator(body)
            if err:
                return {"error": err}
            with self._lock:
                if not self._find_lab(body["lab_id"]):
                    return {"error": "lab_id does not exist"}
                if self._actuator_index(body["actuator_id"]) is not None:
                    return {"error": "actuator_id already exists"}
                self.devices.setdefault("actuators", []).append(
                    {"actuator_id": body["actuator_id"], "lab_id": body["lab_id"], "type": body["type"]}
                )
                bump_top_last_update(self.devices)
                self._save_devices()
            self.logger.info("Actuator created %s lab=%s type=%s", body["actuator_id"], body["lab_id"], body["type"])
            return {"ok": True, "msg": "actuator created"}

        if path == "command":
            err = validate_command(body)
            if err:
                return {"error": err}
            lab_id = body["lab_id"]
            if not self._find_lab(lab_id):
                return {"error": "lab not found"}
            with self._lock:
                idx = self._actuator_index(body["actuator_id"])
                if idx is None:
                    return {"error": "actuator_id not found"}
                actuator = self.devices["actuators"][idx]
                if actuator["lab_id"] != lab_id:
                    return {"error": "actuator not in specified lab"}
            if self.controller_enabled and self.manager:
                self.manager.send_command(
                    lab_id,
                    body["actuator_id"],
                    body["action"],
                    source=body.get("source", "manual"),
                )
            elif self._command_client:
                actuator_bridge.send_command(
                    self._command_client,
                    lab_id,
                    body["actuator_id"],
                    body["action"],
                    source=body.get("source", "manual"),
                )
            self.logger.info(
                "Manual command lab=%s actuator=%s action=%s source=%s",
                lab_id,
                body["actuator_id"],
                body["action"],
                body.get("source", "manual"),
            )
            return {"ok": True, "msg": "command dispatched"}

        return {"error": "invalid endpoint"}

    @cherrypy.tools.json_in()
    @cherrypy.tools.json_out()
    def PUT(self, *uri, **_params):
        if not uri:
            return {
                "error": "use /lab/{id}, /sensor/{id}, /actuator/{id}, /threshold/{lab_id}, or /permissions",
            }

        path = uri[0].lower()
        body = cherrypy.request.json

        if path == "lab":
            if len(uri) < 2:
                return {"error": "lab_id missing"}
            lab_id = uri[1]
            with self._lock:
                lab = self._find_lab(lab_id)
                if not lab:
                    return {"error": "lab not found"}
                if "name" in body:
                    lab["name"] = body["name"]
                if "notes" in body:
                    lab["notes"] = body["notes"]
                self.labs["last_update"] = _ts()
                self._save_labs()
            return {"ok": True, "msg": "lab updated"}

        if path == "sensor":
            if len(uri) < 2:
                return {"error": "sensor_id missing"}
            sensor_id = uri[1]
            with self._lock:
                idx = self._sensor_index(sensor_id)
                if idx is None:
                    return {"error": "sensor not found"}
                patch = {}
                if "lab_id" in body:
                    if not self._find_lab(body["lab_id"]):
                        return {"error": "new lab_id does not exist"}
                    patch["lab_id"] = body["lab_id"]
                if "type" in body:
                    patch["type"] = body["type"]
                self.devices["sensors"][idx].update(patch)
                bump_top_last_update(self.devices)
                self._save_devices()
            return {"ok": True, "msg": "sensor updated"}

        if path == "actuator":
            if len(uri) < 2:
                return {"error": "actuator_id missing"}
            actuator_id = uri[1]
            with self._lock:
                idx = self._actuator_index(actuator_id)
                if idx is None:
                    return {"error": "actuator not found"}
                patch = {}
                if "lab_id" in body:
                    if not self._find_lab(body["lab_id"]):
                        return {"error": "new lab_id does not exist"}
                    patch["lab_id"] = body["lab_id"]
                if "type" in body:
                    if body["type"] not in ("fan", "humidifier", "dehumidifier", "heater"):
                        return {"error": "invalid actuator type"}
                    patch["type"] = body["type"]
                self.devices["actuators"][idx].update(patch)
                bump_top_last_update(self.devices)
                self._save_devices()
            return {"ok": True, "msg": "actuator updated"}

        if path == "threshold":
            if len(uri) < 2:
                return {"error": "lab_id missing"}
            lab_id = uri[1]
            if not self._find_lab(lab_id):
                return {"error": "lab does not exist"}
            err = validate_thresholds_patch(body)
            if err:
                return {"error": err}
            with self._lock:
                entry = self.thresholds.setdefault("per_lab", {}).setdefault(lab_id, {})
                entry.update(body)
                self._save_thresholds()
                merged = self._threshold_for_lab(lab_id)
            if self.controller_enabled and self.manager:
                self.manager.update_thresholds(lab_id, merged)
            self.logger.info("Thresholds updated lab=%s patch=%s", lab_id, body)
            return {"ok": True, "msg": "thresholds updated", "lab_id": lab_id, "thresholds": merged}

        if path == "permissions":
            roles = body.get("roles")
            if not isinstance(roles, dict):
                return {"error": "roles must be an object"}
            with self._lock:
                self.permissions["roles"] = roles
                self._save_permissions()
            return {"ok": True, "msg": "permissions updated"}

        return {"error": "invalid endpoint"}

    @cherrypy.tools.json_out()
    def DELETE(self, *uri, **_params):
        if not uri:
            return {"error": "use /lab/{id}, /sensor/{id}, or /actuator/{id}"}

        path = uri[0].lower()

        if path == "lab":
            if len(uri) < 2:
                return {"error": "lab_id missing"}
            lab_id = uri[1]
            with self._lock:
                if any(s["lab_id"] == lab_id for s in self.devices.get("sensors", [])):
                    return {"error": "remove or move sensors first"}
                if any(a["lab_id"] == lab_id for a in self.devices.get("actuators", [])):
                    return {"error": "remove or move actuators first"}
                before = len(self.labs.get("labs", []))
                self.labs["labs"] = [lab for lab in self.labs.get("labs", []) if lab["lab_id"] != lab_id]
                if len(self.labs["labs"]) == before:
                    return {"error": "lab not found"}
                self.labs["last_update"] = _ts()
                self._save_labs()
                removed = self.thresholds.get("per_lab", {}).pop(lab_id, None)
                if removed is not None:
                    self._save_thresholds()
            self.logger.info("Lab deleted %s", lab_id)
            if self.controller_enabled and self.manager:
                self.manager.remove_lab(lab_id)
            return {"ok": True, "msg": "lab deleted"}

        if path == "sensor":
            if len(uri) < 2:
                return {"error": "sensor_id missing"}
            sensor_id = uri[1]
            with self._lock:
                idx = self._sensor_index(sensor_id)
                if idx is None:
                    return {"error": "sensor not found"}
                self.devices["sensors"].pop(idx)
                bump_top_last_update(self.devices)
                self._save_devices()
            self.logger.info("Sensor deleted %s", sensor_id)
            return {"ok": True, "msg": "sensor deleted"}

        if path == "actuator":
            if len(uri) < 2:
                return {"error": "actuator_id missing"}
            actuator_id = uri[1]
            with self._lock:
                idx = self._actuator_index(actuator_id)
                if idx is None:
                    return {"error": "actuator not found"}
                self.devices["actuators"].pop(idx)
                bump_top_last_update(self.devices)
                self._save_devices()
            self.logger.info("Actuator deleted %s", actuator_id)
            return {"ok": True, "msg": "actuator deleted"}

        return {"error": "invalid endpoint"}


def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    def _cors():
        cherrypy.response.headers["Access-Control-Allow-Origin"] = "*"
        cherrypy.response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        cherrypy.response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    cherrypy.tools.cors = cherrypy.Tool("before_handler", _cors)
    conf = {
        "/": {
            "request.dispatch": cherrypy.dispatch.MethodDispatcher(),
            "tools.sessions.on": True,
            "tools.cors.on": True,
        }
    }
    cherrypy.config.update({"server.socket_host": "0.0.0.0", "server.socket_port": 8080})
    cherrypy.tree.mount(RegistryAPI(), "/", conf)
    cherrypy.engine.start()
    cherrypy.engine.block()


if __name__ == "__main__":
    run()


