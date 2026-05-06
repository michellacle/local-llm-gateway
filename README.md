# Local LLM Gateway

Local LLM Gateway is a FastAPI OpenAI-compatible API server that exposes one stable endpoint for multiple local-network OpenAI-compatible LLM servers.

It discovers models from each configured backend and publishes them through `/v1/models` with a backend prefix. A client can use one base URL and switch models by changing only the model name.

## Why

Local LLM research setups often have several inference servers running on different machines, ports, GPUs, runtimes, and model configurations. Local LLM Gateway gives upstream tools one OpenAI-compatible endpoint while preserving access to every backend model.

Example backend servers:

```text
host1:11434
host2:8000
```

If `host1` exposes `qwen3.6:26b`, Local LLM Gateway exposes it as:

```text
host1-qwen3.6:26b
```

When a request uses `host1-qwen3.6:26b`, Local LLM Gateway forwards it to `host1` with the model rewritten back to `qwen3.6:26b`.

## Features

- OpenAI-compatible `/v1/models` model discovery.
- OpenAI-compatible proxy endpoints for chat completions, completions, and embeddings.
- Dynamic model names based on configured upstream host prefixes.
- Supports streaming chat/completion responses.
- Configurable upstream API keys and request timeouts.
- Installable as a regular Python package with the `local-llm-gateway` CLI.

## Install From PyPI

After the package is published:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install local-llm-gateway
```

Run it:

```bash
local-llm-gateway --config config.json --host 0.0.0.0 --port 8080
```

## Install From Source

For development or before the package is published:

```bash
git clone https://github.com/michellacle/local-llm-gateway.git
cd local-llm-gateway
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

Run tests:

```bash
.venv/bin/python -m pytest -q
```

Run the server:

```bash
.venv/bin/local-llm-gateway --config config.json --host 0.0.0.0 --port 8080
```

You can also run it as a module:

```bash
.venv/bin/python -m local_llm_gateway --config config.json
```

## Configuration

Create `config.json`:

```json
{
  "upstreams": [
    {
      "name": "host1",
      "host": "host1:11434"
    },
    {
      "name": "host2",
      "host": "host2:8000"
    }
  ]
}
```

`host` entries default to:

```text
http://<host>/v1
```

You can use `base_url` instead when a backend needs a custom scheme or path:

```json
{
  "upstreams": [
    {
      "name": "remote_gpu",
      "base_url": "https://gpu.example.test/openai/v1",
      "api_key": "secret-key-if-required",
      "timeout_seconds": 900
    }
  ]
}
```

Rules:

- `name` becomes the public model prefix.
- `name` must be unique.
- `name` cannot contain `-` because public model IDs use `<upstream-name>-<upstream-model-id>`.
- `host` is enough for most local servers.
- `base_url` is useful for HTTPS, reverse proxies, or non-standard paths.

Environment variables:

```bash
export LOCAL_LLM_GATEWAY_CONFIG=/path/to/config.json
export LOCAL_LLM_GATEWAY_HOST=0.0.0.0
export LOCAL_LLM_GATEWAY_PORT=8080
```

CLI arguments override the defaults:

```bash
local-llm-gateway --config /path/to/config.json --host 127.0.0.1 --port 8080
```

## API

Health check:

```bash
curl http://localhost:8080/health
```

List dynamically discovered models:

```bash
curl http://localhost:8080/v1/models
```

Example response:

```json
{
  "object": "list",
  "data": [
    {
      "id": "host1-qwen3.6:26b",
      "object": "model",
      "created": 0,
      "owned_by": "host1"
    },
    {
      "id": "host2-llama3.1:70b",
      "object": "model",
      "created": 0,
      "owned_by": "host2"
    }
  ]
}
```

Chat completion:

```bash
curl http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "host1-qwen3.6:26b",
    "messages": [{"role": "user", "content": "Say hello"}]
  }'
```

Streaming chat completion:

```bash
curl -N http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "host2-llama3.1:70b",
    "stream": true,
    "messages": [{"role": "user", "content": "Write one sentence."}]
  }'
```

Supported proxy endpoints:

- `POST /v1/chat/completions`
- `POST /v1/completions`
- `POST /v1/embeddings`

## Build The Package

Use the project virtual environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python -m pytest -q
python -m build
python -m twine check dist/*
```

This creates:

```text
dist/local_llm_gateway-<version>.tar.gz
dist/local_llm_gateway-<version>-py3-none-any.whl
```

## Publish To PyPI

Create a PyPI API token, then upload:

```bash
. .venv/bin/activate
python -m twine upload dist/*
```

For TestPyPI first:

```bash
. .venv/bin/activate
python -m twine upload --repository testpypi dist/*
```

Install from TestPyPI for verification:

```bash
python3 -m venv /tmp/local-llm-gateway-test
. /tmp/local-llm-gateway-test/bin/activate
python -m pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ local-llm-gateway
local-llm-gateway --help
```

## Development Notes

The repository intentionally keeps `config.json` out of source control because it is machine-specific. Use `config.example.json` as the template.

The package is currently alpha-quality. Review the version, classifiers, and release notes before a public PyPI release.
