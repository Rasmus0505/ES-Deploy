FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/packages/shared_py

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl && rm -rf /var/lib/apt/lists/*

COPY v2/services/worker/requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip && pip install -r /tmp/requirements.txt

COPY v2/packages/shared_py /app/packages/shared_py
COPY v2/services/worker /app/services/worker

WORKDIR /app/services/worker

CMD ["sh", "-c", "celery -A app.worker_app worker --loglevel=INFO --concurrency=2 --beat"]
