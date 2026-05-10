from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from .config import AppConfig, MetricsConfig, Upstream, load_config
from .metrics import MetricsStore, RequestMetric, streaming_proxy_with_metrics
from .router import UnknownModelError, UpstreamRegistry
from .smoketest import SmokeTestRunner

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


def create_app(config: AppConfig | None = None) -> FastAPI:
    app_config = config or load_config()
    registry = UpstreamRegistry(app_config.upstreams)
    app = FastAPI(title="Local LLM Gateway", version="0.1.0")
    app.state.registry = registry
    app.state.upstreams = app_config.upstreams

    metrics_store: MetricsStore | None = None
    if app_config.metrics.enabled:
        metrics_store = MetricsStore(
            retention_seconds=app_config.metrics.retention_seconds,
            max_records=app_config.metrics.max_records,
        )
        app.state.metrics_store = metrics_store

    smoke_runner = SmokeTestRunner(app_config)
    app.state.smoke_runner = smoke_runner

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "upstreams": registry.list_upstreams()}

    @app.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        return await discover_models(app_config.upstreams)

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

    @app.post("/v1/chat/completions", response_model=None)
    async def chat_completions(request: Request) -> StreamingResponse | Response:
        return await proxy_openai_request(
            request, registry, OPENAI_ENDPOINTS["chat_completions"], metrics_store
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
