"""Simple Telegram bot using telepot and requests.

Set envs:
- TELEGRAM_BOT_TOKEN
- REGISTRY_API_URL (default http://localhost:8080)
- ALERT_POLL_SEC (default 30)
- ALERT_COOLDOWN_SEC (default 300)
"""

import os
import time
import shlex
import json
import requests
import telepot
from telepot.loop import MessageLoop
from telepot.namedtuple import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from datetime import datetime

REGISTRY_API = os.getenv("REGISTRY_API_URL", "http://localhost:8080")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
PERMISSIONS_PATH = os.getenv("PERMISSIONS_PATH", "./catalog/permissions.json")
ALERT_POLL_SEC = int(os.getenv("ALERT_POLL_SEC", "30"))
# Shorter cooldown so alerts repeat if problem persists
ALERT_COOLDOWN_SEC = int(os.getenv("ALERT_COOLDOWN_SEC", "120"))

KNOWN_CHATS = set()
_last_alert = {}


def load_permissions():
    try:
        with open(PERMISSIONS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        ids = set()
        for arr in (data.get("roles") or {}).values():
            try:
                ids.update(int(x) for x in arr)
            except Exception:
                continue
        return ids
    except Exception:
        return set()


def fmt_val(v):
    if isinstance(v, (int, float)):
        return f"{v:.2f}"
    return v if v is not None else "?"


def fmt_ts(ts):
    try:
        ts_int = int(ts)
        dt = datetime.fromtimestamp(ts_int)
        age = int(time.time()) - ts_int
        age_txt = f"{age}s ago" if age < 120 else f"{age//60}m ago"
        return f"{dt.strftime('%Y-%m-%d %H:%M:%S')} ({age_txt})"
    except Exception:
        return ts if ts is not None else "never"


def get_labs():
    return _get("labs").get("labs", [])


def _get(endpoint):
    url = f"{REGISTRY_API.rstrip('/')}/{endpoint.lstrip('/')}"
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {"error": "registry unreachable"}


def _post(endpoint, payload):
    url = f"{REGISTRY_API.rstrip('/')}/{endpoint.lstrip('/')}"
    try:
        r = requests.post(url, json=payload, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {"error": "registry unreachable"}


def fmt_status(data):
    labs = data.get("labs", [])
    if not labs:
        return "No labs registered."
    lines = []
    for lab in labs:
        lines.append(f"🏷 {lab.get('lab_id')} ({lab.get('name','')})")
        thr = lab.get("thresholds", {})
        lines.append(
            f"  🎯 Temp {thr.get('t_low','?')}..{thr.get('t_high','?')}  Hum {thr.get('h_low','?')}..{thr.get('h_high','?')}"
        )
        lines.append(f"  ⏱ Last sensor: {fmt_ts(lab.get('last_sensor_seen','never'))}")
        if lab.get("alerts", {}).get("sensor_offline"):
            lines.append("  ⚠️ Sensor offline")
        for s in lab.get("sensors", []):
            rd = s.get("reading") or {}
            lines.append(
                f"   🌡️ {s.get('sensor_id')}: T={fmt_val(rd.get('t'))}°C  H={fmt_val(rd.get('h'))}%"
            )
        for a in lab.get("actuators", []):
            st = a.get("state") or {}
            lines.append(
                f"   ⚙️ {a.get('actuator_id')} ({a.get('type','')}): {st.get('state','?')}"
            )
        lines.append("")
    return "\n".join(lines).strip()


def track_alert(lab_id, kind):
    _last_alert[(lab_id, kind)] = int(time.time())


def should_alert(lab_id, kind):
    last = _last_alert.get((lab_id, kind), 0)
    return (int(time.time()) - last) >= ALERT_COOLDOWN_SEC


def poll_alerts(bot):
    while True:
        if not KNOWN_CHATS:
            time.sleep(ALERT_POLL_SEC)
            continue
        data = _get("status")
        labs = data.get("labs", [])
        for lab in labs:
            lab_id = lab.get("lab_id")
            thr = lab.get("thresholds", {})
            for sensor in lab.get("sensors", []):
                rd = sensor.get("reading") or {}
                sid = sensor.get("sensor_id")
                t = rd.get("t")
                h = rd.get("h")
                allowed_ids = load_permissions()
                recipients = [c for c in KNOWN_CHATS if (not allowed_ids) or c in allowed_ids]
                if t is not None:
                    if t >= thr.get("t_high", 999) and should_alert(lab_id, "t_high"):
                        msg = f"⚠️ {lab_id}: temp {fmt_val(t)} > {fmt_val(thr.get('t_high'))} ({sid})"
                        for chat in recipients:
                            bot.sendMessage(chat, msg)
                        track_alert(lab_id, "t_high")
                    if t <= thr.get("t_low", -999) and should_alert(lab_id, "t_low"):
                        msg = f"⚠️ {lab_id}: temp {fmt_val(t)} < {fmt_val(thr.get('t_low'))} ({sid})"
                        for chat in recipients:
                            bot.sendMessage(chat, msg)
                        track_alert(lab_id, "t_low")
                if h is not None:
                    if h >= thr.get("h_high", 999) and should_alert(lab_id, "h_high"):
                        msg = f"⚠️ {lab_id}: humidity {fmt_val(h)} > {fmt_val(thr.get('h_high'))} ({sid})"
                        for chat in recipients:
                            bot.sendMessage(chat, msg)
                        track_alert(lab_id, "h_high")
                    if h <= thr.get("h_low", -999) and should_alert(lab_id, "h_low"):
                        msg = f"⚠️ {lab_id}: humidity {fmt_val(h)} < {fmt_val(thr.get('h_low'))} ({sid})"
                        for chat in recipients:
                            bot.sendMessage(chat, msg)
                        track_alert(lab_id, "h_low")
        time.sleep(ALERT_POLL_SEC)


def handle(msg):
    glance = telepot.glance(msg, flavor="chat")
    if glance and glance[0] == "text":
        _, _, chat_id = glance
        allowed = load_permissions()
        if allowed and chat_id not in allowed:
            bot.sendMessage(chat_id, "Not authorized.")
            return
        text = msg["text"].strip()
        parts = shlex.split(text)
        if not parts:
            return
        cmd = parts[0].lower()
        # Handle reply keyboard shortcuts
        if text in ("📊 Status", "status"):
            data = _get("status")
            bot.sendMessage(chat_id, fmt_status(data))
            return
        if text in ("🏷 Labs", "labs"):
            send_labs_list(chat_id)
            return
        if text in ("⚡ Turn ON", "turn on"):
            bot.sendMessage(chat_id, "Use: /turn_on <lab_id> <actuator_id>")
            return
        if text in ("⏻ Turn OFF", "turn off"):
            bot.sendMessage(chat_id, "Use: /turn_off <lab_id> <actuator_id>")
            return
        if text in ("➕ Add Lab", "add lab"):
            bot.sendMessage(chat_id, 'Template: /add_lab <lab_id> "<name>" [notes]')
            return
        if text in ("➖ Remove Lab", "remove lab"):
            bot.sendMessage(chat_id, "Template: /remove_lab <lab_id>")
            return
        if text in ("➕ Add Sensor", "add sensor"):
            bot.sendMessage(chat_id, "Template: /add_sensor <lab_id> <sensor_id> <type>")
            return
        if text in ("➖ Remove Sensor", "remove sensor"):
            bot.sendMessage(chat_id, "Template: /remove_sensor <sensor_id>")
            return
        if text in ("➕ Add Actuator", "add actuator"):
            bot.sendMessage(chat_id, "Template: /add_actuator <lab_id> <actuator_id> <type>")
            return
        if text in ("➖ Remove Actuator", "remove actuator"):
            bot.sendMessage(chat_id, "Template: /remove_actuator <actuator_id>")
            return

        if cmd in ("/start", "/help"):
            KNOWN_CHATS.add(chat_id)
            send_menu(chat_id)
            return

        if cmd == "/menu":
            KNOWN_CHATS.add(chat_id)
            send_menu(chat_id)
            return

        if cmd == "/status":
            data = _get("status")
            bot.sendMessage(chat_id, fmt_status(data))
            return

        if cmd == "/list_labs":
            send_labs_list(chat_id)
            return

        if cmd in ("/turn_on", "/turn_off"):
            action = "ON" if cmd == "/turn_on" else "OFF"
            if len(parts) != 3:
                bot.sendMessage(chat_id, f"Usage: {cmd} <lab_id> <actuator_id>")
                return
            payload = {"lab_id": parts[1], "actuator_id": parts[2], "action": action, "source": "bot"}
            res = _post("command", payload)
            bot.sendMessage(chat_id, "OK" if res.get("ok") else f"Error: {res.get('error','unknown')}")
            return

        if cmd == "/add_lab":
            if len(parts) < 3:
                bot.sendMessage(chat_id, "Usage: /add_lab <lab_id> \"<name>\" [notes]")
                return
            lab_id, name = parts[1], parts[2]
            notes = " ".join(parts[3:]) if len(parts) > 3 else ""
            res = _post("labs", {"lab_id": lab_id, "name": name, "notes": notes})
            bot.sendMessage(chat_id, "OK" if res.get("ok") else f"Error: {res.get('error','unknown')}")
            return

        if cmd == "/remove_lab":
            if len(parts) != 2:
                bot.sendMessage(chat_id, "Usage: /remove_lab <lab_id>")
                return
            try:
                r = requests.delete(f"{REGISTRY_API.rstrip('/')}/lab/{parts[1]}", timeout=5)
                res = r.json()
            except Exception:
                res = {"error": "registry unreachable"}
            bot.sendMessage(chat_id, "OK" if res.get("ok") else f"Error: {res.get('error','unknown')}")
            return

        if cmd == "/add_sensor":
            if len(parts) != 4:
                bot.sendMessage(chat_id, "Usage: /add_sensor <lab_id> <sensor_id> <type>")
                return
            res = _post("sensors", {"lab_id": parts[1], "sensor_id": parts[2], "type": parts[3]})
            bot.sendMessage(chat_id, "OK" if res.get("ok") else f"Error: {res.get('error','unknown')}")
            return

        if cmd == "/remove_sensor":
            if len(parts) != 2:
                bot.sendMessage(chat_id, "Usage: /remove_sensor <sensor_id>")
                return
            try:
                r = requests.delete(f"{REGISTRY_API.rstrip('/')}/sensor/{parts[1]}", timeout=5)
                res = r.json()
            except Exception:
                res = {"error": "registry unreachable"}
            bot.sendMessage(chat_id, "OK" if res.get("ok") else f"Error: {res.get('error','unknown')}")
            return

        if cmd == "/add_actuator":
            if len(parts) != 4:
                bot.sendMessage(chat_id, "Usage: /add_actuator <lab_id> <actuator_id> <type>")
                return
            res = _post("actuators", {"lab_id": parts[1], "actuator_id": parts[2], "type": parts[3]})
            bot.sendMessage(chat_id, "OK" if res.get("ok") else f"Error: {res.get('error','unknown')}")
            return

        if cmd == "/remove_actuator":
            if len(parts) != 2:
                bot.sendMessage(chat_id, "Usage: /remove_actuator <actuator_id>")
                return
            try:
                r = requests.delete(f"{REGISTRY_API.rstrip('/')}/actuator/{parts[1]}", timeout=5)
                res = r.json()
            except Exception:
                res = {"error": "registry unreachable"}
            bot.sendMessage(chat_id, "OK" if res.get("ok") else f"Error: {res.get('error','unknown')}")
            return

        bot.sendMessage(chat_id, "Unknown command. Use /help")
    else:
        # handle inline button presses
        flavor = telepot.flavor(msg)
        if flavor == "callback_query":
            query_id, from_id, data = telepot.glance(msg, flavor="callback_query")
            if data == "status":
                KNOWN_CHATS.add(from_id)
                bot.sendMessage(from_id, fmt_status(_get("status")))
            elif data == "list_labs":
                send_labs_list(from_id)
            elif data.startswith("lab:"):
                lab_id = data.split(":", 1)[1]
                send_lab_controls(from_id, lab_id)
            elif data.startswith("cmd:"):
                _, lab_id, actuator_id, action = data.split(":", 3)
                payload = {"lab_id": lab_id, "actuator_id": actuator_id, "action": action, "source": "bot"}
                res = _post("command", payload)
                bot.sendMessage(from_id, "OK" if res.get("ok") else f"Error: {res.get('error','unknown')}")
            elif data.startswith("tpl:"):
                _, kind = data.split(":", 1)
                send_template(from_id, kind)
            elif data.startswith("onall:"):
                lab_id = data.split(":", 1)[1]
                do_all(from_id, lab_id, "ON")
            elif data.startswith("offall:"):
                lab_id = data.split(":", 1)[1]
                do_all(from_id, lab_id, "OFF")
            bot.answerCallbackQuery(query_id)


def do_all(chat_id, lab_id, action):
    status = _get("status")
    labs = [l for l in status.get("labs", []) if l.get("lab_id") == lab_id]
    if not labs:
        bot.sendMessage(chat_id, "Lab not found.")
        return
    errors = []
    for act in labs[0].get("actuators", []):
        payload = {"lab_id": lab_id, "actuator_id": act.get("actuator_id"), "action": action, "source": "bot"}
        res = _post("command", payload)
        if not res.get("ok"):
            errors.append(f"{act.get('actuator_id')}: {res.get('error','unknown')}")
    bot.sendMessage(chat_id, "Done." if not errors else "\n".join(errors))


def send_menu(chat_id):
    KNOWN_CHATS.add(chat_id)
    labs = get_labs()
    help_text = (
        "🤖 Temp/Humidity Bot\n"
        "Use buttons or commands:\n"
        "• /status – full status\n"
        "• /list_labs – list labs\n"
        "• /turn_on <lab_id> <actuator_id>\n"
        "• /turn_off <lab_id> <actuator_id>\n"
        "• /add_lab <lab_id> \"name\" [notes]\n"
        "• /remove_lab <lab_id>\n"
        "• /add_sensor <lab_id> <sensor_id> <type>\n"
        "• /remove_sensor <sensor_id>\n"
        "• /add_actuator <lab_id> <actuator_id> <type>\n"
        "• /remove_actuator <actuator_id>\n"
        "Examples: lab_id=lab1, actuator_id=lab1_fan_1, sensor_id=lab1_temp_1\n"
    )
    reply_kb = ReplyKeyboardMarkup(
        keyboard=[
            ["📊 Status", "🏷 Labs"],
            ["⚡ Turn ON", "⏻ Turn OFF"],
            ["➕ Add Lab", "➖ Remove Lab"],
            ["➕ Add Sensor", "➖ Remove Sensor"],
            ["➕ Add Actuator", "➖ Remove Actuator"],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
    bot.sendMessage(chat_id, help_text, reply_markup=reply_kb)


def send_lab_controls(chat_id, lab_id):
    status = _get("status")
    labs = [l for l in status.get("labs", []) if l.get("lab_id") == lab_id]
    if not labs:
        bot.sendMessage(chat_id, "Lab not found.")
        return
    lab = labs[0]
    rows = []
    for act in lab.get("actuators", []):
        aid = act.get("actuator_id")
        rows.append(
            [
                InlineKeyboardButton(text=f"⚡ ON {aid}", callback_data=f"cmd:{lab_id}:{aid}:ON"),
                InlineKeyboardButton(text=f"⏻ OFF {aid}", callback_data=f"cmd:{lab_id}:{aid}:OFF"),
            ]
        )
    kb = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
    bot.sendMessage(chat_id, f"Lab {lab_id} controls:", reply_markup=kb)


def send_lab_picker(chat_id, action, all_actuators=False):
    labs = get_labs()
    if not labs:
        bot.sendMessage(chat_id, "No labs.")
        return
    buttons = [InlineKeyboardButton(text=f"{lab['lab_id']}", callback_data=f"pick:{action}:{lab['lab_id']}:one") for lab in labs]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    bot.sendMessage(chat_id, f"Choose lab for {action}", reply_markup=kb)


def send_actuator_picker(chat_id, lab_id, action):
    status = _get("status")
    labs = [l for l in status.get("labs", []) if l.get("lab_id") == lab_id]
    if not labs:
        bot.sendMessage(chat_id, "Lab not found.")
        return
    acts = labs[0].get("actuators", [])
    if not acts:
        bot.sendMessage(chat_id, "No actuators in this lab.")
        return
    rows = [
        [InlineKeyboardButton(text=a.get("actuator_id"), callback_data=f"cmd:{lab_id}:{a.get('actuator_id')}:{action}")]
        for a in acts
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    bot.sendMessage(chat_id, f"Choose actuator in {lab_id} to {action}", reply_markup=kb)


def send_labs_list(chat_id):
    labs = get_labs()
    if not labs:
        bot.sendMessage(chat_id, "No labs.")
        return
    txt = "\n".join(f"🏷 {l['lab_id']} – {l.get('name','')}" for l in labs)
    bot.sendMessage(chat_id, txt)


def send_template(chat_id, kind):
    templates = {
        "add_lab": '/add_lab <lab_id> "<name>" [notes]',
        "remove_lab": "/remove_lab <lab_id>",
        "add_sensor": "/add_sensor <lab_id> <sensor_id> <type>",
        "remove_sensor": "/remove_sensor <sensor_id>",
        "add_actuator": "/add_actuator <lab_id> <actuator_id> <type>",
        "remove_actuator": "/remove_actuator <actuator_id>",
    }
    bot.sendMessage(chat_id, f"Template: {templates.get(kind, 'unknown')}")


if __name__ == "__main__":
    if not TOKEN:
        print("TELEGRAM_BOT_TOKEN not set; exiting.")
        exit(1)
    bot = telepot.Bot(TOKEN)
    MessageLoop(bot, handle).run_as_thread()
    import threading
    t = threading.Thread(target=poll_alerts, args=(bot,), daemon=True)
    t.start()
    print("Bot running. Press Ctrl+C to exit.")
    while True:
        time.sleep(10)
