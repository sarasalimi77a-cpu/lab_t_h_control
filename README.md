# IoT Temperature & Humidity Controller

## Overview
This project implements a local IoT-based temperature and humidity monitoring and control system for multiple laboratories (Lab 1 – Lab 3). Environmental sensors publish temperature and humidity data via MQTT, while a central controller applies configurable threshold rules and sends commands to actuators.

The system exposes REST APIs for a web dashboard and a Telegram bot using a CherryPy-based registry service. Optional integration with ThingSpeak is supported for cloud-based data visualization. Configuration and catalogs are stored in JSON files; no database is used.

---

## System Services

- **mqtt**  
  Mosquitto MQTT broker for sensor and actuator communication.

- **controller**  
  MQTT client responsible for:
  - Sensor and actuator bridging  
  - In-memory state management  
  - Control logic and safety rules  
  - Watchdog monitoring  
  - Internal HTTP API

- **registry**  
  CherryPy REST service providing:
  - Catalog CRUD operations  
  - `/status` endpoint combining live state and configuration

- **telegram_bot**  
  Telegram bot that:
  - Polls `/status` for alerts  
  - Sends notifications  
  - Issues manual control commands via `/command`

- **thingspeak (optional)**  
  Adapter service that reads `/status` and uploads selected fields to ThingSpeak.

- **frontend**  
  Static web dashboard that periodically polls `/status` and displays laboratory status cards.

---

## Real-Time Data Access

Both the **Telegram bot** and **Frontend dashboard** retrieve real-time data through the **registry service**:

- The registry queries the controller’s `/snapshot` endpoint to obtain the latest in-memory state.
- The frontend periodically polls `GET /status`.
- The Telegram bot polls `GET /status` for alerts and uses `/command` for manual control actions.

---

## Data Flow

### Sensors → Controller
- MQTT Topic: `labs/<lab_id>/sensors/<sensor_id>/state`
- Payload:

### Controller → Actuators
- MQTT Topic: `labs/<lab_id>/actuators/<actuator_id>/cmd`
- Payload:

### Actuator Feedback → Controller
- MQTT Topic: `labs/<lab_id>/actuators/<actuator_id>/state`
- Payload:

---

## Control Logic

- Each laboratory has configurable thresholds defined in `catalog/thresholds.json`.
- Hysteresis is applied to prevent rapid ON/OFF switching.
- `off_delay_sec` enforces a minimum OFF duration between cycles.
- Fan turns ON if temperature OR humidity exceeds high thresholds.
- Fan turns OFF only when BOTH fall below low thresholds.
- Heater, humidifier, and dehumidifier follow similar hysteresis rules.

---

## Repository Structure


- Dashboard: http://localhost
- Registry API: http://localhost:8080/status

---

## Environment Variables

- TELEGRAM_BOT_TOKEN
- REGISTRY_API_URL
- PERMISSIONS_PATH
- THINGSPEAK_KEYS_PATH
- THINGSPEAK_POLL_SEC
- CONTROL_LOOP_SEC
- SIM_LOOP_SEC

---

## Permissions

Telegram access is controlled by:
`catalog/permissions.json`

Add your chat ID to `owners` or `operators` and restart the telegram_bot container.

---

## Notes

- JSON files are the single source of truth.
- No database is used.
- The controller holds all live state.
- The registry exposes aggregated data via `/status`.

---

## License & Conduct

This project follows the repository Code of Conduct and is intended for educational and laboratory use.
