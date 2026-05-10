/* @ts-check */
"use strict";

/** @type {number} */
const POLL_MS = 1000;

/** @type {string | null} */
let currentRunId = null;

/** @type {number | null} */
let pollTimer = null;

/** @type {HTMLButtonElement} */
const btnRun = /** @type {HTMLButtonElement} */ (document.getElementById("btn-run"));

/** @type {HTMLSelectElement} */
const selModel = /** @type {HTMLSelectElement} */ (document.getElementById("sel-model"));

/** @type {HTMLInputElement} */
const inpIter = /** @type {HTMLInputElement} */ (document.getElementById("inp-iter"));

/** @type {HTMLInputElement} */
const inpTokens = /** @type {HTMLInputElement} */ (document.getElementById("inp-tokens"));

/** @type {HTMLInputElement} */
const inpStream = /** @type {HTMLInputElement} */ (document.getElementById("inp-stream"));

/** @type {HTMLInputElement} */
const inpPrompt = /** @type {HTMLInputElement} */ (document.getElementById("inp-prompt"));

/** @type {HTMLDivElement} */
const statusEl = /** @type {HTMLDivElement} */ (document.getElementById("status"));

/** @type {HTMLDivElement} */
const summaryCard = /** @type {HTMLDivElement} */ (document.getElementById("summary-card"));

/** @type {HTMLDivElement} */
const statGrid = /** @type {HTMLDivElement} */ (document.getElementById("stat-grid"));

/** @type {HTMLTableSectionElement} */
const tbody = /** @type {HTMLTableSectionElement} */ (document.getElementById("results-body"));

/**
 * @param {string} msg
 */
function setStatus(msg) {
  statusEl.textContent = msg;
}

/**
 * @param {number | null | undefined} ms
 * @returns {string}
 */
function formatMs(ms) {
  if (ms == null || ms === 0) return "—";
  return ms < 1000 ? `${ms.toFixed(0)} ms` : `${(ms / 1000).toFixed(2)} s`;
}

/**
 * @param {number | null | undefined} val
 * @returns {string}
 */
function formatNum(val) {
  if (val == null || val === 0) return "—";
  return val.toLocaleString();
}

/**
 * @param {number | null | undefined} val
 * @returns {string}
 */
function formatTps(val) {
  if (val == null || val === 0) return "—";
  return `${val.toFixed(1)}`;
}

/**
 * @typedef {Object} BenchIteration
 * @property {number} iteration
 * @property {number | null} latency_ms
 * @property {number | null} ttft_ms
 * @property {number | null} input_tokens
 * @property {number | null} output_tokens
 * @property {number | null} tps
 * @property {string | null} error
 */

/**
 * @typedef {Object} BenchSummary
 * @property {string} run_id
 * @property {string} model
 * @property {string} backend
 * @property {string} prompt
 * @property {number} iterations
 * @property {boolean} stream
 * @property {number} started_at
 * @property {number | null} finished_at
 * @property {boolean} is_running
 * @property {number} completed
 * @property {number} ok
 * @property {number} failed
 * @property {number} avg_latency_ms
 * @property {number} min_latency_ms
 * @property {number} max_latency_ms
 * @property {number} avg_tps
 * @property {number} min_tps
 * @property {number} max_tps
 * @property {number} avg_ttft_ms
 * @property {number} total_input_tokens
 * @property {number} total_output_tokens
 * @property {BenchIteration[]} results
 */

/**
 * @param {BenchSummary} data
 */
function renderSummary(data) {
  if (data.completed === 0) {
    summaryCard.style.display = "none";
    return;
  }
  summaryCard.style.display = "";

  /** @type {[string, string, string][]} */
  const stats = [
    ["Avg Latency", formatMs(data.avg_latency_ms), "ok"],
    ["Min Latency", formatMs(data.min_latency_ms), "ok"],
    ["Max Latency", formatMs(data.max_latency_ms), "warn"],
    ["Avg TTFT", formatMs(data.avg_ttft_ms), "ok"],
    ["Avg Tokens/s", formatTps(data.avg_tps), "ok"],
    ["Min Tokens/s", formatTps(data.min_tps), "ok"],
    ["Max Tokens/s", formatTps(data.max_tps), "ok"],
    ["Total In", formatNum(data.total_input_tokens), "ok"],
    ["Total Out", formatNum(data.total_output_tokens), "ok"],
  ];

  statGrid.innerHTML = "";
  for (const [label, value, cls] of stats) {
    const box = document.createElement("div");
    box.className = "stat-box";
    const lbl = document.createElement("div");
    lbl.className = "label";
    lbl.textContent = label;
    const val = document.createElement("div");
    val.className = `value ${cls}`;
    val.textContent = value;
    box.appendChild(lbl);
    box.appendChild(val);
    statGrid.appendChild(box);
  }
}

