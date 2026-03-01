#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH="/app/packages/shared_py:${PYTHONPATH:-}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
