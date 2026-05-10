#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec sudo bash "${SCRIPT_DIR}/scripts/install-ubuntu-systemd.sh" --from-source "${SCRIPT_DIR}" --port 8090 "$@"