/**
 * @param {BenchSummary} data
 */
function renderResults(data) {
  tbody.innerHTML = "";

  if (data.results.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 7;
    td.textContent = "Starting benchmark…";
    tr.appendChild(td);
    tbody.appendChild(tr);
    setStatus(`Running benchmark ${data.run_id} for ${data.model}…`);
    return;
  }

  for (const r of data.results) {
    const tr = document.createElement("tr");
    const cells = [
      String(r.iteration),
      formatMs(r.latency_ms),
      formatMs(r.ttft_ms),
      formatNum(r.input_tokens),
      formatNum(r.output_tokens),
      formatTps(r.tps),
      r.error || "—",
    ];
    for (const text of cells) {
      const td = document.createElement("td");
      td.textContent = text;
      if (r.error) td.style.color = "#f85149";
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }

  if (data.is_running) {
    setStatus(`Running ${data.run_id} — ${data.completed}/${data.iterations} done (${data.ok} ok, ${data.failed} failed)…`);
  } else {
    setStatus(`Done ${data.run_id} — ${data.ok} ok, ${data.failed} failed (${data.iterations} total).`);
  }
}

/**
 * @param {BenchSummary} data
 */
function renderAll(data) {
  renderSummary(data);
  renderResults(data);
}

function startPoll(runId) {
  if (pollTimer !== null) {
    clearInterval(pollTimer);
  }

  /**
   * @returns {Promise<void>}
   */
  async function poll() {
    try {
      const resp = await fetch(`/benchmark/status?run_id=${runId}`);
      /** @type {BenchSummary} */
      const data = await resp.json();
      renderAll(data);
      if (!data.is_running) {
        if (pollTimer !== null) {
          clearInterval(pollTimer);
          pollTimer = null;
        }
        btnRun.disabled = false;
      }
    } catch (/** @type {any} */ err) {
      setStatus(`Poll error: ${err.message}`);
    }
  }

  poll();
  pollTimer = window.setInterval(poll, POLL_MS);
}

/**
 * Fetch available models and populate the dropdown.
 */
async function loadModels() {
  try {
    const resp = await fetch("/v1/models");
    /** @type {{ data: Array<{ id: string, owned_by: string }> }} */
    const data = await resp.json();
    selModel.innerHTML = "";
    for (const m of data.data) {
      const opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent = `${m.id} (${m.owned_by})`;
      selModel.appendChild(opt);
    }
  } catch (/** @type {any} */ err) {
    setStatus(`Failed to load models: ${err.message}`);
  }
}

btnRun.addEventListener("click", () => {
  const model = selModel.value;
  if (!model) {
    setStatus("Please select a model first.");
    return;
  }

  btnRun.disabled = true;
  setStatus("Starting benchmark…");
  tbody.innerHTML = "";
  statGrid.innerHTML = "";
  summaryCard.style.display = "none";

  const payload = {
    model: model,
    iterations: parseInt(inpIter.value, 10) || 5,
    max_tokens: parseInt(inpTokens.value, 10) || 256,
    stream: inpStream.checked,
    prompt: inpPrompt.value || "Explain quantum computing in three sentences.",
  };

  fetch("/benchmark/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
    .then(
      /** @param {Response} r */ (r) => r.json()
    )
    .then(
      /** @param {{ run_id: string }} d */ (d) => {
        currentRunId = d.run_id;
        setStatus(`Started benchmark ${d.run_id}`);
        startPoll(d.run_id);
      }
    )
    .catch(
      /** @param {Error} err */ (err) => {
        setStatus(`Error: ${err.message}`);
        btnRun.disabled = false;
      }
    );
});

window.addEventListener("beforeunload", () => {
  if (pollTimer !== null) {
    clearInterval(pollTimer);
  }
});

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", loadModels);
} else {
  loadModels();
}
