# Local LLM Gateway: Enhancement Requirements & AI Agent Working Prompt

> 📁 Save this file as `requirements.md` in the project root.  
> 🔄 We will fill this out together. Once you describe the capabilities you want, I'll help you populate the spec and convert it into an executable AI prompt.

---

## 📖 1. Project Context (Current State)
**Local LLM Gateway** is a FastAPI-based OpenAI-compatible proxy that aggregates multiple local inference backends into a single unified endpoint. It dynamically discovers models, rewrites model names with backend prefixes, supports streaming, and ships with cross-platform system daemon installers (Ubuntu `systemd`, macOS `launchd`).

**Current API Surface:**
- `GET /health`
- `GET /v1/models` (discovery & prefix rewriting)
- `POST /v1/chat/completions`
- `POST /v1/completions`
- `POST /v1/embeddings`

**Core Architectural Constraints:**
- OpenAI API schema compatibility must be preserved exactly.
- Model name routing uses longest-prefix matching on configured upstream `name` fields.
- Configuration supports `config.json`, environment variables, and CLI overrides.
- No external dependencies beyond existing `requirements.txt`/`pyproject.toml`.
- Code must follow existing FastAPI dependency injection, async routing, and testing patterns.

---

## 📋 2. New Capabilities Specification

| # | Capability Name | Description & User Story | Technical Scope | API/Config Changes | Testing Strategy | Status |
|---|---|---|---|---|---|---|
| 1 | **In-Memory Observability Dashboard & Metrics Collector** | As a research operator, I want to observe real-time per-request metrics (input tokens, time-to-first-token, total latency) and running statistics per model and overall via a built-in local dashboard, so I can monitor backend performance without external tooling. | Async metrics collector middleware/hooks, concurrent-safe in-memory store, sliding-window retention, stats aggregation, lightweight HTML/JS dashboard on `/`, optional `/metrics` JSON API | `config.json` additions: `metrics` block (`enabled`, `retention_seconds`, `max_records`, `dashboard_bind`).<br>New routes: `GET /metrics` (JSON), `GET /` (HTML dashboard), `GET /dashboard.js` | Unit tests for metrics collector, stats math, async safety, retention pruning.<br>Integration test for dashboard endpoint returning valid HTML.<br>Proxy mock tests verifying metric capture during streaming/non-streaming flows. | ✅ |
| 2 | **Request Review (Prompt + Response Capture)** | As a research operator, I want to review the content of chat completion requests (prompt messages, response text, temperature, thinking_effort) via a dedicated `/requests` page, with time-based expiry and manual pinning for permanent retention, so I can audit and save interesting interactions. | SQLite-backed `RequestStore` with async CRUD, time-based pruning of unpinned captures, pin/unpin with max-pinned cap, dedicated HTML/JS review page with expandable detail view, model filter, pagination. New `aiosqlite` dependency. | `config.json` additions: `request_review` block (`enabled`, `retention_seconds`, `max_pinned`, `db_path`).<br>New routes: `GET /requests` (HTML), `GET /requests.js`, `GET /api/requests` (JSON list), `POST /api/requests/{id}/pin`, `POST /api/requests/{id}/unpin`, `DELETE /api/requests/{id}`.<br>Capture point: non-streaming `POST /v1/chat/completions` only. | Unit tests for `RequestStore` (add, get, list, pin, unpin, delete, prune, count, filters).<br>Integration test for API endpoints returning correct shapes.<br>Verify capture does not affect response latency or error paths. | ✅ |
| 3 | `[Future Capability]` | `[Describe]` | `[Scope]` | `[Changes]` | `[How to verify]` | 🔲 |

---

## ⚙️ 3. Implementation Constraints & Standards
- **Schema Compliance:** Strictly follow OpenAI API v1.2+ response shapes. Use `openai` pydantic models where applicable.
- **Routing Logic:** Do not break existing `name` prefix mapping. New routing must layer on top.
- **Concurrency:** Maintain existing `asyncio`/httpx streaming pattern. Add circuit breakers/failover only if explicitly requested.
- **Config Evolution:** All new fields must be optional with sensible defaults. Provide migration notes if breaking changes are introduced.
- **Testing:** New features must include `pytest` coverage matching existing style (`test_*_forwarding.py`, `test_config_parsing.py`, etc.).
- **Security:** Pass-through auth is allowed, but never log raw API keys. Validate upstream URLs against `http(s)://`.

---

## 🔄 4. How We'll Work Together
1. You describe the next capability (use Section 2 as a prompt template).
2. I'll populate the specification, draft the AI-ready prompt, and output exact diff-style instructions.
3. We'll iterate feature-by-feature until the enhancement suite is complete.
4. Final output will include: updated `config.example.json`, migration notes, and a finalized `requirements.md`.

💬 **Reply with your next feature idea**, and I'll format it into this spec and generate the exact AI prompt block tailored to your codebase.