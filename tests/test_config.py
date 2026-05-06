from __future__ import annotations

import json

import pytest

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


def test_upstream_names_cannot_contain_separator(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"upstreams": [{"name": "bad-name", "host": "localhost:8000"}]})
    )

    with pytest.raises(ValueError, match="cannot contain '-'" ):
        AppConfig.from_file(config_path)
