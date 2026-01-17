IOT Temperature-Humidity Controller

Overview
This project is a local IoT controller for multiple labs (lab1-lab3). Sensors publish temperature and humidity over MQTT. A controller applies threshold rules and sends actuator commands. A CherryPy registry exposes REST endpoints for a dashboard and Telegram bot. ThingSpeak upload is optional. Configuration is stored in JSON files; no database is used.

Services
- mqtt: Mosquitto broker for sensor and actuator messages.
- controller: MQTT client, sensor/actuator bridges, in-memory state, control logic, watchdog, and a small HTTP API.
- registry: REST API for catalog CRUD and /status. Reads live state from the controller.
- telegram_bot: Bot that polls /status, sends alerts, and issues /command requests.
- thingspeak: Optional adaptor that reads /status and writes configured fields to ThingSpeak.
- frontend: Static UI that polls /status and renders lab cards.

How Telegram and Frontend get real-time data
- Both use the registry service over HTTP.
- The registry calls the controller's /snapshot endpoint to get the latest in-memory state.
- The frontend polls GET /status every few seconds.
- The bot polls GET /status for alerts and uses /command for manual control.

Data flow
Sensors to controller:
- MQTT topic: labs/<lab_id>/sensors/<sensor_id>/state
- Payload: {"t": float, "h": float, "ts": unix_ts, "sensor_id": "..."}

Controller to actuators:
- MQTT topic: labs/<lab_id>/actuators/<actuator_id>/cmd
- Payload: {"action": "ON|OFF", "source": "controller|bot|ui", "ts": unix_ts}

Actuator feedback:
- MQTT topic: labs/<lab_id>/actuators/<actuator_id>/state
- Payload: {"state": "ON|OFF", "ts": unix_ts, "actuator_id": "..."}

Control logic
- Each lab has thresholds in catalog/thresholds.json.
- Hysteresis prevents rapid toggling.
- off_delay_sec ensures a minimum OFF time between cycles.
- Fan: ON when temp or humidity exceeds high thresholds, OFF only when both fall below low thresholds.
- Heater, humidifier, dehumidifier use thresholds with hysteresis.

Repository layout
- catalog/: JSON catalogs and atomic read/write helper.
- catalog_registry/: REST API and validators.
- controller/: control logic, state memory, rules, controller API.
- Device_connectors/: MQTT client, sensor bridge, actuator bridge.
- User_awareness/: Telegram bot.
- ThingSpeak/: adaptor and keys.json.
- frontEnd/: static dashboard.
- docker-compose.yml: service orchestration.

Running with Docker
1) Build and start:
   docker compose up -d --build
2) Open dashboard:
   http://localhost
3) Registry API:
   http://localhost:8080/status

Environment variables
- TELEGRAM_BOT_TOKEN: Telegram bot token.
- REGISTRY_API_URL: Base URL for registry (default http://registry:8080 inside Docker).
- PERMISSIONS_PATH: Path to permissions.json for Telegram (default ./catalog/permissions.json).
- THINGSPEAK_KEYS_PATH: Path to ThingSpeak keys.json.
- THINGSPEAK_POLL_SEC: Polling interval for ThingSpeak updates.
- CONTROL_LOOP_SEC: Controller loop interval.
- SIM_LOOP_SEC: Sensor simulator interval.

Permissions
catalog/permissions.json controls which chat IDs can use the bot and receive alerts.
Add your chat ID to owners or operators and restart the telegram_bot container.

ThingSpeak
ThingSpeak/adaptor.py reads /status from the registry and posts mapped fields.
Configure channels and fields in ThingSpeak/keys.json.

Notes
- JSON files are the source of truth for catalogs. No database is used.
- The controller is the only component that holds live sensor/actuator state.
- The registry aggregates catalogs with live controller state into /status.
