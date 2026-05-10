/* @ts-check */
"use strict";

/** @type {number} */
const REFRESH_MS = 5000;

/** @type {string} */
const METRICS_URL = "/metrics";

/** @type {number | null} */
let timerId = null;

/**
 * @param {number | null | undefined} val
 * @returns {string}
 */
function formatMs(val) {
  if (val == null || val === 0) return "\u2014";
  return val < 1000
    ? `${val.toFixed(1)} ms`
    : `${(val / 1000).toFixed(2)} s`;
}

/**
 * @param {number | null | undefined} val
 * @returns {string}
 */
function formatNum(val) {
  if (val == null || val === 0) return "\u2014";
  return val.toLocaleString();
}

/** @param {string} msg */
function setStatus(msg) {
  /** @type {HTMLElement | null} */
  const el = document.getElementById("status");
  if (el) el.textContent = msg;
}

/**
 * @typedef {Object} OverallStats
 * @property {number} count
 * @property {number} avg_ttft_ms
 * @property {number} avg_total_time_ms
 * @property {number} sum_input_tokens
 * @property {number} sum_output_tokens
 * @property {number} avg_tps
 */

/**
 * @param {OverallStats | undefined} overall
 */
function renderOverall(overall) {
  if (!overall) return;

  /** @type {HTMLTableElement | null} */
  const table = document.getElementById("overall-table");
  if (!table) return;

  /** @type {[string, string][]} */
  const rows = [
    ["Requests", String(overall.count)],
    ["Avg TTFT", formatMs(overall.avg_ttft_ms)],
    ["Avg Total Time", formatMs(overall.avg_total_time_ms)],
    ["Input Tokens", formatNum(overall.sum_input_tokens)],
    ["Output Tokens", formatNum(overall.sum_output_tokens)],
    ["Avg Tokens/s", overall.avg_tps != null ? `${overall.avg_tps.toFixed(1)}` : "\u2014"],
  ];

  table.innerHTML = "";
  for (const [label, value] of rows) {
    const tr = document.createElement("tr");
    const td1 = document.createElement("td");
    td1.textContent = label;
    const td2 = document.createElement("td");
    td2.textContent = value;
    tr.appendChild(td1);
    tr.appendChild(td2);
    table.appendChild(tr);
  }
}

/**
 * @param {Record<string, OverallStats> | undefined} perModel
 */
function renderPerModel(perModel) {
  /** @type {HTMLTableElement | null} */
  const table = document.getElementById("permodel-table");
  if (!table) return;

  const headers = [
    "Model",
    "Requests",
    "Avg TTFT",
    "Avg Total",
    "In Tokens",
    "Out Tokens",
    "Tokens/s",
  ];

  const thead = table.querySelector("thead");
  if (thead) {
    thead.innerHTML = "";
    const htr = document.createElement("tr");
    for (const h of headers) {
      const th = document.createElement("th");
      th.textContent = h;
      htr.appendChild(th);
    }
    thead.appendChild(htr);
  }

  /** @type {HTMLTableSectionElement | null} */
  const tbody = table.querySelector("tbody");
  if (!tbody) return;
  tbody.innerHTML = "";

  if (!perModel) return;

  const modelNames = Object.keys(perModel);
  if (modelNames.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = headers.length;
    td.textContent = "No data yet";
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  for (const name of modelNames) {
    const s = perModel[name];
    const tr = document.createElement("tr");
    const cells = [
      name,
      String(s.count),
      formatMs(s.avg_ttft_ms),
      formatMs(s.avg_total_time_ms),
      formatNum(s.sum_input_tokens),
      formatNum(s.sum_output_tokens),
      s.avg_tps != null ? `${s.avg_tps.toFixed(1)}` : "\u2014",
    ];
    for (const text of cells) {
      const td = document.createElement("td");
      td.textContent = text;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
}

/**
 * @typedef {Object} RecentMetric
 * @property {string} request_id
 * @property {number} timestamp
 * @property {string} public_model
 * @property {string} backend_model
 * @property {string} backend_name
 * @property {number | null} input_tokens
 * @property {number | null} ttft_ms
 * @property {number | null} total_time_ms
 * @property {number | null} response_status
 * @property {number | null} output_tokens
 * @property {number | null} bytes_sent
 */

/**
 * @param {RecentMetric[] | undefined} metrics
 */
function renderRecent(metrics) {
  /** @type {HTMLTableElement | null} */
  const table = document.getElementById("recent-table");
  if (!table) return;

  const headers = [
    "Time",
    "Model",
    "Backend",
    "TTFT",
    "Total",
    "Status",
    "In",
    "Out",
    "Bytes",
  ];

  const thead = table.querySelector("thead");
  if (thead) {
    thead.innerHTML = "";
    const htr = document.createElement("tr");
    for (const h of headers) {
      const th = document.createElement("th");
      th.textContent = h;
      htr.appendChild(th);
    }
    thead.appendChild(htr);
  }

  /** @type {HTMLTableSectionElement | null} */
  const tbody = table.querySelector("tbody");
  if (!tbody) return;
  tbody.innerHTML = "";

  if (!metrics || metrics.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = headers.length;
    td.textContent = "No requests yet";
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  const reversed = metrics.slice().reverse();
  for (const m of reversed) {
    const ts = new Date(m.timestamp * 1000);
    const tr = document.createElement("tr");
    const cells = [
      ts.toLocaleTimeString(),
      m.public_model,
      m.backend_name,
      formatMs(m.ttft_ms),
      formatMs(m.total_time_ms),
      m.response_status != null ? String(m.response_status) : "\u2014",
      formatNum(m.input_tokens),
      formatNum(m.output_tokens),
      formatNum(m.bytes_sent),
    ];
    for (const text of cells) {
      const td = document.createElement("td");
      td.textContent = text;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
}

function fetchData() {
  setStatus("Loading\u2026");
  fetch(METRICS_URL)
    .then((/** @type {Response} */ r) => r.json())
    .then(
      /** @param {Record<string, unknown>} data */
      (data) => {
        /** @type {OverallStats | undefined} */
        const overall = /** @type {OverallStats} */ data.overall;
        /** @type {Record<string, OverallStats> | undefined} */
        const perModel = /** @type {Record<string, OverallStats>} */ data.per_model;
        /** @type {RecentMetric[] | undefined} */
        const recent = /** @type {RecentMetric[]} */ (data.recent_metrics);

        renderOverall(overall);
        renderPerModel(perModel);
        renderRecent(recent);
        setStatus(`Updated ${new Date().toLocaleTimeString()}`);
      }
    )
    .catch(
      /** @param {Error} err */ (err) => {
        setStatus(`Error: ${err.message}`);
      }
    );
}

function start() {
  fetchData();
  timerId = window.setInterval(fetchData, REFRESH_MS);
}

function stop() {
  if (timerId !== null) {
    window.clearInterval(timerId);
    timerId = null;
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", start);
} else {
  start();
}

window.addEventListener("beforeunload", stop);
