# Device_connectors/sensor_bridge.py

from __future__ import annotations

import logging
import re
import time

from controller import state_memory as sm


logger = logging.getLogger(__name__)

SENSOR_RE = re.compile(r"^labs/([^/]+)/sensors/([^/]+)/state$")


def on_sensor_message(topic: str, payload: dict):
    match = SENSOR_RE.match(topic)
    if not match:
        return
    lab_id, sensor_id = match.group(1), match.group(2)
    try:
        temperature = float(payload.get("t"))
    except (TypeError, ValueError):
        temperature = 0.0
    try:
        humidity = float(payload.get("h"))
    except (TypeError, ValueError):
        humidity = 0.0
    timestamp = int(payload.get("ts") or time.time())
    sm.update_sensor(lab_id, sensor_id, temperature, humidity, timestamp)
    logger.debug(
        "Sensor update lab=%s sensor=%s t=%.2f h=%.2f ts=%s",
        lab_id,
        sensor_id,
        temperature,
        humidity,
        timestamp,
    )


def wire(mqtt_client):
    mqtt_client.subscribe("labs/+/sensors/+/state", on_sensor_message)
    logger.info("Sensor bridge listening on labs/+/sensors/+/state")
