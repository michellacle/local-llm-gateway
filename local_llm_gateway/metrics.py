from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def _filter_response_headers(http_headers: httpx.Headers) -> dict[str, str]:
    return {
        key: value
        for key, value in http_headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    }


@dataclass
class RequestMetric:
    request_id: str
    timestamp: float
    public_model: str
    backend_model: str
    backend_name: str
    input_tokens: int | None = None
    ttft_ms: float | None = None
    total_time_ms: float | None = None
    response_status: int | None = None
    output_tokens: int | None = None
    bytes_sent: int | None = None


@dataclass
class MetricsStore:
    retention_seconds: float = 900
    max_records: int = 2000
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _records: deque[RequestMetric] = field(default_factory=lambda: deque(maxlen=2000))
    _prune_task: asyncio.Task | None = None
    start_time: float = field(default_factory=time.time)
    _total_output_tokens: int = field(default=0)

    def __post_init__(self):
        self._records = deque(self._records, maxlen=self.max_records)

    async def add(self, metric: RequestMetric) -> None:
        async with self._lock:
            self._records.append(metric)
            self._total_output_tokens += metric.output_tokens or 0
            if self._prune_task is None or self._prune_task.done():
                self._prune_task = asyncio.create_task(self._prune_loop())

    async def _prune_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.retention_seconds / 2)
                await self._prune()
        except asyncio.CancelledError:
            return

    async def _prune(self) -> None:
        cutoff = time.time() - self.retention_seconds
        async with self._lock:
            while self._records and self._records[0].timestamp < cutoff:
                self._records.popleft()

    async def get_all(self) -> list[RequestMetric]:
        async with self._lock:
            return list(self._records)

    async def get_latest(self, limit: int = 500) -> list[RequestMetric]:
        async with self._lock:
            records = list(self._records)
            return records[-limit:]

    async def aggregate(self) -> dict[str, Any]:
        async with self._lock:
            records = list(self._records)
            start = self.start_time
            total_output = self._total_output_tokens

        if not records:
            return _empty_aggregate(start)

        overall = _compute_stats(records)
        per_model: dict[str, list[RequestMetric]] = {}
        for rec in records:
            key = rec.public_model
            if key not in per_model:
                per_model[key] = []
            per_model[key].append(rec)

        breakdown: dict[str, dict[str, Any]] = {}
        for model, recs in per_model.items():
            breakdown[model] = _compute_stats(recs)

        tokens_per_hour, buckets = _compute_throughput(records, start, total_output)

        return {
            "overall": overall,
            "per_model": breakdown,
            "total_records": len(records),
            "tokens_per_hour": tokens_per_hour,
            "buckets": buckets,
        }


def _empty_aggregate(start_time: float = time.time()) -> dict[str, Any]:
    return {
        "overall": {
            "count": 0,
            "avg_ttft_ms": 0,
            "avg_total_time_ms": 0,
            "sum_input_tokens": 0,
            "sum_output_tokens": 0,
            "avg_tps": 0,
        },
        "per_model": {},
        "total_records": 0,
        "tokens_per_hour": 0,
        "buckets": [],
    }


