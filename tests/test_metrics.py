from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from local_llm_gateway.app import create_app
from local_llm_gateway.config import AppConfig, MetricsConfig, Upstream
from local_llm_gateway.metrics import MetricsStore, RequestMetric, _compute_stats


@pytest.fixture
def app_config():
    return AppConfig(
        upstreams=(
            Upstream(name="test", base_url="http://localhost:9999/v1"),
        ),
        metrics=MetricsConfig(enabled=True, retention_seconds=900, max_records=2000),
    )


@pytest.fixture
def app(app_config):
    return create_app(app_config)


@pytest.fixture
def store():
    return MetricsStore(retention_seconds=2, max_records=100)


def test_metrics_capture_non_streaming(app):
    """Non-streaming response captures input/output tokens, total_time, bytes_sent."""
    async def fake_post(self, url, **kwargs):
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                },
            },
            request=httpx.Request("POST", url),
        )

    with patch.object(httpx.AsyncClient, "post", fake_post):
        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "test-mymodel",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    assert resp.status_code == 200
    metrics_resp = client.get("/metrics")
    assert metrics_resp.status_code == 200
    data = metrics_resp.json()
    assert data["total_records"] == 1
    recent = data["recent_metrics"]
    assert len(recent) == 1
    assert recent[0]["input_tokens"] == 10
    assert recent[0]["output_tokens"] == 20
    assert recent[0]["total_time_ms"] is not None
    assert recent[0]["total_time_ms"] >= 0
    assert recent[0]["bytes_sent"] > 0
    assert recent[0]["response_status"] == 200
    assert recent[0]["public_model"] == "test-mymodel"
    assert recent[0]["backend_model"] == "mymodel"
    assert recent[0]["backend_name"] == "test"


def test_metrics_capture_streaming(app):
    """Streaming response captures ttft_ms, total_time_ms, bytes_sent."""
    chunks = [b'data: {"choices":[]}\n\n', b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n', b'data: [DONE]\n\n']

    async def fake_send(self, request, stream=False):
        if not stream:
            raise ValueError("Expected streaming request")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = httpx.Headers({"content-type": "text/event-stream"})

        async def aiter_bytes():
            for chunk in chunks:
                await asyncio.sleep(0.01)
                yield chunk

        mock_response.aiter_bytes = aiter_bytes
        mock_response.aclose = AsyncMock()
        return mock_response

    with patch.object(httpx.AsyncClient, "send", fake_send):
        client = TestClient(app)
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "test-streammodel",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
            },
        ) as resp:
            assert resp.status_code == 200
            content = resp.read()
            assert len(content) > 0

    metrics_resp = client.get("/metrics")
    assert metrics_resp.status_code == 200
    data = metrics_resp.json()
    assert data["total_records"] == 1
    recent = data["recent_metrics"]
    assert len(recent) == 1
    assert recent[0]["ttft_ms"] is not None
    assert recent[0]["ttft_ms"] >= 0
    assert recent[0]["total_time_ms"] is not None
    assert recent[0]["total_time_ms"] >= 0
    assert recent[0]["bytes_sent"] > 0
    assert recent[0]["public_model"] == "test-streammodel"
    assert recent[0]["backend_name"] == "test"


def test_metrics_stats_aggregation():
    """Aggregate computes correct averages per model and overall."""
    store = MetricsStore()

    now = time.time()
    store._records.append(RequestMetric(
        request_id="r1", timestamp=now, public_model="a", backend_model="a", backend_name="u",
        input_tokens=10, ttft_ms=100, total_time_ms=500, output_tokens=20, bytes_sent=100,
    ))
    store._records.append(RequestMetric(
        request_id="r2", timestamp=now, public_model="a", backend_model="a", backend_name="u",
        input_tokens=20, ttft_ms=200, total_time_ms=600, output_tokens=30, bytes_sent=200,
    ))
    store._records.append(RequestMetric(
        request_id="r3", timestamp=now, public_model="b", backend_model="b", backend_name="u",
        input_tokens=5, ttft_ms=50, total_time_ms=250, output_tokens=10, bytes_sent=50,
    ))

    result = asyncio.run(store.aggregate())

    assert result["total_records"] == 3
    overall = result["overall"]
    assert overall["count"] == 3
    assert overall["avg_ttft_ms"] == pytest.approx(116.67, abs=1)
    assert overall["avg_total_time_ms"] == pytest.approx(450, abs=1)
    assert overall["sum_input_tokens"] == 35
    assert overall["sum_output_tokens"] == 60

    per_model = result["per_model"]
    assert "a" in per_model
    assert "b" in per_model
    assert per_model["a"]["count"] == 2
    assert per_model["a"]["avg_ttft_ms"] == pytest.approx(150, abs=1)
    assert per_model["b"]["count"] == 1
    assert per_model["b"]["avg_ttft_ms"] == pytest.approx(50, abs=1)


