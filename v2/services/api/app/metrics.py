from __future__ import annotations

import time
from typing import Callable

from fastapi import Request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

HTTP_REQUESTS_TOTAL = Counter(
    'listening_v2_http_requests_total',
    'Total HTTP requests',
    ['method', 'path', 'status_code'],
)
HTTP_REQUEST_DURATION = Histogram(
    'listening_v2_http_request_duration_seconds',
    'HTTP request duration seconds',
    ['method', 'path'],
)


def _normalize_path(path: str) -> str:
    if not path:
        return '/'
    if path.startswith('/api/v2/jobs/'):
        return '/api/v2/jobs/{job_id}'
    if path.startswith('/api/v2/exercises/items/') and path.endswith('/audio'):
        return '/api/v2/exercises/items/{item_id}/audio'
    if path.startswith('/api/v2/exercises/') and path.endswith('/attempts'):
        return '/api/v2/exercises/{exercise_id}/attempts'
    if path.startswith('/api/v2/exercises/'):
        return '/api/v2/exercises/{exercise_id}'
    return path


async def metrics_middleware(request: Request, call_next: Callable):
    start = time.perf_counter()
    path = _normalize_path(request.url.path)
    response = await call_next(request)
    duration = max(0.0, time.perf_counter() - start)
    HTTP_REQUEST_DURATION.labels(method=request.method, path=path).observe(duration)
    HTTP_REQUESTS_TOTAL.labels(method=request.method, path=path, status_code=str(response.status_code)).inc()
    return response


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
