"""Thin wrapper around paho-mqtt to simplify JSON publish/subscribe with topic wildcard support."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, List

import paho.mqtt.client as mqtt


logger = logging.getLogger(__name__)
_dropped_messages = 0


JsonCallback = Callable[[str, dict], None]


@dataclass
class _Subscription:
    topic: str
    callback: JsonCallback


class MqttClient:
    """MQTT helper that handles auto-reconnects, wildcard subscriptions and JSON payloads."""

    def __init__(self, client_id: str, host: str = "localhost", port: int = 1883, keepalive: int = 60):
        self.client = mqtt.Client(client_id=client_id, clean_session=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.host, self.port, self.keepalive = host, port, keepalive
        self._subs: List[_Subscription] = []
        self._lock = threading.Lock()
        self._connected = threading.Event()

    # --------------------------------------------------------------------- #
    # MQTT event handlers
    # --------------------------------------------------------------------- #
    def _on_connect(self, *_):
        self._connected.set()
        # resubscribe on reconnect
        with self._lock:
            for sub in self._subs:
                self.client.subscribe(sub.topic, qos=1)

    def _on_disconnect(self, *_):
        self._connected.clear()
        # auto-reconnect loop
        while not self._connected.is_set():
            try:
                time.sleep(2)
                self.client.reconnect()
                logger.info("Reconnected to MQTT broker at %s:%s", self.host, self.port)
            except Exception:
                time.sleep(2)

    def _on_message(self, _client, _userdata, msg):
        callbacks: List[JsonCallback] = []
        with self._lock:
            for sub in self._subs:
                if mqtt.topic_matches_sub(sub.topic, msg.topic):
                    callbacks.append(sub.callback)
        if not callbacks:
            return
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            payload = {}
            global _dropped_messages
            _dropped_messages += 1
            logger.warning("MQTT dropped malformed payloads=%s topic=%s", _dropped_messages, msg.topic)
            return
        logger.debug("MQTT message topic=%s matched_callbacks=%s", msg.topic, len(callbacks))
        for cb in callbacks:
            try:
                cb(msg.topic, payload)
            except Exception as exc:
                logger.exception("MQTT callback error for topic %s: %s", msg.topic, exc)

    # ------------------------------------------------------------------ API
    def connect(self):
        self.client.reconnect_delay_set(min_delay=2, max_delay=30)
        self.client.loop_start()
        try:
            # connect_async avoids raising when broker is temporarily unavailable
            connect_async = getattr(self.client, "connect_async", None)
            if connect_async:
                connect_async(self.host, self.port, self.keepalive)
            else:
                self.client.connect(self.host, self.port, self.keepalive)
        except Exception as exc:
            logger.warning("Initial MQTT connection failed: %s", exc)
        else:
            logger.info("Connecting to MQTT broker at %s:%s", self.host, self.port)

    def subscribe(self, topic: str, callback: JsonCallback):
        """Subscribe to a topic pattern (`+`/`#` supported) with a JSON callback."""
        with self._lock:
            self._subs.append(_Subscription(topic=topic, callback=callback))
        self.client.subscribe(topic, qos=1)
        logger.info("Subscribed to %s", topic)

    def publish_json(self, topic: str, obj: dict, retain: bool = True):
        payload = json.dumps(obj)
        logger.info("Publishing to %s payload=%s", topic, payload)
        self.client.publish(topic, payload, qos=1, retain=retain)

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()
