"use strict";

let state = {
  captures: [],
  total: 0,
  page: 0,
  limit: 50,
  pinnedOnly: false,
  modelFilter: "",
  loading: false,
};

const $ = (sel) => document.querySelector(sel);

function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}

function fmtJSON(val) {
  try {
    return JSON.stringify(JSON.parse(val), null, 2);
  } catch {
    return val;
  }
}

async function loadCaptures() {
  if (state.loading) return;
  state.loading = true;
  $("#status").textContent = "Loading...";

  const params = new URLSearchParams({
    limit: String(state.limit),
    offset: String(state.page * state.limit),
    pinned_only: String(state.pinnedOnly),
  });
  if (state.modelFilter) {
    params.set("model", state.modelFilter);
  }

  try {
    const resp = await fetch(`/api/requests?${params}`);
    const data = await resp.json();

    if (data.error) {
      $("#status").textContent = `Error: ${data.error}`;
      return;
    }

    state.captures = data.captures || [];
    state.total = data.total || 0;
    render();
    $("#status").textContent = `Showing ${state.captures.length} of ${state.total} captures`;
  } catch (err) {
    $("#status").textContent = `Failed to load: ${err.message}`;
  } finally {
    state.loading = false;
  }
}

function render() {
  const tbody = $("#captures-body");
  const empty = $("#empty-state");
  const table = $("#captures-table");

  if (state.captures.length === 0) {
    table.style.display = "none";
    empty.style.display = "block";
  } else {
    table.style.display = "table";
    empty.style.display = "none";
  }

  tbody.innerHTML = state.captures
    .map((c) => {
      const pinnedClass = c.pinned ? "pinned" : "";
      const pinnedLabel = c.pinned ? '✓' : '—';
      const btnClass = c.pinned ? "pin-btn pinned" : "pin-btn";
      const btnLabel = c.pinned ? "Unpin" : "Pin";
      const temp = c.temperature != null ? c.temperature : "—";
      const thinking = c.thinking_effort || "—";
      return `<tr class="capture-row" data-id="${c.id}">
        <td>${fmtTime(c.timestamp)}</td>
        <td><span class="badge">${escHTML(c.public_model)}</span></td>
        <td><span class="badge">${escHTML(c.backend_name)}</span></td>
        <td>${temp}</td>
        <td>${escHTML(thinking)}</td>
        <td><span class="badge ${pinnedClass}">${pinnedLabel}</span></td>
        <td>
          <button class="${btnClass}" data-action="toggle-pin" data-id="${c.id}" title="${btnLabel}">${btnLabel}</button>
          <button class="danger" data-action="delete" data-id="${c.id}" title="Delete">✕</button>
        </td>
      </tr>`;
    })
    .join("");

  // Pagination
  $("#page-info").textContent = `Page ${state.page + 1} (${state.total} total)`;
  $("#btn-prev").disabled = state.page === 0;
  $("#btn-next").disabled = (state.page + 1) * state.limit >= state.total;

  // Event delegation
  tbody.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-action]");
    if (btn) {
      e.stopPropagation();
      const action = btn.dataset.action;
      const id = btn.dataset.id;

      if (action === "toggle-pin") {
        const capture = state.captures.find((c) => c.id === id);
        if (!capture) return;

        const endpoint = capture.pinned ? "unpin" : "pin";
        try {
          await fetch(`/api/requests/${id}/${endpoint}`, { method: "POST" });
          capture.pinned = !capture.pinned;
          render();
        } catch (err) {
          $("#status").textContent = `Action failed: ${err.message}`;
        }
      } else if (action === "delete") {
        if (!confirm("Delete this capture permanently?")) return;
        try {
          await fetch(`/api/requests/${id}`, { method: "DELETE" });
          state.captures = state.captures.filter((c) => c.id !== id);
          state.total--;
          render();
          $("#status").textContent = "Capture deleted";
        } catch (err) {
          $("#status").textContent = `Delete failed: ${err.message}`;
        }
      }
      return;
    }

    // Row click — show detail
    const row = e.target.closest(".capture-row");
    if (row) {
      const id = row.dataset.id;
      showDetail(id);
    }
  });
}

function showDetail(id) {
  const c = state.captures.find((cap) => cap.id === id);
  if (!c) return;

  $("#detail-meta").innerHTML = `
    <span><strong>ID:</strong> ${escHTML(c.id)}</span>
    <span><strong>Time:</strong> ${fmtTime(c.timestamp)}</span>
    <span><strong>Model:</strong> ${escHTML(c.public_model)}</span>
    <span><strong>Backend:</strong> ${escHTML(c.backend_name)} (${escHTML(c.backend_model)})</span>
    <span><strong>Temp:</strong> ${c.temperature ?? "—"}</span>
    <span><strong>Thinking:</strong> ${escHTML(c.thinking_effort || "—")}</span>
    <span><strong>Pinned:</strong> ${c.pinned ? "Yes" : "No"}</span>
  `;
  $("#detail-prompt").textContent = fmtJSON(c.prompt);
  $("#detail-response").textContent = fmtJSON(c.response);
  $("#detail-panel").style.display = "block";
  $("#detail-panel").scrollIntoView({ behavior: "smooth" });
}

function escHTML(s) {
  const div = document.createElement("div");
  div.textContent = s || "";
  return div.innerHTML;
}

// Event listeners
$("#btn-refresh").addEventListener("click", loadCaptures);
$("#btn-prev").addEventListener("click", () => {
  if (state.page > 0) {
    state.page--;
    loadCaptures();
  }
});
$("#btn-next").addEventListener("click", () => {
  state.page++;
  loadCaptures();
});
$("#filter-type").addEventListener("change", (e) => {
  state.pinnedOnly = e.target.value === "pinned";
  state.page = 0;
  loadCaptures();
});
$("#filter-model").addEventListener("input", (e) => {
  state.modelFilter = e.target.value.trim();
  state.page = 0;
  loadCaptures();
});
$("#limit-select").addEventListener("change", (e) => {
  state.limit = parseInt(e.target.value, 10);
  state.page = 0;
  loadCaptures();
});
$("#btn-close-detail").addEventListener("click", () => {
  $("#detail-panel").style.display = "none";
});

// Initial load
loadCaptures();
