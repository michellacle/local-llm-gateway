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

## Install Manually

Local LLM Gateway is intended to be installed from a source checkout by a human operator or an AI agent. It is not distributed through PyPI.

For a foreground development install:

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

Run the server manually:

```bash
.venv/bin/local-llm-gateway --config config.json --host 0.0.0.0 --port 8090
```

You can also run it as a module:

```bash
.venv/bin/python -m local_llm_gateway --config config.json
```

## Install As An Ubuntu Daemon

For a research workstation or lab server, Local LLM Gateway should normally run as an always-on service. The Ubuntu installer creates an isolated virtual environment, writes a `systemd` unit, enables startup on boot, and configures automatic restart on failure.

At the end of installation, the installer starts the service, waits for `/health` to return `{"status":"ok"}`, and prints a confirmation that the gateway is running. If the service starts but the health check fails, the installer prints recent `systemd` logs and exits with an error.

The installer is also the update path. Re-running it from a newer source checkout stops the existing service, force-reinstalls the local package into `/opt/local-llm-gateway/venv`, rewrites the `systemd` unit, restarts the service, runs the health check, and prints `Update successful` when the daemon is running.

Install from a source checkout:

```bash
git clone https://github.com/michellacle/local-llm-gateway.git
cd local-llm-gateway
sudo ./scripts/install-ubuntu-systemd.sh --config ./config.json --port 8090
```

Equivalent explicit form:

```bash
sudo ./scripts/install-ubuntu-systemd.sh --from-source . --config ./config.json --port 8090
```

The installer creates:

```text
/opt/local-llm-gateway/venv
/etc/local-llm-gateway/config.json
/etc/default/local-llm-gateway
/etc/systemd/system/local-llm-gateway.service
```

The service runs as the dedicated system user:

```text
local-llm-gateway
```

Check status:

```bash
systemctl status local-llm-gateway --no-pager
```

Follow logs:

```bash
journalctl -u local-llm-gateway -f
```

Restart after changing config:

```bash
sudo systemctl restart local-llm-gateway
```

Verify the service:

```bash
curl http://127.0.0.1:8090/health
curl http://127.0.0.1:8090/v1/models
```

If a Docker container such as Open WebUI needs to reach the host service on the default Docker bridge, use:

```text
http://172.17.0.1:8090/v1
```

Update an existing daemon install from a source checkout:

```bash
cd /path/to/local-llm-gateway
git pull
sudo ./scripts/install-ubuntu-systemd.sh --from-source . --config ./config.json --port 8090
```

Disable or remove the service:

```bash
sudo systemctl disable --now local-llm-gateway
sudo rm -f /etc/systemd/system/local-llm-gateway.service
sudo systemctl daemon-reload
```

Remove installed files only if you no longer need the local config:

```bash
sudo rm -rf /opt/local-llm-gateway /etc/local-llm-gateway /etc/default/local-llm-gateway
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
- `name` can contain hyphens; the gateway matches the longest configured upstream prefix.
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

## Build Local Artifacts

You do not need to build artifacts for normal daemon installation; the installer can install directly from a source checkout. Build artifacts are useful for offline transfer, audits, or controlled internal releases.

Use the project virtual environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python -m pytest -q
python -m build
```

This creates local distribution files:

```text
dist/local_llm_gateway-<version>.tar.gz
dist/local_llm_gateway-<version>-py3-none-any.whl
```

Install a local wheel manually if needed:

```bash
python3 -m venv /tmp/local-llm-gateway-test
. /tmp/local-llm-gateway-test/bin/activate
python -m pip install ./dist/local_llm_gateway-<version>-py3-none-any.whl
local-llm-gateway --help
```

## Development Notes

The repository intentionally keeps `config.json` out of source control because it is machine-specific. Use `config.example.json` as the template.

The package is currently alpha-quality. Review the version, service behavior, and release notes before deploying it as critical local research infrastructure.
