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
    parser.add_argument(
        "--smoketest",
        action="store_true",
        help="Run smoke test against all discovered models then exit.",
    )
    parser.add_argument(
        "--smoketest-max",
        type=int,
        default=10,
        help="Max models to test in smoke test (default: 10).",
    )
    parser.add_argument(
        "--smoketest-stream",
        action="store_true",
        help="Use streaming mode for smoke test requests.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)

    if args.smoketest:
        import asyncio
        from .smoketest import SmokeTestRunner

        runner = SmokeTestRunner(config)

        async def run():
            test_id = await runner.start_run(
                max_models=args.smoketest_max,
                stream=args.smoketest_stream,
            )
            while True:
                r = await runner.get_run(test_id)
                if r and not r.is_running:
                    break
                await asyncio.sleep(0.5)

            if r:
                s = r.summary
                print(f"\nSmoke test {s['test_id']} complete:")
                print(f"  OK: {s['ok']}  Failed: {s['failed']}  Total: {s['total_models']}")
                for res in s["results"]:
                    icon = "✓" if res["status"] == "ok" else "✗"
                    lat = f"{res['latency_ms']:.0f}ms" if res["latency_ms"] else "—"
                    preview = f' "{res["response_preview"]}"' if res.get("response_preview") else ""
                    err = f" [{res['error']}]" if res.get("error") else ""
                    print(f"  {icon} {res['model']} ({res['backend']}) {lat}{preview}{err}")
                return s["failed"] == 0

        success = asyncio.run(run())
        raise SystemExit(0 if success else 1)

    app = create_app(config)
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)
