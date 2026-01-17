"""Central logging configuration for the IoT backend.

Routes sensor, actuator, and control logs to separate files while keeping stdout output.
"""

from __future__ import annotations

import logging
import os
from logging.config import dictConfig
from typing import Any, Dict

_CONFIGURED = False


def configure_logging() -> None:
    """Set up log handlers only once."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_dir = os.environ.get("LOG_DIR", "/tmp/logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        log_dir = "/tmp"
        os.makedirs(log_dir, exist_ok=True)

    def _file(name: str) -> Dict[str, Any]:
        return {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": os.path.join(log_dir, f"{name}.log"),
            "maxBytes": 1_000_000,
            "backupCount": 3,
            "encoding": "utf-8",
            "formatter": "detailed",
        }

    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "simple": {
                "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
            },
            "detailed": {
                "format": "%(asctime)s %(levelname)s %(name)s [%(filename)s:%(lineno)d]: %(message)s",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "simple",
                "level": os.environ.get("LOG_LEVEL", "WARNING"),
            },
            "sensor_file": _file("sensors"),
            "actuator_file": _file("actuators"),
            "logic_file": _file("logic"),
        },
        "root": {
            "handlers": ["console"],
            "level": os.environ.get("LOG_LEVEL_ROOT", "WARNING"),
        },
        "loggers": {
            # Sensor pipeline
            "Device_connectors.sensor_bridge": {
                "handlers": ["sensor_file", "console"],
                "level": "INFO",
                "propagate": False,
            },
            "controller.state_memory": {
                "handlers": ["sensor_file", "console"],
                "level": "INFO",
                "propagate": False,
            },
            # Actuator pipeline
            "Device_connectors.actuator_bridge": {
                "handlers": ["actuator_file", "console"],
                "level": "INFO",
                "propagate": False,
            },
            "controller.rules": {
                "handlers": ["logic_file", "console"],
                "level": "INFO",
                "propagate": False,
            },
            "controller.control_unit": {
                "handlers": ["logic_file", "console"],
                "level": "INFO",
                "propagate": False,
            },
            "controller.cu_instancer": {
                "handlers": ["logic_file", "console"],
                "level": "INFO",
                "propagate": False,
            },
            "Device_connectors.mqtt_client": {
                "handlers": ["logic_file", "console"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }

    dictConfig(config)
    _CONFIGURED = True
