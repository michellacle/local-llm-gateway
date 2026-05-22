"use strict";

const statusEl = document.getElementById("status");
const bodyEl = document.getElementById("upstreams-body");

function setStatus(msg, ok) {
  statusEl.textContent = msg;
  statusEl.style.color = ok === true ? "#3fb950" : ok === false ? "#f85149" : "#8b949e";
}

async function loadUpstreams() {
  try {
    const res = await fetch("/admin/upstreams");
    const data = await res.json();
    renderUpstreams(data.upstreams);
  } catch (e) {
    setStatus("Failed to load upstreams: " + e.message, false);
  }
}

function renderUpstreams(upstreams) {
  if (!upstreams.length) {
    bodyEl.innerHTML = '<tr><td colspan="5">No upstreams configured.</td></tr>';
    return;
  }
  bodyEl.innerHTML = upstreams
    .map((u) => {
      const keyBadge = u.api_key ? '<span class="badge">set</span>' : "—";
      return `<tr>
        <td style="font-weight:600;">${esc(u.name)}</td>
        <td style="font-size:.8rem; word-break:break-all;">${esc(u.base_url)}</td>
        <td>${keyBadge}</td>
        <td>${u.timeout_seconds}</td>
        <td><button class="danger" style="padding:.25rem .6rem; font-size:.75rem;" onclick="deleteUpstream('${esc(u.name)}')">Remove</button></td>
      </tr>`;
    })
    .join("");
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

async function testConnection(payload) {
  try {
    const btn = document.activeElement;
    if (btn) { btn.disabled = true; btn.textContent = "Testing…"; }
    const res = await fetch("/admin/upstreams/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();

    if (data.status === "ok") {
      const modelList = data.models.map((m) => esc(m)).join(", ") || "(none)";
      setStatus(`✓ Connected! Found ${data.models_found} model(s): ${modelList}`, true);
    } else if (data.status === "error") {
      setStatus(`✗ Upstream returned HTTP ${data.status_code}: ${data.detail}`, false);
    } else {
      setStatus(`✗ ${data.detail || "Unknown error"}`, false);
    }
  } catch (e) {
    setStatus("✗ Could not reach gateway: " + e.message, false);
  } finally {
    const allBtns = [document.getElementById("btn-test"), document.getElementById("btn-test-url")];
    allBtns.forEach((b) => { if (b) { b.disabled = false; b.textContent = "Test"; } });
  }
}

document.getElementById("btn-test").addEventListener("click", async () => {
  const host = document.getElementById("inp-host").value.trim();
  const scheme = document.getElementById("inp-scheme").value;
  const apiPath = document.getElementById("inp-api-path").value.trim() || "/v1";
  const apiKey = document.getElementById("inp-api-key").value.trim() || null;

  if (!host) return setStatus("Host is required", false);

  await testConnection({ host, scheme, api_path: apiPath, api_key: apiKey });
});

document.getElementById("btn-test-url").addEventListener("click", async () => {
  const baseUrl = document.getElementById("inp-base-url").value.trim();
  const apiKey = document.getElementById("inp-api-key-url").value.trim() || null;

  if (!baseUrl) return setStatus("Base URL is required", false);

  await testConnection({ base_url: baseUrl, api_key: apiKey });
});

document.getElementById("btn-add").addEventListener("click", async () => {
  const name = document.getElementById("inp-name").value.trim();
  const host = document.getElementById("inp-host").value.trim();
  const scheme = document.getElementById("inp-scheme").value;
  const apiPath = document.getElementById("inp-api-path").value.trim() || "/v1";
  const apiKey = document.getElementById("inp-api-key").value.trim() || null;
  const timeout = parseFloat(document.getElementById("inp-timeout").value) || 600;

  if (!name) return setStatus("Name is required", false);
  if (!host) return setStatus("Host is required", false);

  try {
    const res = await fetch("/admin/upstreams", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, host, scheme, api_path: apiPath, api_key: apiKey, timeout_seconds: timeout }),
    });
    const data = await res.json();
    if (res.ok) {
      setStatus(`Added upstream "${name}" (persisted: ${data.persisted})`, true);
      document.getElementById("inp-name").value = "";
      document.getElementById("inp-host").value = "";
      document.getElementById("inp-api-key").value = "";
      await loadUpstreams();
    } else {
      setStatus(data.detail || "Failed to add upstream", false);
    }
  } catch (e) {
    setStatus("Error: " + e.message, false);
  }
});

document.getElementById("btn-add-url").addEventListener("click", async () => {
  const name = document.getElementById("inp-name-url").value.trim();
  const baseUrl = document.getElementById("inp-base-url").value.trim();
  const apiKey = document.getElementById("inp-api-key-url").value.trim() || null;
  const timeout = parseFloat(document.getElementById("inp-timeout-url").value) || 600;

  if (!name) return setStatus("Name is required", false);
  if (!baseUrl) return setStatus("Base URL is required", false);

  try {
    const res = await fetch("/admin/upstreams", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, base_url: baseUrl, api_key: apiKey, timeout_seconds: timeout }),
    });
    const data = await res.json();
    if (res.ok) {
      setStatus(`Added upstream "${name}" (persisted: ${data.persisted})`, true);
      document.getElementById("inp-name-url").value = "";
      document.getElementById("inp-base-url").value = "";
      document.getElementById("inp-api-key-url").value = "";
      await loadUpstreams();
    } else {
      setStatus(data.detail || "Failed to add upstream", false);
    }
  } catch (e) {
    setStatus("Error: " + e.message, false);
  }
});

window.deleteUpstream = async function (name) {
  if (!confirm(`Remove upstream "${name}"?`)) return;
  try {
    const res = await fetch(`/admin/upstreams/${encodeURIComponent(name)}`, { method: "DELETE" });
    const data = await res.json();
    if (res.ok) {
      setStatus(`Removed upstream "${name}"`, true);
      await loadUpstreams();
    } else {
      setStatus(data.detail || "Failed to remove upstream", false);
    }
  } catch (e) {
    setStatus("Error: " + e.message, false);
  }
};

document.getElementById("btn-reload").addEventListener("click", async () => {
  try {
    const res = await fetch("/admin/reload", { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      setStatus(`Reloaded config. Upstreams: ${data.upstreams.join(", ")}`, true);
      await loadUpstreams();
    } else {
      setStatus(data.detail || "Failed to reload config", false);
    }
  } catch (e) {
    setStatus("Error: " + e.message, false);
  }
});

loadUpstreams();
