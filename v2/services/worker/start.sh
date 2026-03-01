#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH="/app/packages/shared_py:${PYTHONPATH:-}"
exec celery -A app.worker_app worker --loglevel=INFO --concurrency=2 --beat
