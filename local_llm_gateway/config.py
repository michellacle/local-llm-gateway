from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class Upstream:
    name: str
    base_url: str
    api_key: str | None = None
    timeout_seconds: float = 600.0

    @property
    def normalized_base_url(self) -> str:
        return self.base_url.rstrip("/")


@dataclass(frozen=True)
class MetricsConfig:
    enabled: bool = True
    retention_seconds: float = 900
    max_records: int = 2000
    dashboard_bind: str = "127.0.0.1"


@dataclass(frozen=True)
class AppConfig:
    upstreams: tuple[Upstream, ...]
    metrics: MetricsConfig = field(default_factory=MetricsConfig)

    @classmethod
    def from_file(cls, path: str | os.PathLike[str]) -> "AppConfig":
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        raw = json.loads(config_path.read_text())
        upstreams_data = raw.get("upstreams")
        if not isinstance(upstreams_data, list) or not upstreams_data:
            raise ValueError("Config must contain a non-empty 'upstreams' array")

        upstreams: list[Upstream] = []
        seen_names: set[str] = set()
        for item in upstreams_data:
            if not isinstance(item, dict):
                raise ValueError("Each upstream must be an object")

            name = str(item.get("name", "")).strip()
            base_url = build_base_url(item)
            if not name:
                raise ValueError("Each upstream must define a non-empty 'name'")
            if name in seen_names:
                raise ValueError(f"Duplicate upstream name '{name}'")

            seen_names.add(name)
            upstreams.append(
                Upstream(
                    name=name,
                    base_url=base_url,
                    api_key=item.get("api_key"),
                    timeout_seconds=float(item.get("timeout_seconds", 600.0)),
                )
            )

        metrics_raw = raw.get("metrics", {})
        if not isinstance(metrics_raw, dict):
            metrics_raw = {}
        metrics = MetricsConfig(
            enabled=bool(metrics_raw.get("enabled", True)),
            retention_seconds=float(metrics_raw.get("retention_seconds", 900)),
            max_records=int(metrics_raw.get("max_records", 2000)),
            dashboard_bind=str(metrics_raw.get("dashboard_bind", "127.0.0.1")),
        )

        return cls(upstreams=tuple(upstreams), metrics=metrics)


def build_base_url(item: dict) -> str:
    raw_base_url = str(item.get("base_url", "")).strip()
    raw_host = str(item.get("host", "")).strip()

    if raw_base_url:
        return normalize_base_url(raw_base_url)
    if raw_host:
        scheme = str(item.get("scheme", "http")).strip() or "http"
        api_path = str(item.get("api_path", "/v1")).strip() or "/v1"
        if not api_path.startswith("/"):
            api_path = f"/{api_path}"
        return normalize_base_url(f"{scheme}://{raw_host}{api_path}")

    name = item.get("name", "<unknown>")
    raise ValueError(f"Upstream '{name}' must define either 'host' or 'base_url'")


def normalize_base_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid upstream base URL '{value}'")
    return value.rstrip("/")


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    config_path = path or os.getenv(
        "LOCAL_LLM_GATEWAY_CONFIG", os.getenv("LLM_MUX_CONFIG", "config.json")
    )
    return AppConfig.from_file(config_path)
