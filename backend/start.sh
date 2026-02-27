#!/usr/bin/env bash
set -euo pipefail

PORT_VALUE="${PORT:-8766}"
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT_VALUE"

