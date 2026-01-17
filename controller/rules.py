"""Automation rules turning sensor snapshots into actuator commands."""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from typing import Dict, List, Tuple

import logging


logger = logging.getLogger(__name__)

_DEFAULT_DEVICE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "catalog", "devices.json")
)

# lab_id -> actuator_type -> [actuator_id]
_actuator_index: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
_device_path: str | None = None


def load_device_catalog(path: str | None = None) -> None:
    """Load devices.json and build quick lookup tables for actuators."""
    global _actuator_index, _device_path
    catalog_path = path or _DEFAULT_DEVICE_PATH
    if not os.path.exists(catalog_path):
        logger.warning("devices catalog missing at %s", catalog_path)
        _actuator_index = defaultdict(lambda: defaultdict(list))
        _device_path = catalog_path
        return

    with open(catalog_path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    idx: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
    for act in raw.get("actuators", []):
        try:
            lab_id = act["lab_id"]
            act_type = act["type"]
            act_id = act["actuator_id"]
        except KeyError as exc:
            logger.error("Invalid actuator entry in catalog: %s (missing %s)", act, exc)
            continue
        idx[lab_id][act_type].append(act_id)

    _actuator_index = idx
    _device_path = catalog_path
    total = 0
    for type_map in idx.values():
        for actuators in type_map.values():
            total += len(actuators)
    logger.info("Loaded %d actuators from %s", total, catalog_path)


def _actuators_of_type(lab_id: str, act_type: str) -> List[str]:
    if not _actuator_index and _device_path is None:
        load_device_catalog()
    return list(_actuator_index.get(lab_id, {}).get(act_type, []))


def _act_state(lab_snapshot: dict, actuator_id: str) -> str:
    return lab_snapshot.get("actuators", {}).get(actuator_id, {}).get("state", "OFF")


def _act_timestamp(lab_snapshot: dict, actuator_id: str) -> int:
    return lab_snapshot.get("actuators", {}).get(actuator_id, {}).get("ts", 0)


def _latest_sensor_reading(lab_snapshot: dict) -> Tuple[float, float, int]:
    sensors = lab_snapshot.get("sensors", {})
    if not sensors:
        return 0.0, 0.0, 0
    latest = max(sensors.values(), key=lambda entry: entry.get("ts", 0))
    t_val = latest.get("avg_t", latest.get("t", 0.0))
    h_val = latest.get("avg_h", latest.get("h", 0.0))
    return float(t_val), float(h_val), int(latest.get("ts", 0))


def decide(lab_id: str, lab_snapshot: dict, thresholds: dict) -> List[dict]:
    """Return a list of actuator commands to keep the environment within thresholds."""
    if not lab_snapshot.get("sensors"):
        return []

    t, h, _ = _latest_sensor_reading(lab_snapshot)
    th = thresholds
    cmds: List[dict] = []

    off_delay = float(th.get("off_delay_sec", 0))
    hysteresis = float(th.get("hysteresis", 0))
    heat_needed = t < th["t_low"]

    # Fan with hysteresis around high thresholds; heater has priority
    for actuator_id in _actuators_of_type(lab_id, "fan"):
        current_state = _act_state(lab_snapshot, actuator_id)
        should_force_on = (t > th["t_high"] or h > th["h_high"]) and not heat_needed
        # turn off when both temp and humidity are comfortably below the high thresholds minus hysteresis
        should_allow_off = (t < (th["t_high"] - hysteresis)) and (h < (th["h_high"] - hysteresis))
        if heat_needed and current_state == "ON":
            cmds.append({"actuator_id": actuator_id, "action": "OFF"})
        elif should_force_on and current_state != "ON":
            cmds.append({"actuator_id": actuator_id, "action": "ON"})
        elif should_allow_off and current_state == "ON":
            last_ts = _act_timestamp(lab_snapshot, actuator_id)
            if last_ts and (time.time() - last_ts) >= off_delay:
                cmds.append({"actuator_id": actuator_id, "action": "OFF"})

    # Dehumidifier
    for actuator_id in _actuators_of_type(lab_id, "dehumidifier"):
        current_state = _act_state(lab_snapshot, actuator_id)
        if h > th["h_high"]:
            if current_state != "ON":
                cmds.append({"actuator_id": actuator_id, "action": "ON"})
        elif h < th["h_high"] - hysteresis:
            if current_state != "OFF":
                cmds.append({"actuator_id": actuator_id, "action": "OFF"})

    # Humidifier
    for actuator_id in _actuators_of_type(lab_id, "humidifier"):
        current_state = _act_state(lab_snapshot, actuator_id)
        if h < th["h_low"]:
            if current_state != "ON":
                cmds.append({"actuator_id": actuator_id, "action": "ON"})
        elif h > th["h_low"] + hysteresis:
            if current_state != "OFF":
                cmds.append({"actuator_id": actuator_id, "action": "OFF"})

    # Heater
    for actuator_id in _actuators_of_type(lab_id, "heater"):
        current_state = _act_state(lab_snapshot, actuator_id)
        if t < th["t_low"]:
            if current_state != "ON":
                cmds.append({"actuator_id": actuator_id, "action": "ON"})
        elif t > th["t_low"] + hysteresis:
            if current_state != "OFF":
                cmds.append({"actuator_id": actuator_id, "action": "OFF"})

    return cmds
