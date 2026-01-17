# check if required keys are present and validate field formats

def require_keys(obj, keys):
    missing = [k for k in keys if k not in obj]
    return missing

def is_snake_id(s):
    if not isinstance(s, str) or not s:
        return False
    return all(c.islower() or c.isdigit() or c == "_" for c in s)

def validate_lab(payload):
    miss = require_keys(payload, ["lab_id", "name"])
    if miss:
        return f"missing fields: {', '.join(miss)}"
    if not is_snake_id(payload["lab_id"]):
        return "lab_id must be snake_case (e.g., lab1, lab_chem)"
    return None

def validate_sensor(payload):
    miss = require_keys(payload, ["sensor_id", "lab_id", "type"])
    if miss:
        return f"missing fields: {', '.join(miss)}"
    if not is_snake_id(payload["sensor_id"]):
        return "sensor_id must be snake_case (e.g., lab1_temp_1)"
    if not is_snake_id(payload["lab_id"]):
        return "lab_id must be snake_case"
    # 'type' for sensors can be 'temp', 'hum', or a specific model; keep it open/simple
    return None

def validate_actuator(payload):
    miss = require_keys(payload, ["actuator_id", "lab_id", "type"])
    if miss:
        return f"missing fields: {', '.join(miss)}"
    if not is_snake_id(payload["actuator_id"]):
        return "actuator_id must be snake_case (e.g., lab1_fan_1)"
    if not is_snake_id(payload["lab_id"]):
        return "lab_id must be snake_case"
    if payload["type"] not in ("fan", "humidifier", "dehumidifier", "heater"):
        return "type must be 'fan', 'humidifier', 'dehumidifier', or 'heater'"
    return None

def validate_thresholds_patch(patch):
    allowed = {"t_high", "t_low", "h_high", "h_low", "off_delay_sec", "hysteresis"}
    for k, v in patch.items():
        if k not in allowed:
            return f"unknown field: {k}"
        if not isinstance(v, (int, float)):
            return f"{k} must be number"
    return None

def validate_command(payload):
    miss = require_keys(payload, ["lab_id", "actuator_id", "action"])
    if miss:
        return f"missing fields: {', '.join(miss)}"
    if not is_snake_id(payload["lab_id"]):
        return "lab_id must be snake_case"
    if not is_snake_id(payload["actuator_id"]):
        return "actuator_id must be snake_case"
    action = str(payload["action"]).upper()
    if action not in ("ON", "OFF"):
        return "action must be either 'ON' or 'OFF'"
    return None