def _compute_throughput(
    records: list[RequestMetric], start_time: float, total_output: int
) -> tuple[float, list[dict[str, Any]]]:
    now = time.time()
    uptime_hours = (now - start_time) / 3600
    if uptime_hours <= 0:
        uptime_hours = 1 / 3600

    tokens_per_hour = round(total_output / uptime_hours, 1)

    bucket_size = 3600
    buckets: dict[int, int] = {}
    for r in records:
        ts = r.timestamp
        bucket_key = int((ts - start_time) // bucket_size)
        buckets[bucket_key] = buckets.get(bucket_key, 0) + (r.output_tokens or 0)

    sorted_keys = sorted(buckets.keys())
    result = [
        {"time": round((k * bucket_size) + start_time, 3), "tokens": buckets[k]}
        for k in sorted_keys
    ]

    return tokens_per_hour, result


def _compute_stats(records: list[RequestMetric]) -> dict[str, Any]:
    count = len(records)
    ttft_values = [r.ttft_ms for r in records if r.ttft_ms is not None]
    total_values = [r.total_time_ms for r in records if r.total_time_ms is not None]
    input_tokens = sum(r.input_tokens or 0 for r in records)
    output_tokens = sum(r.output_tokens or 0 for r in records)

    tps_values = []
    for r in records:
        if r.output_tokens and r.total_time_ms and r.total_time_ms > 0:
            tps_values.append(r.output_tokens / (r.total_time_ms / 1000))

    return {
        "count": count,
        "avg_ttft_ms": round(sum(ttft_values) / len(ttft_values), 2) if ttft_values else 0,
        "avg_total_time_ms": round(sum(total_values) / len(total_values), 2) if total_values else 0,
        "sum_input_tokens": input_tokens,
        "sum_output_tokens": output_tokens,
        "avg_tps": round(sum(tps_values) / len(tps_values), 1) if tps_values else 0,
    }


async def streaming_proxy_with_metrics(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: float,
    store: MetricsStore,
    public_model: str,
    backend_model: str,
    backend_name: str,
    input_tokens: int | None = None,
) -> tuple[asyncio.Event, "StreamingResponse", RequestMetric]:
    from fastapi.responses import StreamingResponse

    request_id = str(uuid.uuid4())
    start_time = time.time()
    first_chunk_time: float | None = None
    total_bytes = 0
    status_code = 200

    metric = RequestMetric(
        request_id=request_id,
        timestamp=start_time,
        public_model=public_model,
        backend_model=backend_model,
        backend_name=backend_name,
        input_tokens=input_tokens,
    )

    ready = asyncio.Event()
    client = httpx.AsyncClient(timeout=timeout_seconds)
    http_request = client.build_request("POST", url, headers=headers, json=payload)

    try:
        response = await client.send(http_request, stream=True)
        status_code = response.status_code
    except httpx.RequestError as exc:
        await client.aclose()
        metric.response_status = 502
        metric.total_time_ms = round((time.time() - start_time) * 1000, 2)
        await store.add(metric)
        raise type(exc)(f"Upstream request failed: {exc}") from exc

    response_headers = _filter_response_headers(response.headers)

    def extract_tokens(obj: dict[str, Any]) -> tuple[int | None, int | None]:
        usage_node = obj.get("usage")
        if usage_node:
            return usage_node.get("prompt_tokens"), usage_node.get("completion_tokens")
        if obj.get("prompt_eval_count") is not None:
            return obj.get("prompt_eval_count"), obj.get("eval_count")
        return None, None

    async def body() -> AsyncIterator[bytes]:
        nonlocal first_chunk_time, total_bytes, metric
        sse_buffer = ""
        try:
            async for chunk in response.aiter_bytes():
                if first_chunk_time is None:
                    first_chunk_time = time.time()
                    metric.ttft_ms = round((first_chunk_time - start_time) * 1000, 2)
                    ready.set()
                total_bytes += len(chunk)
                sse_buffer += chunk.decode("utf-8", errors="replace")
                while "\n" in sse_buffer:
                    line, sse_buffer = sse_buffer.split("\n", 1)
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        continue
                    try:
                        obj = json.loads(data)
                        in_t, out_t = extract_tokens(obj)
                        if in_t is not None:
                            metric.input_tokens = in_t
                        if out_t is not None:
                            metric.output_tokens = out_t
                    except (json.JSONDecodeError, KeyError):
                        pass
                yield chunk
        finally:
            remaining = sse_buffer
            while "\n" in remaining:
                line, remaining = remaining.split("\n", 1)
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    continue
                try:
                    obj = json.loads(data)
                    in_t, out_t = extract_tokens(obj)
                    if in_t is not None:
                        metric.input_tokens = in_t
                    if out_t is not None:
                        metric.output_tokens = out_t
                except (json.JSONDecodeError, KeyError):
                    pass
            elapsed = time.time() - start_time
            metric.total_time_ms = round(elapsed * 1000, 2)
            metric.bytes_sent = total_bytes
            metric.response_status = status_code
            await store.add(metric)
            await response.aclose()
            await client.aclose()

    stream_response = StreamingResponse(
        body(),
        status_code=status_code,
        headers=response_headers,
        media_type=response.headers.get("content-type", "text/event-stream"),
    )
    return ready, stream_response, metric
