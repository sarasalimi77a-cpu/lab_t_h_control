from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Dict

import requests


logger = logging.getLogger("ThingSpeakAdaptor")

DEFAULT_CONFIG = {"channels": []}


def load_config(path: str) -> dict:
    if not os.path.exists(path):
        logger.warning("ThingSpeak config not found at %s; using empty config", path)
        return DEFAULT_CONFIG
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def build_sensor_lookup(status: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    lookup = {}
    for lab in status.get("labs", []):
        for sensor in lab.get("sensors", []):
            reading = sensor.get("reading") or {}
            lookup[sensor.get("sensor_id")] = {
                "t": reading.get("t"),
                "h": reading.get("h"),
                "ts": reading.get("ts"),
                "lab_id": sensor.get("lab_id"),
            }
    return lookup


def _extract_key(raw: str) -> str:
    if raw.startswith("http"):
        parsed = urllib.parse.urlparse(raw)
        qs = urllib.parse.parse_qs(parsed.query)
        key = qs.get("api_key", [""])[0]
        return key
    return raw


def post_field(api_key: str, field_num: int, value: Any, base_url: str) -> None:
    if value is None:
        return
    clean_key = _extract_key(api_key)
    payload = {"api_key": clean_key, f"field{field_num}": value}
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(base_url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8")
    logger.info("ThingSpeak field%s update response=%s", field_num, body)


def run_once(api_url: str, config: dict, base_url: str) -> None:
    try:
        resp = requests.get(f"{api_url.rstrip('/')}/status", timeout=10)
        resp.raise_for_status()
        status = resp.json()
    except Exception as exc:
        logger.error("Failed to fetch status from registry: %s", exc)
        return
    lookup = build_sensor_lookup(status)
    for chan in config.get("channels", []):
        chan_key = chan.get("api_key")
        fields: Dict[str, Dict[str, Any]] = chan.get("fields", {})
        payload_fields: Dict[str, Any] = {}
        api_key = None
        for field_name, mapping in fields.items():
            try:
                field_num = int(field_name.replace("field", ""))
            except ValueError:
                logger.warning("Invalid field name %s in ThingSpeak config", field_name)
                continue
            api_key = mapping.get("api_key") or chan_key
            sensor_id = mapping.get("sensor_id")
            metric = mapping.get("metric", "t")
            if not api_key or not sensor_id:
                continue
            sensor_reading = lookup.get(sensor_id)
            if not sensor_reading:
                logger.debug("No reading for sensor %s", sensor_id)
                continue
            value = sensor_reading.get(metric)
            if value is None:
                continue
            try:
                value = round(float(value), 2)
            except Exception:
                continue
            payload_fields[f"field{field_num}"] = value
        if api_key and payload_fields:
            clean_key = _extract_key(api_key)
            payload_fields["api_key"] = clean_key
            data = urllib.parse.urlencode(payload_fields).encode("utf-8")
            req = urllib.request.Request(base_url, data=data, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    body = resp.read().decode("utf-8")
                logger.info("ThingSpeak channel %s update response=%s", chan.get("name"), body)
            except Exception as exc:
                logger.error("Failed to post channel %s: %s", chan.get("name"), exc)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    api_url = os.getenv("REGISTRY_API_URL", "http://localhost:8080")
    config_path = os.getenv("THINGSPEAK_KEYS_PATH", "./ThingSpeak/keys.json")
    poll_sec = int(os.getenv("THINGSPEAK_POLL_SEC", "60"))
    config = load_config(config_path)
    base_url = os.getenv("THINGSPEAK_UPDATE_URL", "https://api.thingspeak.com/update")
    logger.info("ThingSpeak adaptor running poll=%ss config=%s", poll_sec, config_path)
    while True:
        run_once(api_url, config, base_url)
        time.sleep(poll_sec)


if __name__ == "__main__":
    main()
