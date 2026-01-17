# Dockerfile
FROM python:3.11-slim

# system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev libssl-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

# requirements

# cherrypy, paho-mqtt, python-telegram-bot, requests
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt


COPY . /app


EXPOSE 8080


CMD ["python", "-u", "-m", "catalog_registry.registry_api"]
