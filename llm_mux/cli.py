from __future__ import annotations

import argparse
import os

import uvicorn

from .app import create_app
from .config import load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="llm-mux",
        description="Run an OpenAI-compatible multiplexer for local LLM servers.",
    )
    parser.add_argument(
        "--config",
        default=os.getenv("LLM_MUX_CONFIG", "config.json"),
        help="Path to the JSON config file. Defaults to LLM_MUX_CONFIG or ./config.json.",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("LLM_MUX_HOST", "0.0.0.0"),
        help="Bind host. Defaults to LLM_MUX_HOST or 0.0.0.0.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("LLM_MUX_PORT", "8080")),
        help="Bind port. Defaults to LLM_MUX_PORT or 8080.",
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
