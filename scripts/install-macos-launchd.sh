#!/usr/bin/env bash
set -euo pipefail
trap 'fail "Command failed on line ${LINENO}: ${BASH_COMMAND}"' ERR

LABEL="com.local-llm-gateway"
SERVICE_NAME="local-llm-gateway"
INSTALL_DIR="/usr/local/local-llm-gateway"
CONFIG_DIR="/etc/local-llm-gateway"
CONFIG_FILE="${CONFIG_DIR}/config.json"
PLIST_FILE="/Library/LaunchDaemons/${LABEL}.plist"
STDOUT_LOG="/var/log/local-llm-gateway.out.log"
STDERR_LOG="/var/log/local-llm-gateway.err.log"
HOST="0.0.0.0"
PORT="8090"
SOURCE_DIR="."
CONFIG_SOURCE=""
SKIP_START="false"
HEALTH_CHECK_HOST="127.0.0.1"
SERVICE_WAS_INSTALLED="false"

usage() {
  cat <<USAGE
Install Local LLM Gateway as an always-running launchd service on macOS.

This installer is designed for manual/source checkout installs by a human or AI agent.
It does not install from PyPI.

Usage:
  sudo ./scripts/install-macos-launchd.sh [options]

Options:
  --from-source PATH          Install from a local source checkout. Default: current directory.
  --config PATH               Seed /etc/local-llm-gateway/config.json from PATH.
  --host HOST                 Bind host for the daemon. Default: 0.0.0.0
  --port PORT                 Bind port for the daemon. Default: 8090
  --skip-start                Install the LaunchDaemon but do not load or start it.
  -h, --help                  Show this help.

Examples:
  sudo ./scripts/install-macos-launchd.sh --config ./config.json --port 8090
  sudo ./scripts/install-macos-launchd.sh --from-source . --config ./config.json --port 8090
USAGE
}

log() {
  printf '[local-llm-gateway macOS installer] %s\n' "$*"
}

fail() {
  printf '[local-llm-gateway macOS installer] ERROR: %s\n' "$*" >&2
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

plist_loaded() {
  launchctl print "system/${LABEL}" >/dev/null 2>&1
}

plist_exists() {
  [[ -f "$PLIST_FILE" ]]
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
[[ "$(uname -s)" == "Darwin" ]] || fail "This installer only supports macOS"
command -v launchctl >/dev/null 2>&1 || fail "launchctl is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required. Install Command Line Tools or Python 3 first."

[[ -d "$SOURCE_DIR" ]] || fail "Source directory not found: $SOURCE_DIR"
SOURCE_DIR="$(cd "$SOURCE_DIR" && pwd)"
[[ -f "${SOURCE_DIR}/pyproject.toml" ]] || fail "Source directory must contain pyproject.toml: $SOURCE_DIR"

if [[ -n "$CONFIG_SOURCE" ]]; then
  [[ -f "$CONFIG_SOURCE" ]] || fail "Config file not found: $CONFIG_SOURCE"
fi

if plist_exists || plist_loaded; then
  SERVICE_WAS_INSTALLED="true"
  if [[ "$SKIP_START" == "true" ]]; then
    log "Existing LaunchDaemon detected; leaving runtime state unchanged because --skip-start was used"
  else
    log "Existing LaunchDaemon detected; unloading it before updating installed files"
    launchctl bootout system "$PLIST_FILE" >/dev/null 2>&1 || true
  fi
fi

log "Creating install and config directories"
install -d -m 0755 "$INSTALL_DIR"
install -d -m 0755 "$CONFIG_DIR"

if [[ -n "$CONFIG_SOURCE" ]]; then
  if [[ -f "$CONFIG_FILE" ]]; then
    log "Replacing existing config with --config source: $CONFIG_FILE"
  fi
  install -m 0644 "$CONFIG_SOURCE" "$CONFIG_FILE"
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
  chmod 0644 "$CONFIG_FILE"
else
  log "Keeping existing config: $CONFIG_FILE"
fi

log "Creating Python virtual environment in ${INSTALL_DIR}/venv"
python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/python" -m pip install --upgrade pip

log "Installing or updating Local LLM Gateway from source: $SOURCE_DIR"
"${INSTALL_DIR}/venv/bin/python" -m pip install --upgrade --force-reinstall "$SOURCE_DIR"
"${INSTALL_DIR}/venv/bin/python" -m pip show local-llm-gateway

log "Preparing log files"
touch "$STDOUT_LOG" "$STDERR_LOG"
chmod 0644 "$STDOUT_LOG" "$STDERR_LOG"

log "Writing LaunchDaemon plist: $PLIST_FILE"
cat > "$PLIST_FILE" <<EOF_PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${INSTALL_DIR}/venv/bin/local-llm-gateway</string>
    <string>--config</string>
    <string>${CONFIG_FILE}</string>
    <string>--host</string>
    <string>${HOST}</string>
    <string>--port</string>
    <string>${PORT}</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>LOCAL_LLM_GATEWAY_CONFIG</key>
    <string>${CONFIG_FILE}</string>
    <key>LOCAL_LLM_GATEWAY_HOST</key>
    <string>${HOST}</string>
    <key>LOCAL_LLM_GATEWAY_PORT</key>
    <string>${PORT}</string>
  </dict>
  <key>WorkingDirectory</key>
  <string>${INSTALL_DIR}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${STDOUT_LOG}</string>
  <key>StandardErrorPath</key>
  <string>${STDERR_LOG}</string>
</dict>
</plist>
EOF_PLIST
chown root:wheel "$PLIST_FILE"
chmod 0644 "$PLIST_FILE"

if command -v plutil >/dev/null 2>&1; then
  plutil -lint "$PLIST_FILE"
fi

if [[ "$SKIP_START" == "true" ]]; then
  log "Skipping LaunchDaemon load/start by request"
else
  log "Loading LaunchDaemon"
  launchctl bootstrap system "$PLIST_FILE"
  log "Starting LaunchDaemon"
  launchctl kickstart -k "system/${LABEL}"

  if ! wait_for_health; then
    launchctl print "system/${LABEL}" || true
    tail -n 80 "$STDOUT_LOG" || true
    tail -n 80 "$STDERR_LOG" || true
    fail "LaunchDaemon loaded but health check failed: $(health_check_url)"
  fi
fi

log "Installation complete"
if [[ "$SKIP_START" == "true" ]]; then
  log "LaunchDaemon installed/updated, but not started because --skip-start was used"
else
  if [[ "$SERVICE_WAS_INSTALLED" == "true" ]]; then
    log "Update successful. Service is running and healthy: $(health_check_url)"
  else
    log "Installation successful. Service is running and healthy: $(health_check_url)"
  fi
fi
log "Config: $CONFIG_FILE"
log "Plist: $PLIST_FILE"
log "Status: launchctl print system/${LABEL}"
log "Logs: tail -f ${STDOUT_LOG} ${STDERR_LOG}"
