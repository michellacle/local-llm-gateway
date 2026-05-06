from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .config import AppConfig, Upstream, load_config
from .router import UnknownModelError, UpstreamRegistry

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
    app = FastAPI(title="LLM Mux", version="0.1.0")
    app.state.registry = registry
    app.state.upstreams = app_config.upstreams

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "upstreams": registry.list_upstreams()}

    @app.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        return await discover_models(app_config.upstreams)

    @app.post("/v1/chat/completions", response_model=None)
    async def chat_completions(request: Request) -> StreamingResponse | Response:
        return await proxy_openai_request(request, registry, OPENAI_ENDPOINTS["chat_completions"])

    @app.post("/v1/completions", response_model=None)
    async def completions(request: Request) -> StreamingResponse | Response:
        return await proxy_openai_request(request, registry, OPENAI_ENDPOINTS["completions"])

    @app.post("/v1/embeddings", response_model=None)
    async def embeddings(request: Request) -> StreamingResponse | Response:
        return await proxy_openai_request(request, registry, OPENAI_ENDPOINTS["embeddings"])

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
    request: Request, registry: UpstreamRegistry, upstream_path: str
) -> StreamingResponse | Response:
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

    if bool(payload.get("stream")):
        return await streaming_proxy_response(
            url, headers, upstream_payload, upstream.timeout_seconds
        )

    async with httpx.AsyncClient(timeout=upstream.timeout_seconds) as client:
        try:
            response = await client.post(url, headers=headers, json=upstream_payload)
        except httpx.RequestError as exc:
            return upstream_unavailable(exc, upstream)

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
    host = os.getenv("LLM_MUX_HOST", "0.0.0.0")
    port = int(os.getenv("LLM_MUX_PORT", "8080"))
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
