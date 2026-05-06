from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from llm_mux.app import create_app
from llm_mux.config import AppConfig, Upstream


def response(status_code: int, url: str, json: dict) -> httpx.Response:
    return httpx.Response(status_code, json=json, request=httpx.Request("GET", url))


def test_models_are_discovered_with_upstream_prefix(monkeypatch):
    async def fake_get(self, url, **kwargs):
        if url == "http://minadioro:11434/v1/models":
            return response(
                200,
                url,
                {"object": "list", "data": [{"id": "qwen3.6:26b", "object": "model"}]},
            )
        if url == "http://gpus:8000/v1/models":
            return response(
                200,
                url,
                {"object": "list", "data": [{"id": "llama3.1:70b", "object": "model"}]},
            )
        raise AssertionError(url)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    app = create_app(
        AppConfig(
            upstreams=(
                Upstream(name="minadioro", base_url="http://minadioro:11434/v1"),
                Upstream(name="gpus", base_url="http://gpus:8000/v1"),
            )
        )
    )

    api_response = TestClient(app).get("/v1/models")

    assert api_response.status_code == 200
    assert {model["id"] for model in api_response.json()["data"]} == {
        "minadioro-qwen3.6:26b",
        "gpus-llama3.1:70b",
    }


def test_proxy_strips_upstream_prefix(monkeypatch):
    captured = {}

    async def fake_post(self, url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return httpx.Response(200, json={"id": "chatcmpl-test"})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    app = create_app(
        AppConfig(
            upstreams=(
                Upstream(name="minadioro", base_url="http://minadioro:11434/v1"),
            )
        )
    )

    api_response = TestClient(app).post(
        "/v1/chat/completions",
        json={
            "model": "minadioro-qwen3.6:26b",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert api_response.status_code == 200
    assert captured == {
        "url": "http://minadioro:11434/v1/chat/completions",
        "json": {
            "model": "qwen3.6:26b",
            "messages": [{"role": "user", "content": "hello"}],
        },
    }
