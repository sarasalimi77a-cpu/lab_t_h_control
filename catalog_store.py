# read and write JSON files with thread safety and timestamp management

import json
import os
import threading
from datetime import datetime

_FILE_LOCK = threading.Lock()

def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def read_json(path):
    with _FILE_LOCK:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

def write_json(path, data, touch_ts_keys=None):
    if touch_ts_keys:
        for k in touch_ts_keys:
            if isinstance(data, dict) and k in data:
                if isinstance(data[k], dict) and "last_update" in data[k]:
                    data[k]["last_update"] = _ts()
                if k == "last_update":
                    data["last_update"] = _ts()
    with _FILE_LOCK:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)

def bump_top_last_update(obj):
    if isinstance(obj, dict) and "last_update" in obj:
        obj["last_update"] = _ts()
