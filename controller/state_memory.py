# controller/state_memory.py
from __future__ import annotations
import threading, time, copy, logging
from typing import Dict, Any, List, Tuple

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_state: Dict[str, Dict[str, Any]] = {}
_history: Dict[str, Dict[str, List[Tuple[float, float]]]] = {}
_thresholds: Dict[str, Dict[str, Any]] = {}

def init_labs(lab_ids: list[str]) -> None:
    with _lock:
        for lab_id in lab_ids:
            if lab_id not in _state:
                _state[lab_id] = {
                    "sensors": {},          # sensor_id -> {t,h,ts}
                    "actuators": {},        # actuator_id -> {state,ts}
                    "last_sensor_seen": 0,
                    "alerts": {"sensor_offline": False},
                }

def set_thresholds(lab_id: str, thresholds: dict) -> None:
    with _lock:
        _thresholds[lab_id] = thresholds

def update_sensor(lab_id: str, sensor_id: str, t: float, h: float, ts: int) -> None:
    with _lock:
        if lab_id not in _state:
            init_labs([lab_id])
        hist = _history.setdefault(lab_id, {}).setdefault(sensor_id, [])
        hist.append((float(t), float(h)))
        if len(hist) > 3:
            hist.pop(0)
        avg_t = sum(x[0] for x in hist) / len(hist)
        avg_h = sum(x[1] for x in hist) / len(hist)
        _state[lab_id]["sensors"][sensor_id] = {"t": float(t), "h": float(h), "ts": int(ts)}
        _state[lab_id]["sensors"][sensor_id]["avg_t"] = avg_t
        _state[lab_id]["sensors"][sensor_id]["avg_h"] = avg_h
        _state[lab_id]["last_sensor_seen"] = int(ts)
        _state[lab_id]["alerts"]["sensor_offline"] = False
        logger.info("Sensor update lab=%s sensor=%s t=%.2f h=%.2f ts=%s", lab_id, sensor_id, t, h, ts)

def update_actuator_state(lab_id: str, actuator_id: str, state: str, ts: int) -> None:
    with _lock:
        if lab_id not in _state:
            init_labs([lab_id])
        _state[lab_id]["actuators"][actuator_id] = {"state": state, "ts": int(ts)}
        logger.info("Actuator feedback lab=%s actuator=%s state=%s ts=%s", lab_id, actuator_id, state, ts)

def get_lab(lab_id: str) -> dict:
    with _lock:
        out = copy.deepcopy(_state.get(lab_id, {}))
        if lab_id in _thresholds:
            out["thresholds"] = copy.deepcopy(_thresholds[lab_id])
        return out

def get_snapshot() -> dict:
    with _lock:
        snap = copy.deepcopy(_state)
        # attach thresholds view
        for lab_id in snap.keys():
            if lab_id in _thresholds:
                snap[lab_id]["thresholds"] = copy.deepcopy(_thresholds[lab_id])
        return snap

def stale_state(lab_id: str, max_age: int = 30) -> bool:
    """Return True if last_sensor_seen is older than max_age seconds."""
    with _lock:
        lab = _state.get(lab_id)
        if not lab:
            return True
        last = lab.get("last_sensor_seen", 0)
        return (int(time.time()) - last) > max_age

def run_watchdog(publish_interval_sec: int = 30) -> threading.Thread:
    def _loop():
        while True:
            now = int(time.time())
            with _lock:
                for lab_id, lab in _state.items():
                    last = lab.get("last_sensor_seen", 0)
                    offline = (now - last) > 2 * publish_interval_sec
                    lab["alerts"]["sensor_offline"] = offline
                    if offline:
                        logger.warning("Sensor offline detected for lab=%s last_seen=%s now=%s", lab_id, last, now)
            time.sleep(publish_interval_sec)
    th = threading.Thread(target=_loop, daemon=True)
    th.start()
    return th
