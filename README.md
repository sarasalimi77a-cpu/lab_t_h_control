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
- **MQTT Topic**  
  `labs/<lab_id>/sensors/<sensor_id>/state`
- **Payload**
```json
{
  "t": 25.3,
  "h": 45.0,
  "ts": 1700000000,
  "sensor_id": "sensor_01"
}

