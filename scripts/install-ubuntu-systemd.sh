#!/usr/bin/env bash
set -euo pipefail
trap 'fail "Command failed on line ${LINENO}: ${BASH_COMMAND}"' ERR

SERVICE_NAME="local-llm-gateway"
INSTALL_DIR="/opt/local-llm-gateway"
CONFIG_DIR="/etc/local-llm-gateway"
CONFIG_FILE="${CONFIG_DIR}/config.json"
ENV_FILE="/etc/default/${SERVICE_NAME}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
SERVICE_USER="local-llm-gateway"
HOST="0.0.0.0"
PORT="8090"
SOURCE_DIR="."
CONFIG_SOURCE=""
SKIP_START="false"
HEALTH_CHECK_HOST="127.0.0.1"
SERVICE_WAS_INSTALLED="false"

usage() {
  cat <<USAGE
Install Local LLM Gateway as an always-running systemd service on Ubuntu.

This installer is designed for manual/source checkout installs by a human or AI agent.
It does not install from PyPI.

Usage:
  sudo ./scripts/install-ubuntu-systemd.sh [options]

Options:
  --from-source PATH          Install from a local source checkout. Default: current directory.
  --config PATH               Seed /etc/local-llm-gateway/config.json from PATH.
  --host HOST                 Bind host for the daemon. Default: 0.0.0.0
  --port PORT                 Bind port for the daemon. Default: 8090
  --user USER                 System user for the daemon. Default: local-llm-gateway
  --skip-start                Install and enable the service but do not start it.
  -h, --help                  Show this help.

Examples:
  sudo ./scripts/install-ubuntu-systemd.sh --config ./config.json --port 8090
  sudo ./scripts/install-ubuntu-systemd.sh --from-source . --config ./config.json --port 8090
USAGE
}

log() {
  printf '[local-llm-gateway installer] %s\n' "$*"
}

fail() {
  printf '[local-llm-gateway installer] ERROR: %s\n' "$*" >&2
  exit 1
}

health_check_url() {
  printf 'http://%s:%s/health' "$HEALTH_CHECK_HOST" "$PORT"
}

wait_for_health() {
  local url
  url="$(health_check_url)"

  log "Waiting for health check: $url"
  for _ in $(seq 1 30); do
    if "${INSTALL_DIR}/venv/bin/python" - "$url" <<'PY' >/dev/null 2>&1
import json
import sys
import urllib.request

url = sys.argv[1]
with urllib.request.urlopen(url, timeout=2) as response:
    payload = json.loads(response.read().decode("utf-8"))
if payload.get("status") != "ok":
    raise SystemExit(1)
PY
    then
      return 0
    fi
    sleep 1
  done

  return 1
}

service_exists() {
  systemctl list-unit-files "${SERVICE_NAME}.service" --no-legend 2>/dev/null | grep -q "^${SERVICE_NAME}.service"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --from-source)
      SOURCE_DIR="${2:-}"
      [[ -n "$SOURCE_DIR" ]] || fail "--from-source requires a path"
      shift 2
      ;;
    --from-pypi|--package)
      fail "$1 is not supported. Local LLM Gateway is installed from a local source checkout; use --from-source PATH."
      ;;
    --config)
      CONFIG_SOURCE="${2:-}"
      [[ -n "$CONFIG_SOURCE" ]] || fail "--config requires a path"
      shift 2
      ;;
    --host)
      HOST="${2:-}"
      [[ -n "$HOST" ]] || fail "--host requires a value"
      shift 2
      ;;
    --port)
      PORT="${2:-}"
      [[ "$PORT" =~ ^[0-9]+$ ]] || fail "--port must be numeric"
      shift 2
      ;;
    --user)
      SERVICE_USER="${2:-}"
      [[ -n "$SERVICE_USER" ]] || fail "--user requires a value"
      shift 2
      ;;
    --skip-start)
      SKIP_START="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown option: $1"
      ;;
  esac
done

[[ "$(id -u)" -eq 0 ]] || fail "Run this installer with sudo or as root"
command -v systemctl >/dev/null 2>&1 || fail "systemd is required"

[[ -d "$SOURCE_DIR" ]] || fail "Source directory not found: $SOURCE_DIR"
SOURCE_DIR="$(cd "$SOURCE_DIR" && pwd)"
[[ -f "${SOURCE_DIR}/pyproject.toml" ]] || fail "Source directory must contain pyproject.toml: $SOURCE_DIR"

if [[ -n "$CONFIG_SOURCE" ]]; then
  [[ -f "$CONFIG_SOURCE" ]] || fail "Config file not found: $CONFIG_SOURCE"
