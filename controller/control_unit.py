# controller/control_unit.py

from __future__ import annotations

import threading
import time
import logging
from typing import Dict

from Device_connectors import actuator_bridge as ab
from controller import rules, state_memory as sm


class ControlUnit:
    """Continuously evaluates rules for a lab and publishes actuator commands."""

    def __init__(self, lab_id: str, mqtt_client, thresholds: Dict[str, float], loop_sec: int = 2):
        self.logger = logging.getLogger(f"ControlUnit[{lab_id}]")
        self.lab_id = lab_id
        self.mqtt = mqtt_client
        self.loop_sec = loop_sec
        self._thresholds = dict(thresholds)
        self._stop = threading.Event()
        sm.set_thresholds(lab_id, self._thresholds)
        self.logger.info("Control unit ready loop_sec=%s", loop_sec)

    # ------------------------------------------------------------------ lifecycle
    def loop_forever(self):
        while not self._stop.is_set():
            snapshot = sm.get_lab(self.lab_id)
            if snapshot:
                thresholds = snapshot.get("thresholds", self._thresholds)
                cmds = rules.decide(self.lab_id, snapshot, thresholds)
                if cmds:
                    for cmd in cmds:
                        self.logger.info("Rule decision actuator=%s action=%s", cmd["actuator_id"], cmd["action"])
                        ab.send_command(
                            self.mqtt,
                            self.lab_id,
                            cmd["actuator_id"],
                            cmd["action"],
                            source="rules",
                        )
                self._thresholds = dict(thresholds)
            self._stop.wait(self.loop_sec)
        self.logger.info("Control unit stopped")

    def stop(self):
        self._stop.set()

    # ---------------------------------------------------------------- updates
    def update_thresholds(self, thresholds: Dict[str, float]):
        """Refresh thresholds used by the control loop."""
        self._thresholds = dict(thresholds)
        sm.set_thresholds(self.lab_id, self._thresholds)
        self.logger.info("Thresholds updated %s", self._thresholds)
