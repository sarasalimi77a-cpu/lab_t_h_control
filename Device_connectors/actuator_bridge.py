# Device_connectors/actuator_bridge.py

from __future__ import annotations

import logging
import re
import time

from controller import state_memory as sm


logger = logging.getLogger(__name__)

ACT_STATE_RE = re.compile(r"^labs/([^/]+)/actuators/([^/]+)/state$")


def on_actuator_state(topic: str, payload: dict):
    match = ACT_STATE_RE.match(topic)
    if not match:
        return
    lab_id, actuator_id = match.group(1), match.group(2)
    state = str(payload.get("state", "OFF")).upper()
    timestamp = int(payload.get("ts", int(time.time())))
    sm.update_actuator_state(lab_id, actuator_id, state, timestamp)
    logger.debug("Actuator feedback lab=%s actuator=%s state=%s ts=%s", lab_id, actuator_id, state, timestamp)


def send_command(mqtt_client, lab_id: str, actuator_id: str, action: str, source: str = "rules"):
    topic = f"labs/{lab_id}/actuators/{actuator_id}/cmd"
    payload = {"action": action.upper(), "source": source, "ts": int(time.time())}
    logger.info("Command -> topic=%s payload=%s", topic, payload)
    mqtt_client.publish_json(topic, payload, retain=True)


def wire(mqtt_client):
    mqtt_client.subscribe("labs/+/actuators/+/state", on_actuator_state)
    logger.info("Actuator bridge listening on labs/+/actuators/+/state")