def test_metrics_retention():
    """Records older than retention_seconds are pruned."""
    store = MetricsStore(retention_seconds=1, max_records=100)

    old_time = time.time() - 10
    store._records.append(RequestMetric(
        request_id="old", timestamp=old_time, public_model="a", backend_model="a", backend_name="u",
    ))
    store._records.append(RequestMetric(
        request_id="new", timestamp=time.time(), public_model="a", backend_model="a", backend_name="u",
    ))

    async def run():
        await store._prune()
        records = await store.get_all()
        return records

    records = asyncio.run(run())
    assert len(records) == 1
    assert records[0].request_id == "new"


def test_metrics_async_safety():
    """Concurrent adds don't corrupt the store."""
    store = MetricsStore(max_records=10000)

    async def add_many(start: int):
        for i in range(100):
            await store.add(RequestMetric(
                request_id=f"r-{start + i}",
                timestamp=time.time(),
                public_model="a",
                backend_model="a",
                backend_name="u",
            ))

    async def run():
        await asyncio.gather(
            add_many(0),
            add_many(100),
            add_many(200),
            add_many(300),
        )
        records = await store.get_all()
        return len(records)

    count = asyncio.run(run())
    assert count == 400


def test_metrics_config_defaults():
    """MetricsConfig defaults are correct."""
    cfg = MetricsConfig()
    assert cfg.enabled is True
    assert cfg.retention_seconds == 900
    assert cfg.max_records == 2000
    assert cfg.dashboard_bind == "127.0.0.1"


def test_metrics_config_from_file(tmp_path):
    """Metrics block is parsed from config.json."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({
            "upstreams": [{"name": "u1", "host": "h1:11434"}],
            "metrics": {
                "enabled": False,
                "retention_seconds": 600,
                "max_records": 500,
                "dashboard_bind": "0.0.0.0",
            },
        })
    )

    cfg = AppConfig.from_file(config_path)
    assert cfg.metrics.enabled is False
    assert cfg.metrics.retention_seconds == 600
    assert cfg.metrics.max_records == 500
    assert cfg.metrics.dashboard_bind == "0.0.0.0"


def test_dashboard_endpoint(app):
    """GET / returns HTML dashboard."""
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Local LLM Gateway" in resp.text


def test_dashboard_js_endpoint(app):
    """GET /dashboard.js returns JavaScript."""
    client = TestClient(app)
    resp = client.get("/dashboard.js")
    assert resp.status_code == 200
    assert "application/javascript" in resp.headers["content-type"]
    assert "REFRESH_MS" in resp.text


def test_metrics_disabled(app_config):
    """When metrics are disabled, /metrics returns error."""
    app_config_with_metrics_disabled = AppConfig(
        upstreams=app_config.upstreams,
        metrics=MetricsConfig(enabled=False),
    )
    app_no_metrics = create_app(app_config_with_metrics_disabled)
    client = TestClient(app_no_metrics)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data


def test_metrics_empty_state(app):
    """Empty metrics store returns zeroed aggregate."""
    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["overall"]["count"] == 0
    assert data["overall"]["avg_ttft_ms"] == 0
    assert data["per_model"] == {}
    assert data["recent_metrics"] == []


def test_metrics_max_records_enforced():
    """Deque maxlen caps the record count."""
    store = MetricsStore(max_records=5)

    async def run():
        for i in range(10):
            await store.add(RequestMetric(
                request_id=f"r-{i}",
                timestamp=time.time(),
                public_model="a",
                backend_model="a",
                backend_name="u",
            ))
        records = await store.get_all()
        return len(records)

    count = asyncio.run(run())
    assert count == 5


def test_compute_stats_empty():
    """_compute_stats handles empty list gracefully."""
    result = _compute_stats([])
    assert result["count"] == 0
    assert result["avg_ttft_ms"] == 0
    assert result["avg_total_time_ms"] == 0


def test_compute_stats_single():
    """_compute_stats handles single record."""
    records = [RequestMetric(
        request_id="r1", timestamp=time.time(), public_model="a", backend_model="a", backend_name="u",
        input_tokens=10, ttft_ms=100, total_time_ms=500, output_tokens=20, bytes_sent=100,
    )]
    result = _compute_stats(records)
    assert result["count"] == 1
    assert result["avg_ttft_ms"] == 100
    assert result["avg_total_time_ms"] == 500
    assert result["sum_input_tokens"] == 10
    assert result["sum_output_tokens"] == 20


def test_proxy_unknown_model_returns_404(app):
    """Unknown model returns 404 with helpful detail."""
    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "unknown-model",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 404
    data = resp.json()
    assert "expected_format" in data["detail"]


def test_proxy_missing_model_returns_400(app):
    """Missing model field returns 400."""
    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 400


def test_proxy_invalid_json_returns_400(app):
    """Invalid JSON body returns 400."""
    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        content="not json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400
