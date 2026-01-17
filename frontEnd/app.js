const API_BASE = window.API_BASE || "";
const REFRESH_MS = window.STATUS_POLL_MS || 5000;

const labsContainer = document.getElementById("labs-container");
const statusIndicator = document.getElementById("status-indicator");
const intervalControl = document.getElementById("poll-interval");

async function fetchStatus() {
  try {
    const res = await fetch(`${API_BASE}/status`);
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    renderStatus(data);
    setIndicator("Online", "ok");
  } catch (err) {
    console.error("Failed to load status", err);
    setIndicator("Offline", "error");
  } finally {
    window.setTimeout(fetchStatus, currentInterval());
  }
}

function currentInterval() {
  const val = parseInt(intervalControl?.value || REFRESH_MS, 10);
  return Number.isNaN(val) ? REFRESH_MS : Math.max(1000, val);
}

function setIndicator(text, state) {
  statusIndicator.textContent = text;
  statusIndicator.className = state;
}

function renderStatus(data) {
  const labs = data.labs || [];
  labsContainer.innerHTML = "";
  labs.forEach((lab) => labsContainer.appendChild(renderLabCard(lab)));
}

function renderLabCard(lab) {
  const card = document.createElement("article");
  card.className = "lab-card";

  const offline = lab.alerts && lab.alerts.sensor_offline;
  const lastSeenTs = lab.last_sensor_seen || 0;
  const ageSec = lastSeenTs ? Math.max(0, Math.floor(Date.now() / 1000 - lastSeenTs)) : null;

  const header = document.createElement("header");
  header.innerHTML = `
    <h2>${lab.lab_id} — ${lab.name || ""} ${offline ? '<span class="badge danger">Sensor offline</span>' : ""}</h2>
    <p>${lab.notes || ""}</p>
  `;
  card.appendChild(header);

  const thresholds = lab.thresholds || {};
  const summaryTemp = summarizeTemp(lab.sensors);
  const summaryHum = summarizeHum(lab.sensors);
  const activeActs = (lab.actuators || []).filter((a) => (a.state?.state || "").toUpperCase() === "ON").length;

  const info = document.createElement("div");
  info.className = "lab-info";
  info.innerHTML = `
    <div><strong>Temp Low/High:</strong> ${thresholds.t_low ?? "?"} / ${thresholds.t_high ?? "?"}</div>
    <div><strong>Hum Low/High:</strong> ${thresholds.h_low ?? "?"} / ${thresholds.h_high ?? "?"}</div>
    <div><strong>Off Delay:</strong> ${thresholds.off_delay_sec ?? "?"} s</div>
    <div><strong>Last Sensor:</strong> ${formatTs(lab.last_sensor_seen)}</div>
    <div><strong>Summary:</strong> ${summaryTemp}, ${summaryHum}, active actuators: ${activeActs}</div>
    ${ageSec && ageSec > 30 ? '<div><span class="badge warn">Stale data</span></div>' : ""}
  `;
  card.appendChild(info);

  if (lab.alerts && lab.alerts.sensor_offline) {
    const alert = document.createElement("div");
    alert.className = "alert";
    alert.textContent = "Sensor offline!";
    card.appendChild(alert);
  }

  const sensors = document.createElement("section");
  sensors.className = "sensors";
  sensors.innerHTML = "<h3>Sensors</h3>";
  const sensorList = document.createElement("ul");
  sensorList.className = "grid";
  let outOfRange = false;
  (lab.sensors || []).forEach((sensor) => {
    const reading = sensor.reading || {};
    const type = sensor.type || "";
    const showTemp = type !== "hum";
    const showHum = type !== "temp";
    if (showTemp && isOutOfRange(reading.t, thresholds.t_low, thresholds.t_high)) outOfRange = true;
    if (showHum && isOutOfRange(reading.h, thresholds.h_low, thresholds.h_high)) outOfRange = true;
    const item = document.createElement("li");
    item.innerHTML = `
      <span class="item-title">${sensor.sensor_id}</span>
      <span class="item-sub">${type}</span>
      ${showTemp ? `<span class="item-value">T: ${fmt(reading.t)}°C</span>` : ""}
      ${showHum ? `<span class="item-value">H: ${fmt(reading.h)}%</span>` : ""}
      <span class="item-meta">${formatTs(reading.ts)}</span>
    `;
    if ((showTemp && isOutOfRange(reading.t, thresholds.t_low, thresholds.t_high)) || (showHum && isOutOfRange(reading.h, thresholds.h_low, thresholds.h_high))) {
      item.classList.add("alert");
    }
    sensorList.appendChild(item);
  });
  sensors.appendChild(sensorList);
  card.appendChild(sensors);

  if (outOfRange) {
    card.classList.add("alert");
  }
  const actuators = document.createElement("section");
  actuators.className = "actuators";
  actuators.innerHTML = "<h3>Actuators</h3>";
  const actuatorList = document.createElement("ul");
  actuatorList.className = "grid";
  (lab.actuators || []).forEach((actuator) => {
    const state = actuator.state || {};
    const stateLabel = (state.state || "OFF").toUpperCase();
    const age = state.ts ? Math.max(0, Math.floor(Date.now() / 1000 - state.ts)) : null;
    const item = document.createElement("li");
    item.classList.toggle("on", stateLabel === "ON");
    item.innerHTML = `
      <span class="item-title">${actuator.actuator_id}</span>
      <span class="item-sub">${actuator.type || ""}</span>
      <span class="item-value">${stateLabel}</span>
      <span class="item-meta">${formatTs(state.ts)} ${age && age > 60 ? '<span class="badge warn">Stale</span>' : ""}</span>
    `;
    actuatorList.appendChild(item);
  });
  actuators.appendChild(actuatorList);
  card.appendChild(actuators);

  return card;
}

function fmt(value) {
  if (value === undefined || value === null || Number.isNaN(value)) {
    return "?";
  }
  return Number.parseFloat(value).toFixed(2);
}

function formatTs(ts) {
  if (!ts) return "never";
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return String(ts);
  return d.toLocaleString();
}

fetchStatus();

if (intervalControl) {
  intervalControl.value = REFRESH_MS;
  intervalControl.addEventListener("change", fetchStatus);
}

function summarizeTemp(sensors = []) {
  const temps = sensors.map((s) => s.reading?.t).filter((v) => v !== undefined);
  if (!temps.length) return "Temp: ?";
  const avg = temps.reduce((a, b) => a + b, 0) / temps.length;
  return `Temp: ${avg.toFixed(2)}°C`;
}

function summarizeHum(sensors = []) {
  const hums = sensors.map((s) => s.reading?.h).filter((v) => v !== undefined);
  if (!hums.length) return "Hum: ?";
  const avg = hums.reduce((a, b) => a + b, 0) / hums.length;
  return `Hum: ${avg.toFixed(2)}%`;
}

function isOutOfRange(val, low, high) {
  if (val === undefined || val === null) return false;
  if (low !== undefined && val < low) return true;
  if (high !== undefined && val > high) return true;
  return false;
}
