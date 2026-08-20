#!/usr/bin/env bash
set -euo pipefail

echo "[restart] Restarting local-llm-gateway…"
sudo systemctl restart local-llm-gateway
echo "[restart] Done."
