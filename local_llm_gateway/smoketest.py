from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import AppConfig


@dataclass
class ModelResult:
    model: str
    backend: str
    status: str = "pending"
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    tps: float | None = None
    error: str | None = None
    response_preview: str | None = None


@dataclass
class SmokeTestRun:
    test_id: str
    started_at: float
    finished_at: float | None = None
    results: list[ModelResult] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def is_running(self) -> bool:
        return self.finished_at is None

    @property
    def summary(self) -> dict[str, Any]:
        total = len(self.results)
        ok = sum(1 for r in self.results if r.status == "ok")
        failed = sum(1 for r in self.results if r.status == "error")
        pending = sum(1 for r in self.results if r.status == "pending")
        return {
            "test_id": self.test_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "is_running": self.is_running,
            "total_models": total,
            "ok": ok,
            "failed": failed,
            "pending": pending,
            "results": [
                {
                    "model": r.model,
                    "backend": r.backend,
                    "status": r.status,
                    "latency_ms": r.latency_ms,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "tps": r.tps,
                    "error": r.error,
                    "response_preview": r.response_preview,
                }
                for r in self.results
            ],
        }


class SmokeTestRunner:
    """Runs smoke tests.

    When ``gateway_base_url`` is set (web mode), requests go through the
    gateway's own proxy endpoints so metrics hooks fire naturally.
    When it's None (CLI mode), requests go directly to upstreams.
    """

    def __init__(
        self,
        app_config: AppConfig,
        gateway_base_url: str | None = None,
    ) -> None:
        self._config = app_config
        self._gateway: str | None = gateway_base_url
        self._runs: dict[str, SmokeTestRun] = {}
        self._lock = asyncio.Lock()

    def set_gateway_url(self, url: str) -> None:
        self._gateway = url

    async def start_run(
        self,
        max_models: int = 10,
        prompt: str | None = None,
        stream: bool = False,
    ) -> str:
        test_id = str(uuid.uuid4())[:8]
        run = SmokeTestRun(
            test_id=test_id,
            started_at=time.time(),
        )
        async with self._lock:
            self._runs[test_id] = run

        asyncio.create_task(
            self._execute_run(run, max_models, prompt or "Say hello in one word.", stream)
        )
        return test_id

    async def get_run(self, test_id: str) -> SmokeTestRun | None:
        async with self._lock:
            return self._runs.get(test_id)

    async def get_latest_run(self) -> SmokeTestRun | None:
        async with self._lock:
            if not self._runs:
                return None
            return self._runs[max(self._runs, key=lambda k: self._runs[k].started_at)]

    # --- execution ---

    async def _execute_run(
        self,
        run: SmokeTestRun,
        max_models: int,
        prompt: str,
        stream: bool,
    ) -> None:
        models = await self._discover_models()
        models = models[:max_models]

        for m in models:
            async with run.lock:
                run.results.append(ModelResult(model=m["id"], backend=m["backend"]))

        tasks = [self._test_model(run, m, prompt, stream) for m in models]
        await asyncio.gather(*tasks, return_exceptions=True)

        async with run.lock:
            run.finished_at = time.time()

    # --- discovery ---

    async def _discover_models(self) -> list[dict[str, Any]]:
        """Discover models via gateway /v1/models or directly from upstreams."""
        if self._gateway:
            return await self._discover_via_gateway()
        return await self._discover_direct()

    async def _discover_via_gateway(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self._gateway}/v1/models")
            resp.raise_for_status()
            data = resp.json().get("data", [])
        results: list[dict[str, Any]] = []
        for m in data:
            if not isinstance(m, dict):
                continue
            results.append({
                "id": m["id"],
                "backend": m.get("owned_by", "?"),
            })
        return results

    async def _discover_direct(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=10) as client:
            for upstream in self._config.upstreams:
                try:
                    resp = await client.get(
                        f"{upstream.normalized_base_url}/models",
                        timeout=upstream.timeout_seconds,
                    )
                    resp.raise_for_status()
                    for m in resp.json().get("data", []):
                        if not isinstance(m, dict):
                            continue
                        results.append({
                            "id": f"{upstream.name}-{m.get('id', '')}",
                            "backend": upstream.name,
                        })
                except Exception:
                    continue
        return results

    # --- per-model test ---

    async def _test_model(
        self,
        run: SmokeTestRun,
        model_info: dict[str, Any],
        prompt: str,
        stream: bool,
    ) -> None:
        model_id = model_info["id"]
        backend = model_info["backend"]

        if self._gateway:
            await self._test_via_gateway(run, model_id, prompt, stream)
            return

        # CLI mode: direct to upstream
        upstream = None
        for u in self._config.upstreams:
            if u.name == backend:
                upstream = u
                break
        if upstream is None:
            return

        upstream_model = model_id[len(backend) + 1 :]
        url = f"{upstream.normalized_base_url}/chat/completions"

        payload: dict[str, Any] = {
            "model": upstream_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 32,
            "stream": stream,
        }
        headers = {}
        if upstream.api_key:
            headers["authorization"] = f"Bearer {upstream.api_key}"

        start = time.time()
        async with httpx.AsyncClient(timeout=upstream.timeout_seconds) as client:
            try:
                if stream:
                    await self._do_streaming(client, url, headers, payload, run, model_id, start)
                else:
                    await self._do_non_streaming(client, url, headers, payload, run, model_id, start)
            except Exception as exc:
                await self._mark_error(run, model_id, exc, start)

    # --- gateway path ---

    async def _test_via_gateway(
        self,
        run: SmokeTestRun,
        model_id: str,
        prompt: str,
        stream: bool,
    ) -> None:
        url = f"{self._gateway}/v1/chat/completions"
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 32,
            "stream": stream,
        }
        start = time.time()
        async with httpx.AsyncClient(timeout=1200) as client:
            try:
                if stream:
                    await self._do_streaming(client, url, {}, payload, run, model_id, start)
                else:
                    await self._do_non_streaming(client, url, {}, payload, run, model_id, start)
            except Exception as exc:
                await self._mark_error(run, model_id, exc, start)

    # --- helpers ---

    async def _do_non_streaming(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        run: SmokeTestRun,
        model_id: str,
        start: float,
    ) -> None:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        body = resp.json()
        elapsed = round(resp.elapsed.total_seconds() * 1000, 2)
        usage = body.get("usage", {})
        content = ""
        for c in body.get("choices", []):
            content = c.get("message", {}).get("content", "") or content

        async with run.lock:
            for r in run.results:
                if r.model == model_id:
                    r.status = "ok"
                    r.latency_ms = elapsed
                    r.input_tokens = usage.get("prompt_tokens")
                    r.output_tokens = usage.get("completion_tokens")
                    out_tok = r.output_tokens or 0
                    r.tps = round(out_tok / (elapsed / 1000), 1) if out_tok and elapsed > 0 else None
                    r.response_preview = content[:120] if content else None
                    break

    def _extract_tokens(self, obj: dict[str, Any]) -> tuple[int | None, int | None]:
        usage_node = obj.get("usage")
        if usage_node:
            return usage_node.get("prompt_tokens"), usage_node.get("completion_tokens")
        if obj.get("prompt_eval_count") is not None:
            return obj.get("prompt_eval_count"), obj.get("eval_count")
        return None, None

    async def _do_streaming(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        run: SmokeTestRun,
        model_id: str,
        start: float,
    ) -> None:
        req = client.build_request("POST", url, headers=headers, json=payload)
        resp = await client.send(req, stream=True)
        resp.raise_for_status()
        collected: list[str] = []
        stream_input_tokens: int | None = None
        stream_output_tokens: int | None = None
        buffer = ""
        async for chunk in resp.aiter_bytes():
            buffer += chunk.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    continue
                try:
                    obj = json.loads(data)
                    content = obj.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if content:
                        collected.append(content)
                    in_t, out_t = self._extract_tokens(obj)
                    if in_t is not None:
                        stream_input_tokens = in_t
                    if out_t is not None:
                        stream_output_tokens = out_t
                except (json.JSONDecodeError, IndexError, KeyError):
                    pass
        await resp.aclose()
        elapsed = round((time.time() - start) * 1000, 2)
        full_text = "".join(collected)

        async with run.lock:
            for r in run.results:
                if r.model == model_id:
                    r.status = "ok"
                    r.latency_ms = elapsed
                    r.input_tokens = stream_input_tokens
                    r.output_tokens = stream_output_tokens
                    out_tok = r.output_tokens or 0
                    r.tps = round(out_tok / (elapsed / 1000), 1) if out_tok and elapsed > 0 else None
                    r.response_preview = full_text[:120] if full_text else None
                    break

    async def _mark_error(
        self,
        run: SmokeTestRun,
        model_id: str,
        exc: Exception,
        start: float,
    ) -> None:
        elapsed = round((time.time() - start) * 1000, 2)
        async with run.lock:
            for r in run.results:
                if r.model == model_id:
                    r.status = "error"
                    r.latency_ms = elapsed
                    r.error = str(exc)[:200]
                    break


