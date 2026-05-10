/* @ts-check */
"use strict";

/** @type {number} */
const POLL_MS = 1000;

/** @type {string | null} */
let currentTestId = null;

/** @type {number | null} */
let pollTimer = null;

/** @type {HTMLButtonElement} */
const btnRun = /** @type {HTMLButtonElement} */ (document.getElementById("btn-run"));

/** @type {HTMLInputElement} */
const inpMax = /** @type {HTMLInputElement} */ (document.getElementById("inp-max"));

/** @type {HTMLInputElement} */
const inpStream = /** @type {HTMLInputElement} */ (document.getElementById("inp-stream"));

/** @type {HTMLInputElement} */
const inpPrompt = /** @type {HTMLInputElement} */ (document.getElementById("inp-prompt"));

/** @type {HTMLDivElement} */
const statusEl = /** @type {HTMLDivElement} */ (document.getElementById("status"));

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
function formatLatency(ms) {
  if (ms == null) return "—";
  return ms < 1000 ? `${ms.toFixed(0)} ms` : `${(ms / 1000).toFixed(2)} s`;
}

/**
 * @param {number | null | undefined} val
 * @returns {string}
 */
function formatNum(val) {
  if (val == null) return "—";
  return val.toLocaleString();
}

/**
 * @param {string} status
 * @returns {string}
 */
function statusClass(status) {
  if (status === "ok") return "status-ok";
  if (status === "error") return "status-error";
  return "status-pending";
}

/**
 * @typedef {Object} SmokeResult
 * @property {string} model
 * @property {string} backend
 * @property {string} status
 * @property {number | null} latency_ms
 * @property {number | null} input_tokens
 * @property {number | null} output_tokens
 * @property {number | null} tps
 * @property {string | null} error
 * @property {string | null} response_preview
 */

/**
 * @typedef {Object} SmokeSummary
 * @property {string} test_id
 * @property {number} started_at
 * @property {number | null} finished_at
 * @property {boolean} is_running
 * @property {number} total_models
 * @property {number} ok
 * @property {number} failed
 * @property {number} pending
 * @property {SmokeResult[]} results
 */

/**
 * @param {SmokeSummary} data
 */
function renderResults(data) {
  tbody.innerHTML = "";

  if (data.results.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 9;
    td.textContent = "Discovering models…";
    tr.appendChild(td);
    tbody.appendChild(tr);
    setStatus(`Running test ${data.test_id}…`);
    return;
  }

  const ok = data.ok;
  const failed = data.failed;
  const pending = data.pending;

  for (const r of data.results) {
    const tr = document.createElement("tr");

    const tdModel = document.createElement("td");
    tdModel.textContent = r.model;

    const tdBackend = document.createElement("td");
    tdBackend.textContent = r.backend;

    const tdStatus = document.createElement("td");
    tdStatus.textContent = r.status;
    tdStatus.className = statusClass(r.status);

    const tdLatency = document.createElement("td");
    tdLatency.textContent = formatLatency(r.latency_ms);

    const tdIn = document.createElement("td");
    tdIn.textContent = formatNum(r.input_tokens);

    const tdOut = document.createElement("td");
    tdOut.textContent = formatNum(r.output_tokens);

    const tdTps = document.createElement("td");
    tdTps.textContent = r.tps != null ? `${r.tps.toFixed(1)}` : "—";

    const tdPreview = document.createElement("td");
    tdPreview.textContent = r.response_preview || "—";

    const tdError = document.createElement("td");
    tdError.textContent = r.error || "—";
    if (r.error) tdError.style.color = "#f85149";

    tr.appendChild(tdModel);
    tr.appendChild(tdBackend);
    tr.appendChild(tdStatus);
    tr.appendChild(tdLatency);
    tr.appendChild(tdIn);
    tr.appendChild(tdOut);
    tr.appendChild(tdTps);
    tr.appendChild(tdPreview);
    tr.appendChild(tdError);
    tbody.appendChild(tr);
  }

  if (data.is_running) {
    setStatus(`Running ${data.test_id} — ${ok} ok, ${failed} failed, ${pending} pending…`);
  } else {
    setStatus(`Done ${data.test_id} — ${ok} ok, ${failed} failed (${data.total_models} total).`);
  }
}

function startPoll(testId) {
  if (pollTimer !== null) {
    clearInterval(pollTimer);
  }

  /**
   * @returns {Promise<void>}
   */
  async function poll() {
    try {
      const resp = await fetch(`/smoketest/status?test_id=${testId}`);
      /** @type {SmokeSummary} */
      const data = await resp.json();
      renderResults(data);
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

btnRun.addEventListener("click", () => {
  btnRun.disabled = true;
  setStatus("Starting smoke test…");
  tbody.innerHTML = "";

  const payload = {
    max_models: parseInt(inpMax.value, 10) || 6,
    stream: inpStream.checked,
    prompt: inpPrompt.value || "Say hello in one word.",
  };

  fetch("/smoketest/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
    .then(
      /** @param {Response} r */ (r) => r.json()
    )
    .then(
      /** @param {{ test_id: string }} d */ (d) => {
        currentTestId = d.test_id;
        setStatus(`Started test ${d.test_id}`);
        startPoll(d.test_id);
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
