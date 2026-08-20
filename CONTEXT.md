# Local LLM Gateway

An OpenAI-compatible proxy that aggregates multiple local inference backends into a single unified endpoint, with observability and request review capabilities.

## Language

**Upstream**:
A configured inference backend (e.g., Ollama, vLLM) with a name, base URL, and optional API key. The gateway routes requests to upstreams based on model name prefix matching.
_Avoid_: Backend, server, provider

**Public Model**:
The model name as seen by the client, formatted as `<upstream_name>-<model_id>`. This is what appears in the `model` field of requests to the gateway.
_Avoid_: Full model name, prefixed model

**Backend Model**:
The model name as understood by the upstream, with the upstream prefix stripped. This is what the gateway sends to the upstream in the proxied request.
_Avoid_: Raw model name, upstream model

**Request Metric**:
A timing and usage record captured for every proxied request: latency, TTFT, token counts, status code. Stored in-memory with time-based retention.
_Avoid_: Performance metric, request stats

**Request Capture**:
A persisted record of a chat completion request's content: the prompt (messages), response (choices), temperature, and thinking_effort. Stored in SQLite with time-based expiry and manual pinning.
_Avoid_: Request log, conversation log, prompt log

**Pin**:
Marking a request capture for permanent retention, exempting it from time-based expiry. Pinned captures survive both pruning and gateway restarts.
_Avoid_: Bookmark, save, keep

**Expiry**:
The time window after which unpinned request captures are automatically pruned (default: 1 hour). Configurable via `request_review.retention_seconds`.
_Avoid_: TTL, retention period