@dataclass
class BenchmarkIteration:
    iteration: int
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    tps: float | None = None
    ttft_ms: float | None = None
    error: str | None = None


@dataclass
class BenchmarkRun:
    run_id: str
    model: str
    backend: str
    prompt: str
    iterations: int
    stream: bool
    started_at: float
    finished_at: float | None = None
    results: list[BenchmarkIteration] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def is_running(self) -> bool:
        return self.finished_at is None

    @property
    def summary(self) -> dict[str, Any]:
        ok_results = [r for r in self.results if r.error is None]
        latencies = [r.latency_ms for r in ok_results if r.latency_ms is not None]
        tps_values = [r.tps for r in ok_results if r.tps is not None]
        ttft_values = [r.ttft_ms for r in ok_results if r.ttft_ms is not None]
        in_tokens = [r.input_tokens or 0 for r in ok_results]
        out_tokens = [r.output_tokens or 0 for r in ok_results]

        def avg(vals: list[float]) -> float:
            return round(sum(vals) / len(vals), 2) if vals else 0

        return {
            "run_id": self.run_id,
            "model": self.model,
            "backend": self.backend,
            "prompt": self.prompt,
            "iterations": self.iterations,
            "stream": self.stream,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "is_running": self.is_running,
            "completed": len(self.results),
            "ok": len(ok_results),
            "failed": len(self.results) - len(ok_results),
            "avg_latency_ms": avg(latencies),
            "min_latency_ms": round(min(latencies), 2) if latencies else 0,
            "max_latency_ms": round(max(latencies), 2) if latencies else 0,
            "avg_tps": avg(tps_values),
            "min_tps": round(min(tps_values), 1) if tps_values else 0,
            "max_tps": round(max(tps_values), 1) if tps_values else 0,
            "avg_ttft_ms": avg(ttft_values),
            "total_input_tokens": sum(in_tokens),
            "total_output_tokens": sum(out_tokens),
            "results": [
                {
                    "iteration": r.iteration,
                    "latency_ms": r.latency_ms,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "tps": r.tps,
                    "ttft_ms": r.ttft_ms,
                    "error": r.error,
                }
                for r in self.results
            ],
        }


