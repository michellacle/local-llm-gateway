from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from .config import (
    AppConfig,
    MetricsConfig,
    RequestReviewConfig,
    Upstream,
    add_upstream_to_config,
    load_config,
    remove_upstream_from_config,
)
from .metrics import MetricsStore, RequestMetric, streaming_proxy_with_metrics
from .request_store import RequestStore
from .router import UnknownModelError, UpstreamRegistry
from .smoketest import BenchmarkRunner, SmokeTestRunner

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

OPENAI_ENDPOINTS = {
    "chat_completions": "/chat/completions",
    "completions": "/completions",
    "embeddings": "/embeddings",
}


def create_app(config: AppConfig | None = None, config_path: str | None = None) -> FastAPI:
    app_config = config or load_config()
    registry = UpstreamRegistry(app_config.upstreams)

    metrics_store: MetricsStore | None = None
    if app_config.metrics.enabled:
        metrics_store = MetricsStore(
            retention_seconds=app_config.metrics.retention_seconds,
            max_records=app_config.metrics.max_records,
        )

    request_store: RequestStore | None = None
    if app_config.request_review.enabled:
        request_store = RequestStore(
            db_path=app_config.request_review.db_path,
            retention_seconds=app_config.request_review.retention_seconds,
            max_pinned=app_config.request_review.max_pinned,
        )

    @asynccontextmanager
    async def lifespan(app_ref: FastAPI) -> AsyncIterator[None]:
        if request_store is not None:
            await request_store.connect()
        yield
        if request_store is not None:
            await request_store.close()

    app = FastAPI(
        title="Local LLM Gateway",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.registry = registry
    app.state.upstreams = app_config.upstreams
    app.state.config_path = config_path

    if app_config.metrics.enabled:
        app.state.metrics_store = metrics_store

    smoke_runner = SmokeTestRunner(app_config)
    app.state.smoke_runner = smoke_runner

    benchmark_runner = BenchmarkRunner(app_config)
    app.state.benchmark_runner = benchmark_runner

    @asynccontextmanager
    async def lifespan(app_ref: FastAPI) -> AsyncIterator[None]:
        if request_store is not None:
            await request_store.connect()
        yield
        if request_store is not None:
            await request_store.close()

    # Re-create app with lifespan (FastAPI requires this at construction time,
    # so we patch the router's lifespan instead).
    app.router.lifespan_context = lifespan

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "upstreams": registry.list_upstreams()}

    @app.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        return await discover_models(app.state.upstreams)

    @app.get("/metrics")
    async def get_metrics() -> dict[str, Any]:
        if metrics_store is None:
            return {"error": "Metrics are disabled"}
        stats = await metrics_store.aggregate()
        latest = await metrics_store.get_latest(500)
        stats["recent_metrics"] = [
            {
                "request_id": m.request_id,
                "timestamp": m.timestamp,
                "public_model": m.public_model,
                "backend_model": m.backend_model,
                "backend_name": m.backend_name,
                "input_tokens": m.input_tokens,
                "ttft_ms": m.ttft_ms,
                "total_time_ms": m.total_time_ms,
                "response_status": m.response_status,
                "output_tokens": m.output_tokens,
                "bytes_sent": m.bytes_sent,
            }
            for m in latest
        ]
        return stats

    static_dir = Path(__file__).resolve().parent / "static"

    @app.get("/dashboard.js")
    async def dashboard_js() -> FileResponse:
        js_path = static_dir / "dashboard.js"
        if js_path.exists():
            return FileResponse(str(js_path), media_type="application/javascript")
        return Response(content=";", media_type="application/javascript")

    @app.get("/upstreams.js")
    async def upstreams_js() -> FileResponse:
        js_path = static_dir / "upstreams.js"
        if js_path.exists():
            return FileResponse(str(js_path), media_type="application/javascript")
        return Response(content=";", media_type="application/javascript")

    @app.get("/upstreams")
    async def upstreams_page() -> FileResponse:
        html_path = static_dir / "upstreams.html"
        if html_path.exists():
            return FileResponse(str(html_path), media_type="text/html")
        return Response(
            content="<html><body><h1>Upstreams</h1><p>Not available</p></body></html>",
            media_type="text/html",
        )

    @app.get("/")
    async def dashboard() -> FileResponse:
        html_path = static_dir / "dashboard.html"
        if html_path.exists():
            return FileResponse(str(html_path), media_type="text/html")
        return Response(
            content="<html><body><h1>Dashboard</h1><p>Metrics available at /metrics</p></body></html>",
            media_type="text/html",
        )

    @app.get("/smoketest.js")
    async def smoketest_js() -> FileResponse:
        js_path = static_dir / "smoketest.js"
        if js_path.exists():
            return FileResponse(str(js_path), media_type="application/javascript")
        return Response(content=";", media_type="application/javascript")

    @app.get("/smoketest")
    async def smoketest_page() -> FileResponse:
        html_path = static_dir / "smoketest.html"
        if html_path.exists():
            return FileResponse(str(html_path), media_type="text/html")
        return Response(
            content="<html><body><h1>Smoke Test</h1><p>Not available</p></body></html>",
            media_type="text/html",
        )

    @app.post("/smoketest/run")
    async def run_smoketest(request: Request) -> dict[str, Any]:
        payload = await request.json()
        gateway_url = f"{request.url.scheme}://{request.url.netloc}"
        smoke_runner.set_gateway_url(gateway_url)
        test_id = await smoke_runner.start_run(
            max_models=payload.get("max_models", 10),
            prompt=payload.get("prompt"),
            stream=bool(payload.get("stream", False)),
        )
        return {"test_id": test_id}

    @app.get("/smoketest/status")
    async def smoketest_status(
        test_id: str | None = None,
    ) -> dict[str, Any]:
        if test_id:
            run = await smoke_runner.get_run(test_id)
        else:
            run = await smoke_runner.get_latest_run()
        if run is None:
            return {"error": "No smoke test run found"}
        return run.summary

    @app.get("/benchmark.js")
    async def benchmark_js() -> FileResponse:
        js_path = static_dir / "benchmark.js"
        if js_path.exists():
            return FileResponse(str(js_path), media_type="application/javascript")
        return Response(content=";", media_type="application/javascript")

    @app.get("/benchmark")
    async def benchmark_page() -> FileResponse:
        html_path = static_dir / "benchmark.html"
        if html_path.exists():
            return FileResponse(str(html_path), media_type="text/html")
        return Response(
            content="<html><body><h1>Benchmark</h1><p>Not available</p></body></html>",
            media_type="text/html",
        )

    @app.post("/benchmark/run")
    async def run_benchmark(request: Request) -> dict[str, Any]:
        payload = await request.json()
        gateway_url = f"{request.url.scheme}://{request.url.netloc}"
        benchmark_runner.set_gateway_url(gateway_url)
        run_id = await benchmark_runner.start_run(
            model=payload.get("model", ""),
            prompt=payload.get("prompt", "Say hello in one word."),
            iterations=payload.get("iterations", 5),
            stream=bool(payload.get("stream", False)),
            max_tokens=payload.get("max_tokens", 256),
        )
        return {"run_id": run_id}

    @app.get("/benchmark/status")
    async def benchmark_status(
        run_id: str | None = None,
    ) -> dict[str, Any]:
        if run_id:
            run = await benchmark_runner.get_run(run_id)
        else:
            run = await benchmark_runner.get_latest_run()
        if run is None:
            return {"error": "No benchmark run found"}
        return run.summary

    # --- Request Review Routes ---

    @app.get("/requests.js")
    async def requests_js() -> FileResponse:
        js_path = static_dir / "requests.js"
        if js_path.exists():
            return FileResponse(str(js_path), media_type="application/javascript")
        return Response(content=";", media_type="application/javascript")

    @app.get("/requests")
    async def requests_page() -> FileResponse:
        html_path = static_dir / "requests.html"
        if html_path.exists():
            return FileResponse(str(html_path), media_type="text/html")
        return Response(
            content="<html><body><h1>Request Review</h1><p>Not available</p></body></html>",
            media_type="text/html",
        )

    @app.get("/api/requests")
    async def api_list_requests(
        limit: int = 50,
        offset: int = 0,
        pinned_only: bool = False,
        model: str | None = None,
    ) -> dict[str, Any]:
        if request_store is None:
            return {"error": "Request review is disabled"}

        captures = await request_store.list_captures(
            limit=limit, offset=offset, pinned_only=pinned_only, model=model
        )
        total = await request_store.count_captures(pinned_only=pinned_only)

        return {
            "captures": [
                {
                    "id": c.id,
                    "timestamp": c.timestamp,
                    "public_model": c.public_model,
                    "backend_model": c.backend_model,
                    "backend_name": c.backend_name,
                    "prompt": c.prompt,
                    "response": c.response,
                    "temperature": c.temperature,
                    "thinking_effort": c.thinking_effort,
                    "pinned": c.pinned,
                }
                for c in captures
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @app.post("/api/requests/{capture_id}/pin")
    async def api_pin_request(capture_id: str) -> dict[str, Any]:
        if request_store is None:
            return {"error": "Request review is disabled"}

        success = await request_store.pin(capture_id)
        if not success:
            raise HTTPException(
                status_code=400,
                detail="Could not pin — max pinned limit reached or capture not found",
            )
        return {"status": "pinned", "id": capture_id}

    @app.post("/api/requests/{capture_id}/unpin")
    async def api_unpin_request(capture_id: str) -> dict[str, Any]:
        if request_store is None:
            return {"error": "Request review is disabled"}

        success = await request_store.unpin(capture_id)
        if not success:
            raise HTTPException(
                status_code=400,
                detail="Could not unpin — capture not found or already unpinned",
            )
        return {"status": "unpinned", "id": capture_id}

    @app.delete("/api/requests/{capture_id}")
    async def api_delete_request(capture_id: str) -> dict[str, Any]:
        if request_store is None:
            return {"error": "Request review is disabled"}

        success = await request_store.delete(capture_id)
        if not success:
            raise HTTPException(
                status_code=404,
                detail="Capture not found",
            )
        return {"status": "deleted", "id": capture_id}

    @app.get("/admin/upstreams")
    async def admin_list_upstreams() -> dict[str, Any]:
        return {
            "upstreams": [
                {
                    "name": u.name,
                    "base_url": u.base_url,
                    "api_key": u.api_key,
                    "timeout_seconds": u.timeout_seconds,
                }
                for u in app.state.upstreams
            ]
        }

    @app.post("/admin/upstreams")
    async def admin_add_upstream(request: Request) -> dict[str, Any]:
        payload = await request.json()
        name = str(payload.get("name", "")).strip()
        if not name:
            raise HTTPException(status_code=400, detail="'name' is required")

        if name in registry.list_upstreams():
            raise HTTPException(status_code=409, detail=f"Upstream '{name}' already exists")

        host = payload.get("host")
        base_url = payload.get("base_url")
        if not host and not base_url:
            raise HTTPException(status_code=400, detail="Either 'host' or 'base_url' is required")

        scheme = str(payload.get("scheme", "http")).strip() or "http"
        api_path = str(payload.get("api_path", "/v1")).strip() or "/v1"
        if not api_path.startswith("/"):
            api_path = f"/{api_path}"
        api_key = payload.get("api_key")
        timeout_seconds = float(payload.get("timeout_seconds", 600.0))

        computed_base_url: str
        if base_url:
            from urllib.parse import urlparse as _urlparse

            parsed = _urlparse(str(base_url))
            if not parsed.scheme or not parsed.netloc:
                raise HTTPException(status_code=400, detail="Invalid 'base_url'")
            computed_base_url = str(base_url).rstrip("/")
        else:
            if not api_path.startswith("/"):
                api_path = f"/{api_path}"
            computed_base_url = f"{scheme}://{host}{api_path}".rstrip("/")

        new_upstream = Upstream(
            name=name,
            base_url=computed_base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )

        registry.add_upstream(new_upstream)
        app.state.upstreams = (*app.state.upstreams, new_upstream)

        cp = app.state.config_path
        if cp:
            add_upstream_to_config(
                cp,
                name=name,
                host=host,
                base_url=base_url,
                scheme=scheme,
                api_path=api_path,
                api_key=api_key,
                timeout_seconds=timeout_seconds,
            )

        return {
            "status": "added",
            "upstream": {
                "name": new_upstream.name,
                "base_url": new_upstream.base_url,
                "timeout_seconds": new_upstream.timeout_seconds,
            },
            "persisted": bool(cp),
        }

    @app.delete("/admin/upstreams/{upstream_name}")
    async def admin_delete_upstream(upstream_name: str) -> dict[str, Any]:
        if upstream_name not in registry.list_upstreams():
            raise HTTPException(status_code=404, detail=f"Upstream '{upstream_name}' not found")

        if len(registry.list_upstreams()) <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove the last upstream")

        registry.remove_upstream(upstream_name)
        app.state.upstreams = tuple(u for u in app.state.upstreams if u.name != upstream_name)

        cp = app.state.config_path
        if cp:
            remove_upstream_from_config(cp, upstream_name)

        return {
            "status": "removed",
            "upstream": upstream_name,
            "persisted": bool(cp),
        }

    @app.post("/admin/upstreams/test")
    async def admin_test_upstream(request: Request) -> dict[str, Any]:
        payload = await request.json()
        host = payload.get("host")
        base_url = payload.get("base_url")
        scheme = str(payload.get("scheme", "http")).strip() or "http"
        api_path = str(payload.get("api_path", "/v1")).strip() or "/v1"
        api_key = payload.get("api_key")
        timeout = float(payload.get("timeout_seconds", 10))

        if base_url:
            from urllib.parse import urlparse as _urlparse

            parsed = _urlparse(str(base_url))
            if not parsed.scheme or not parsed.netloc:
                raise HTTPException(status_code=400, detail="Invalid 'base_url'")
            url = str(base_url).rstrip("/")
        elif host:
            if not api_path.startswith("/"):
                api_path = f"/{api_path}"
            url = f"{scheme}://{host}{api_path}".rstrip("/")
        else:
            raise HTTPException(status_code=400, detail="Either 'host' or 'base_url' is required")

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(f"{url}/models", headers=headers)
                resp.raise_for_status()
                models = resp.json().get("data", [])
                return {
                    "status": "ok",
                    "url": url,
                    "models_found": len(models),
                    "models": [m.get("id", "") for m in models[:20]],
                }
        except httpx.ConnectError as exc:
            raise HTTPException(status_code=502, detail=f"Connection refused: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail=f"Request timed out: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            return {
                "status": "error",
                "url": url,
                "status_code": exc.response.status_code,
                "detail": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
                "models_found": 0,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Request failed: {exc}") from exc

    @app.post("/admin/reload")
    async def admin_reload() -> dict[str, Any]:
        cp = app.state.config_path
        if not cp:
            raise HTTPException(status_code=500, detail="No config path set, cannot reload")

        try:
            new_config = load_config(cp)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to load config: {exc}") from exc

        registry.reload(new_config.upstreams)
        app.state.upstreams = new_config.upstreams

        if new_config.metrics.enabled and app.state.metrics_store is None:
            app.state.metrics_store = MetricsStore(
                retention_seconds=new_config.metrics.retention_seconds,
                max_records=new_config.metrics.max_records,
            )
        elif not new_config.metrics.enabled:
            app.state.metrics_store = None

        app.state.smoke_runner = SmokeTestRunner(new_config)
        app.state.benchmark_runner = BenchmarkRunner(new_config)

        return {
            "status": "reloaded",
            "upstreams": registry.list_upstreams(),
        }

    @app.post("/v1/chat/completions", response_model=None)
    async def chat_completions(request: Request) -> StreamingResponse | Response:
        return await proxy_openai_request(
            request,
            registry,
            OPENAI_ENDPOINTS["chat_completions"],
            metrics_store,
            request_store=request_store,
        )

    @app.post("/v1/completions", response_model=None)
    async def completions(request: Request) -> StreamingResponse | Response:
        return await proxy_openai_request(
            request, registry, OPENAI_ENDPOINTS["completions"], metrics_store
        )

    @app.post("/v1/embeddings", response_model=None)
    async def embeddings(request: Request) -> StreamingResponse | Response:
        return await proxy_openai_request(
            request, registry, OPENAI_ENDPOINTS["embeddings"], metrics_store
        )

    return app


async def discover_models(upstreams: tuple[Upstream, ...]) -> dict[str, Any]:
    data: list[dict[str, Any]] = []
    errors: dict[str, str] = {}

    async with httpx.AsyncClient() as client:
        for upstream in upstreams:
            try:
                response = await client.get(
                    f"{upstream.normalized_base_url}/models",
                    headers=auth_headers(upstream),
                    timeout=upstream.timeout_seconds,
                )
                response.raise_for_status()
                upstream_models = response.json().get("data", [])
            except Exception as exc:
                errors[upstream.name] = str(exc)
                continue

            for model in upstream_models:
                model_id = model.get("id") if isinstance(model, dict) else None
                if not model_id:
                    continue
                public_model = dict(model)
                public_model["id"] = f"{upstream.name}-{model_id}"
                public_model.setdefault("object", "model")
                public_model.setdefault("created", 0)
                public_model["owned_by"] = upstream.name
                data.append(public_model)

    result: dict[str, Any] = {"object": "list", "data": data}
    if errors:
        result["warnings"] = [
            {"upstream": upstream, "message": message}
            for upstream, message in sorted(errors.items())
        ]
    return result


async def proxy_openai_request(
    request: Request,
    registry: UpstreamRegistry,
    upstream_path: str,
    metrics_store: MetricsStore | None = None,
    request_store: RequestStore | None = None,
) -> StreamingResponse | Response:
    import time as _time

    try:
        payload = await request.json()
    except Exception as exc:  # FastAPI turns JSON decode errors into opaque 500s here.
        raise HTTPException(status_code=400, detail="Request body must be valid JSON") from exc

    model = payload.get("model")
    if not isinstance(model, str) or not model:
        raise HTTPException(status_code=400, detail="Request body must include a string 'model'")

    try:
        upstream, upstream_model = registry.get_upstream(model)
    except UnknownModelError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "message": str(exc),
                "expected_format": "<upstream>-<model>",
                "available_upstreams": exc.available_upstreams,
            },
        ) from exc

    upstream_payload = dict(payload)
    upstream_payload["model"] = upstream_model

    headers = upstream_headers(request, upstream)
    url = f"{upstream.normalized_base_url}{upstream_path}"
    request_start = _time.time()

    # Extract capture fields for chat completions
    capture_prompt: str | None = None
    capture_temperature: float | None = None
    capture_thinking_effort: str | None = None
    if request_store is not None and upstream_path == OPENAI_ENDPOINTS["chat_completions"]:
        messages = payload.get("messages")
        if messages is not None:
            capture_prompt = json.dumps(messages, ensure_ascii=False)
        capture_temperature = payload.get("temperature")
        # thinking_effort can be in extra_body or directly in payload
        capture_thinking_effort = payload.get("thinking_effort")

    if bool(payload.get("stream")):
        if metrics_store is not None:
            _, stream_resp, _ = await streaming_proxy_with_metrics(
                url,
                headers,
                upstream_payload,
                upstream.timeout_seconds,
                metrics_store,
                model,
                upstream_model,
                upstream.name,
            )
            return stream_resp
        return await streaming_proxy_response(
            url, headers, upstream_payload, upstream.timeout_seconds
        )

    async with httpx.AsyncClient(timeout=upstream.timeout_seconds) as client:
        try:
            response = await client.post(url, headers=headers, json=upstream_payload)
        except httpx.RequestError as exc:
            if metrics_store is not None:
                await metrics_store.add(
                    RequestMetric(
                        request_id="",
                        timestamp=request_start,
                        public_model=model,
                        backend_model=upstream_model,
                        backend_name=upstream.name,
                        response_status=502,
                        total_time_ms=round((_time.time() - request_start) * 1000, 2),
                    )
                )
            return upstream_unavailable(exc, upstream)

    # Capture response content for request review
    if request_store is not None and capture_prompt is not None:
        resp_body = response.content
        try:
            resp_json = json.loads(resp_body) if resp_body else {}
            choices = resp_json.get("choices", [])
            response_text = json.dumps(
                [c.get("message", {}).get("content", "") for c in choices],
                ensure_ascii=False,
            )
            await request_store.add(
                public_model=model,
                backend_model=upstream_model,
                backend_name=upstream.name,
                prompt=capture_prompt,
                response=response_text,
                temperature=capture_temperature,
                thinking_effort=capture_thinking_effort,
            )
        except Exception:
            pass  # Don't let capture failures affect the response

    if metrics_store is not None:
        elapsed = _time.time() - request_start
        resp_body = response.content
        try:
            resp_json = json.loads(resp_body) if resp_body else {}
        except Exception:
            resp_json = {}
        usage = resp_json.get("usage", {}) if isinstance(resp_json, dict) else {}
        await metrics_store.add(
            RequestMetric(
                request_id="",
                timestamp=request_start,
                public_model=model,
                backend_model=upstream_model,
                backend_name=upstream.name,
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
                response_status=response.status_code,
                total_time_ms=round(elapsed * 1000, 2),
                bytes_sent=len(resp_body),
                ttft_ms=round(elapsed * 1000, 2),
            )
        )

    return Response(
        status_code=response.status_code,
        content=response.content,
        headers=filtered_response_headers(response.headers),
        media_type=response.headers.get("content-type"),
    )


async def streaming_proxy_response(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout_seconds: float
) -> StreamingResponse:
    client = httpx.AsyncClient(timeout=timeout_seconds)
    request = client.build_request("POST", url, headers=headers, json=payload)
    try:
        response = await client.send(request, stream=True)
    except httpx.RequestError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {exc}") from exc

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return StreamingResponse(
        body(),
        status_code=response.status_code,
        headers=filtered_response_headers(response.headers),
        media_type=response.headers.get("content-type", "text/event-stream"),
    )


def auth_headers(upstream: Upstream) -> dict[str, str]:
    if not upstream.api_key:
        return {}
    return {"authorization": f"Bearer {upstream.api_key}"}


def upstream_headers(request: Request, upstream: Upstream) -> dict[str, str]:
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    }
    # Drop empty/malformed Authorization headers from the incoming request
    # (e.g. "Bearer " with no token) — they cause httpx LocalProtocolError.
    auth = headers.get("authorization", "")
    if auth:
        stripped = auth.strip()
        if not stripped:
            del headers["authorization"]
        elif stripped.lower().startswith("bearer"):
            token = stripped[len("Bearer "):].strip() if len(stripped) > 7 else ""
            if not token:
                del headers["authorization"]
    if upstream.api_key:
        headers["authorization"] = f"Bearer {upstream.api_key}"
    return headers


def filtered_response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    }


def upstream_unavailable(exc: httpx.RequestError, upstream: Upstream) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content={
            "error": {
                "message": f"Upstream '{upstream.name}' is unavailable: {exc}",
                "type": "upstream_unavailable",
            }
        },
    )


def main() -> None:
    host = os.getenv("LOCAL_LLM_GATEWAY_HOST", os.getenv("LLM_MUX_HOST", "0.0.0.0"))
    port = int(os.getenv("LOCAL_LLM_GATEWAY_PORT", os.getenv("LLM_MUX_PORT", "8080")))
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
