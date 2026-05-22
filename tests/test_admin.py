from __future__ import annotations

import json
import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from local_llm_gateway.app import create_app
from local_llm_gateway.config import AppConfig, Upstream, add_upstream_to_config


def test_admin_list_upstreams():
    app = create_app(
        AppConfig(
            upstreams=(
                Upstream(name="alpha", base_url="http://alpha:11434/v1"),
                Upstream(name="beta", base_url="http://beta:8000/v1"),
            )
        ),
        config_path="/tmp/nonexistent.json",
    )

    resp = TestClient(app).get("/admin/upstreams")

    assert resp.status_code == 200
    data = resp.json()
    names = {u["name"] for u in data["upstreams"]}
    assert names == {"alpha", "beta"}


def test_admin_add_upstream_with_host():
    app = create_app(
        AppConfig(
            upstreams=(
                Upstream(name="alpha", base_url="http://alpha:11434/v1"),
            )
        ),
        config_path=None,
    )

    resp = TestClient(app).post(
        "/admin/upstreams",
        json={"name": "gamma", "host": "gamma:11434"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "added"
    assert body["upstream"]["name"] == "gamma"
    assert body["upstream"]["base_url"] == "http://gamma:11434/v1"
    assert body["persisted"] is False

    list_resp = TestClient(app).get("/admin/upstreams")
    names = {u["name"] for u in list_resp.json()["upstreams"]}
    assert names == {"alpha", "gamma"}


def test_admin_add_upstream_with_base_url():
    app = create_app(
        AppConfig(
            upstreams=(
                Upstream(name="alpha", base_url="http://alpha:11434/v1"),
            )
        ),
        config_path=None,
    )

    resp = TestClient(app).post(
        "/admin/upstreams",
        json={"name": "delta", "base_url": "http://delta:9000/api"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["upstream"]["base_url"] == "http://delta:9000/api"


def test_admin_add_upstream_persists_to_config(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({"upstreams": [{"name": "alpha", "host": "alpha:11434"}]})
    )

    app = create_app(
        AppConfig.from_file(config_file),
        config_path=str(config_file),
    )

    resp = TestClient(app).post(
        "/admin/upstreams",
        json={"name": "zeta", "host": "zeta:11434"},
    )

    assert resp.status_code == 200
    assert resp.json()["persisted"] is True

    saved = json.loads(config_file.read_text())
    names = [u["name"] for u in saved["upstreams"]]
    assert names == ["alpha", "zeta"]
    assert saved["upstreams"][1]["host"] == "zeta:11434"


def test_admin_add_upstream_duplicate_returns_409():
    app = create_app(
        AppConfig(
            upstreams=(
                Upstream(name="alpha", base_url="http://alpha:11434/v1"),
            )
        ),
        config_path=None,
    )

    resp = TestClient(app).post(
        "/admin/upstreams",
        json={"name": "alpha", "host": "other:11434"},
    )

    assert resp.status_code == 409


def test_admin_add_upstream_missing_name_returns_400():
    app = create_app(
        AppConfig(
            upstreams=(
                Upstream(name="alpha", base_url="http://alpha:11434/v1"),
            )
        ),
        config_path=None,
    )

    resp = TestClient(app).post(
        "/admin/upstreams",
        json={"host": "gamma:11434"},
    )

    assert resp.status_code == 400


def test_admin_add_upstream_missing_host_and_base_url_returns_400():
    app = create_app(
        AppConfig(
            upstreams=(
                Upstream(name="alpha", base_url="http://alpha:11434/v1"),
            )
        ),
        config_path=None,
    )

    resp = TestClient(app).post(
        "/admin/upstreams",
        json={"name": "gamma"},
    )

    assert resp.status_code == 400


def test_admin_add_upstream_invalid_base_url_returns_400():
    app = create_app(
        AppConfig(
            upstreams=(
                Upstream(name="alpha", base_url="http://alpha:11434/v1"),
            )
        ),
        config_path=None,
    )

    resp = TestClient(app).post(
        "/admin/upstreams",
        json={"name": "gamma", "base_url": "not-a-url"},
    )

    assert resp.status_code == 400


def test_admin_add_upstream_with_api_key_and_timeout():
    app = create_app(
        AppConfig(
            upstreams=(
                Upstream(name="alpha", base_url="http://alpha:11434/v1"),
            )
        ),
        config_path=None,
    )

    resp = TestClient(app).post(
        "/admin/upstreams",
        json={
            "name": "gamma",
            "host": "gamma:11434",
            "api_key": "sk-secret",
            "timeout_seconds": 120,
        },
    )

    assert resp.status_code == 200
    list_resp = TestClient(app).get("/admin/upstreams")
    gamma = [u for u in list_resp.json()["upstreams"] if u["name"] == "gamma"][0]
    assert gamma["api_key"] == "sk-secret"
    assert gamma["timeout_seconds"] == 120


def test_admin_add_upstream_custom_scheme_and_api_path():
    app = create_app(
        AppConfig(
            upstreams=(
                Upstream(name="alpha", base_url="http://alpha:11434/v1"),
            )
        ),
        config_path=None,
    )

    resp = TestClient(app).post(
        "/admin/upstreams",
        json={
            "name": "gamma",
            "host": "gamma:8443",
            "scheme": "https",
            "api_path": "/openai",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["upstream"]["base_url"] == "https://gamma:8443/openai"


def test_admin_reload_reloads_config(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({"upstreams": [{"name": "alpha", "host": "alpha:11434"}]})
    )

    app = create_app(
        AppConfig.from_file(config_file),
        config_path=str(config_file),
    )

    list_resp = TestClient(app).get("/admin/upstreams")
    assert {u["name"] for u in list_resp.json()["upstreams"]} == {"alpha"}

    config_file.write_text(
        json.dumps({
            "upstreams": [
                {"name": "alpha", "host": "alpha:11434"},
                {"name": "beta", "host": "beta:8000"},
            ]
        })
    )

    reload_resp = TestClient(app).post("/admin/reload")
    assert reload_resp.status_code == 200
    assert reload_resp.json()["status"] == "reloaded"

    list_resp = TestClient(app).get("/admin/upstreams")
    assert {u["name"] for u in list_resp.json()["upstreams"]} == {"alpha", "beta"}


def test_admin_reload_no_config_path_returns_500():
    app = create_app(
        AppConfig(
            upstreams=(
                Upstream(name="alpha", base_url="http://alpha:11434/v1"),
            )
        ),
        config_path=None,
    )

    resp = TestClient(app).post("/admin/reload")
    assert resp.status_code == 500


def test_admin_reload_invalid_config_returns_400(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({"upstreams": [{"name": "alpha", "host": "alpha:11434"}]})
    )

    app = create_app(
        AppConfig.from_file(config_file),
        config_path=str(config_file),
    )

    config_file.write_text("this is not valid json")

    resp = TestClient(app).post("/admin/reload")
    assert resp.status_code == 400


def test_add_upstream_to_config_function(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({"upstreams": [{"name": "alpha", "host": "alpha:11434"}]})
    )

    new_config = add_upstream_to_config(
        config_file,
        name="beta",
        host="beta:8000",
    )

    assert len(new_config.upstreams) == 2
    assert {u.name for u in new_config.upstreams} == {"alpha", "beta"}

    saved = json.loads(config_file.read_text())
    assert len(saved["upstreams"]) == 2


def test_add_upstream_to_config_duplicate_raises():
    config_file = Path(tempfile.mktemp(suffix=".json"))
    config_file.write_text(
        json.dumps({"upstreams": [{"name": "alpha", "host": "alpha:11434"}]})
    )

    try:
        import pytest

        with pytest.raises(ValueError, match="Duplicate"):
            add_upstream_to_config(
                config_file,
                name="alpha",
                host="other:11434",
            )
    finally:
        config_file.unlink(missing_ok=True)


def test_add_upstream_to_config_needs_host_or_base_url():
    config_file = Path(tempfile.mktemp(suffix=".json"))
    config_file.write_text(
        json.dumps({"upstreams": [{"name": "alpha", "host": "alpha:11434"}]})
    )

    try:
        import pytest

        with pytest.raises(ValueError, match="host.*base_url"):
            add_upstream_to_config(
                config_file,
                name="beta",
            )
    finally:
        config_file.unlink(missing_ok=True)


def test_app_config_to_dict():
    config = AppConfig(
        upstreams=(
            Upstream(name="alpha", base_url="http://alpha:11434/v1", api_key="sk-x"),
        )
    )
    d = config.to_dict()
    assert d["upstreams"][0]["name"] == "alpha"
    assert d["upstreams"][0]["api_key"] == "sk-x"
    assert d["metrics"]["enabled"] is True


def test_app_config_roundtrip(tmp_path):
    config_file = tmp_path / "config.json"
    config = AppConfig(
        upstreams=(
            Upstream(name="alpha", base_url="http://alpha:11434/v1", api_key="sk-x"),
            Upstream(name="beta", base_url="http://beta:8000/v1"),
        )
    )
    config.to_json_file(config_file)

    loaded = AppConfig.from_file(config_file)
    assert len(loaded.upstreams) == 2
    assert loaded.upstreams[0].api_key == "sk-x"


def test_newly_added_upstream_is_routable(monkeypatch):
    captured = {}

    async def fake_post(self, url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return httpx.Response(200, json={"id": "chatcmpl-test"})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    app = create_app(
        AppConfig(
            upstreams=(
                Upstream(name="alpha", base_url="http://alpha:11434/v1"),
            )
        ),
        config_path=None,
    )

    client = TestClient(app)

    client.post(
        "/admin/upstreams",
        json={"name": "gamma", "host": "gamma:9000"},
    )

    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "gamma-llama3",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert resp.status_code == 200
    assert captured["url"] == "http://gamma:9000/v1/chat/completions"
    assert captured["json"]["model"] == "llama3"
