from __future__ import annotations

import argparse
import os

import uvicorn

from .app import create_app
from .config import load_config


def parse_args() -> argparse.Namespace:
    default_config = os.getenv(
        "LOCAL_LLM_GATEWAY_CONFIG", os.getenv("LLM_MUX_CONFIG", "config.json")
    )
    default_host = os.getenv("LOCAL_LLM_GATEWAY_HOST", os.getenv("LLM_MUX_HOST", "0.0.0.0"))
    default_port = os.getenv("LOCAL_LLM_GATEWAY_PORT", os.getenv("LLM_MUX_PORT", "8080"))

    parser = argparse.ArgumentParser(
        prog="local-llm-gateway",
        description="Run an OpenAI-compatible multiplexer for local LLM servers.",
    )
    parser.add_argument(
        "--config",
        default=default_config,
        help=(
            "Path to the JSON config file. Defaults to LOCAL_LLM_GATEWAY_CONFIG, "
            "LLM_MUX_CONFIG, or ./config.json."
        ),
    )
    parser.add_argument(
        "--host",
        default=default_host,
        help="Bind host. Defaults to LOCAL_LLM_GATEWAY_HOST, LLM_MUX_HOST, or 0.0.0.0.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(default_port),
        help="Bind port. Defaults to LOCAL_LLM_GATEWAY_PORT, LLM_MUX_PORT, or 8080.",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn reload for local development.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    app = create_app(config)
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)