fi

if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}" in
    ubuntu|debian|linuxmint|pop) ;;
    *) log "Warning: this installer is designed for Ubuntu-based systems; detected ID=${ID:-unknown}" ;;
  esac
fi

if service_exists; then
  SERVICE_WAS_INSTALLED="true"
  if [[ "$SKIP_START" == "true" ]]; then
    log "Existing service detected; leaving runtime state unchanged because --skip-start was used"
  else
    log "Existing service detected; stopping it before updating installed files"
    systemctl stop "$SERVICE_NAME" || true
  fi
fi

log "Installing OS dependencies"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip ca-certificates

log "Creating service user and directories"
if ! getent group "$SERVICE_USER" >/dev/null 2>&1; then
  groupadd --system "$SERVICE_USER"
fi

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --gid "$SERVICE_USER" --home "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

install -d -m 0755 "$INSTALL_DIR"
install -d -m 0755 "$CONFIG_DIR"

if [[ -n "$CONFIG_SOURCE" ]]; then
  if [[ -f "$CONFIG_FILE" ]]; then
    log "Replacing existing config with --config source: $CONFIG_FILE"
  fi
  install -m 0640 -o root -g "$SERVICE_USER" "$CONFIG_SOURCE" "$CONFIG_FILE"
elif [[ ! -f "$CONFIG_FILE" ]]; then
  cat > "$CONFIG_FILE" <<'JSON'
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
JSON
  chown root:"$SERVICE_USER" "$CONFIG_FILE"
  chmod 0640 "$CONFIG_FILE"
else
  log "Keeping existing config: $CONFIG_FILE"
fi

log "Creating Python virtual environment in ${INSTALL_DIR}/venv"
python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/python" -m pip install --upgrade pip

log "Installing or updating Local LLM Gateway from source: $SOURCE_DIR"
"${INSTALL_DIR}/venv/bin/python" -m pip install --upgrade --force-reinstall "$SOURCE_DIR"
"${INSTALL_DIR}/venv/bin/python" -m pip show local-llm-gateway

chown -R root:root "${INSTALL_DIR}/venv"

log "Writing environment file: $ENV_FILE"
cat > "$ENV_FILE" <<EOF_ENV
LOCAL_LLM_GATEWAY_CONFIG=${CONFIG_FILE}
LOCAL_LLM_GATEWAY_HOST=${HOST}
LOCAL_LLM_GATEWAY_PORT=${PORT}
EOF_ENV
chmod 0644 "$ENV_FILE"

log "Writing systemd service: $SERVICE_FILE"
cat > "$SERVICE_FILE" <<EOF_SERVICE
[Unit]
Description=Local LLM Gateway
Documentation=https://github.com/michellacle/local-llm-gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
EnvironmentFile=${ENV_FILE}
ExecStart=${INSTALL_DIR}/venv/bin/local-llm-gateway --config \${LOCAL_LLM_GATEWAY_CONFIG} --host \${LOCAL_LLM_GATEWAY_HOST} --port \${LOCAL_LLM_GATEWAY_PORT}
Restart=always
RestartSec=5
WorkingDirectory=${INSTALL_DIR}
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=${CONFIG_DIR}

[Install]
WantedBy=multi-user.target
EOF_SERVICE
chmod 0644 "$SERVICE_FILE"

log "Enabling systemd service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

if [[ "$SKIP_START" == "true" ]]; then
  log "Skipping service start by request"
else
  log "Starting systemd service"
  systemctl restart "$SERVICE_NAME"
  if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    systemctl status "$SERVICE_NAME" --no-pager || true
    journalctl -u "$SERVICE_NAME" -n 80 --no-pager || true
    fail "Service did not become active"
  fi
  if ! wait_for_health; then
    systemctl status "$SERVICE_NAME" --no-pager || true
    journalctl -u "$SERVICE_NAME" -n 80 --no-pager || true
    fail "Service is active but health check failed: $(health_check_url)"
  fi
fi

log "Installation complete"
if [[ "$SKIP_START" == "true" ]]; then
  log "Service installed/updated and enabled, but not started because --skip-start was used"
else
  if [[ "$SERVICE_WAS_INSTALLED" == "true" ]]; then
    log "Update successful. Service is running and healthy: $(health_check_url)"
  else
    log "Installation successful. Service is running and healthy: $(health_check_url)"
  fi
fi
log "Config: $CONFIG_FILE"
log "Environment: $ENV_FILE"
log "Service: $SERVICE_FILE"
log "Status: systemctl status ${SERVICE_NAME}"
log "Logs: journalctl -u ${SERVICE_NAME} -f"
