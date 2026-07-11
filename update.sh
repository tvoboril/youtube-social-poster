#!/usr/bin/env bash
# Pull the latest code and rebuild/restart the stack.
set -euo pipefail
cd "$(dirname "$0")"
git pull
docker compose --profile cloudflare up -d --build
docker compose --profile cloudflare ps
