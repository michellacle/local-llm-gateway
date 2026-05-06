from __future__ import annotations

import json

from llm_mux.config import AppConfig


def test_config_accepts_host_port_entries(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "upstreams": [
                    {"name": "minadioro", "host": "minadioro:11434"},
                    {"name": "gpus", "host": "gpus:8000"},
                ]
            }
        )
    )

    config = AppConfig.from_file(config_path)

    assert [upstream.base_url for upstream in config.upstreams] == [
        "http://minadioro:11434/v1",
        "http://gpus:8000/v1",
    ]


def test_config_accepts_hyphenated_upstream_names(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "upstreams": [
                    {"name": "minadioro-ollama", "host": "minadioro:11434"},
                    {"name": "gpus-vllm", "host": "gpus:8000"},
                ]
            }
        )
    )

    config = AppConfig.from_file(config_path)

    assert [upstream.name for upstream in config.upstreams] == [
        "minadioro-ollama",
        "gpus-vllm",
    ]