class BenchmarkRunner:
    """Runs benchmarks: single model, N iterations, computes averages."""

    def __init__(
        self,
        app_config: AppConfig,
        gateway_base_url: str | None = None,
    ) -> None:
        self._config = app_config
        self._gateway: str | None = gateway_base_url
        self._runs: dict[str, BenchmarkRun] = {}
        self._lock = asyncio.Lock()

    def set_gateway_url(self, url: str) -> None:
        self._gateway = url

    async def start_run(
        self,
        model: str,
        prompt: str,
        iterations: int = 5,
        stream: bool = False,
        max_tokens: int = 256,
    ) -> str:
        run_id = str(uuid.uuid4())[:8]
        backend = model.split("-", 1)[0] if "-" in model else "?"
        run = BenchmarkRun(
            run_id=run_id,
            model=model,
            backend=backend,
            prompt=prompt,
            iterations=iterations,
            stream=stream,
            started_at=time.time(),
        )
        async with self._lock:
            self._runs[run_id] = run

        asyncio.create_task(self._execute(run, prompt, stream, max_tokens))
        return run_id

    async def get_run(self, run_id: str) -> BenchmarkRun | None:
        async with self._lock:
            return self._runs.get(run_id)

    async def get_latest_run(self) -> BenchmarkRun | None:
        async with self._lock:
            if not self._runs:
                return None
            return self._runs[max(self._runs, key=lambda k: self._runs[k].started_at)]

    async def _execute(
        self,
        run: BenchmarkRun,
        prompt: str,
        stream: bool,
        max_tokens: int,
    ) -> None:
        for i in range(1, run.iterations + 1):
            async with run.lock:
                run.results.append(BenchmarkIteration(iteration=i))

            it = run.results[-1]
            start = time.time()
            try:
                if self._gateway:
                    await self._single_request(
                        self._gateway, run.model, prompt, stream, max_tokens, it, start
                    )
                else:
                    await self._single_direct(
                        run, prompt, stream, max_tokens, it, start
                    )
            except Exception as exc:
                elapsed = round((time.time() - start) * 1000, 2)
                async with run.lock:
                    it.latency_ms = elapsed
                    it.error = str(exc)[:200]

        async with run.lock:
            run.finished_at = time.time()

    async def _single_request(
        self,
        gateway: str,
        model: str,
        prompt: str,
        stream: bool,
        max_tokens: int,
        it: BenchmarkIteration,
        start: float,
    ) -> None:
        url = f"{gateway}/v1/chat/completions"
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": stream,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            if stream:
                await self._stream_req(client, url, {}, payload, it, start)
            else:
                await self._json_req(client, url, {}, payload, it, start)

    async def _single_direct(
        self,
        run: BenchmarkRun,
        prompt: str,
        stream: bool,
        max_tokens: int,
        it: BenchmarkIteration,
        start: float,
    ) -> None:
        upstream = None
        for u in self._config.upstreams:
            if u.name == run.backend:
                upstream = u
                break
        if not upstream:
            it.error = f"Unknown backend: {run.backend}"
            return

        upstream_model = run.model[len(run.backend) + 1 :]
        url = f"{upstream.normalized_base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": upstream_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": stream,
        }
        headers = {}
        if upstream.api_key:
            headers["authorization"] = f"Bearer {upstream.api_key}"

        async with httpx.AsyncClient(timeout=upstream.timeout_seconds) as client:
            if stream:
                await self._stream_req(client, url, headers, payload, it, start)
            else:
                await self._json_req(client, url, headers, payload, it, start)

    async def _json_req(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        it: BenchmarkIteration,
        start: float,
    ) -> None:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        body = resp.json()
        elapsed = round(resp.elapsed.total_seconds() * 1000, 2)
        usage = body.get("usage", {})
        in_tok = usage.get("prompt_tokens")
        out_tok = usage.get("completion_tokens")
        tps = round(out_tok / (elapsed / 1000), 1) if out_tok and elapsed > 0 else None
        it.latency_ms = elapsed
        it.input_tokens = in_tok
        it.output_tokens = out_tok
        it.tps = tps
        it.ttft_ms = elapsed

    async def _stream_req(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        it: BenchmarkIteration,
        start: float,
    ) -> None:
        req = client.build_request("POST", url, headers=headers, json=payload)
        resp = await client.send(req, stream=True)
        resp.raise_for_status()
        buffer = ""
        first_chunk_time: float | None = None
        async for chunk in resp.aiter_bytes():
            if first_chunk_time is None:
                first_chunk_time = time.time()
            buffer += chunk.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    continue
                try:
                    obj = json.loads(data)
                    in_t, out_t = self._extract_tokens(obj)
                    if in_t is not None:
                        it.input_tokens = in_t
                    if out_t is not None:
                        it.output_tokens = out_t
                        total_ms = round((time.time() - start) * 1000, 2)
                        it.tps = round(out_t / (total_ms / 1000), 1) if total_ms > 0 else None
                except (json.JSONDecodeError, IndexError, KeyError):
                    pass
        await resp.aclose()
        elapsed = round((time.time() - start) * 1000, 2)
        ttft = round((first_chunk_time - start) * 1000, 2) if first_chunk_time else elapsed
        it.latency_ms = elapsed
        it.ttft_ms = ttft
