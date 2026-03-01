from __future__ import annotations

import traceback
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from listening_v2_shared.db import init_db
from listening_v2_shared.config import get_settings
from listening_v2_shared.model_routes import ensure_default_model_routes

from .db import session_scope
from .metrics import metrics_middleware, metrics_response
from .response import fail
from .routers import admin, auth, exercises, health, jobs, me, wallet

settings = get_settings()
app = FastAPI(title='Listening V2 API', version='2.0.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.middleware('http')
async def request_context_middleware(request: Request, call_next):
    request.state.request_id = uuid.uuid4().hex
    response = await call_next(request)
    response.headers['x-request-id'] = request.state.request_id
    return response


if settings.enable_metrics:
    app.middleware('http')(metrics_middleware)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    payload = fail(
        request_id=str(getattr(request.state, 'request_id', '')),
        code='http_error',
        message=str(exc.detail),
        data={'statusCode': exc.status_code},
    )
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    payload = fail(
        request_id=str(getattr(request.state, 'request_id', '')),
        code='validation_error',
        message='request_validation_failed',
        data={'errors': exc.errors()},
    )
    return JSONResponse(status_code=422, content=payload)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    traceback.print_exc()
    payload = fail(
        request_id=str(getattr(request.state, 'request_id', '')),
        code='internal_error',
        message=str(exc),
    )
    return JSONResponse(status_code=500, content=payload)


@app.on_event('startup')
def startup_event() -> None:
    if settings.auto_init_db:
        init_db()
    with session_scope() as db:
        ensure_default_model_routes(db)


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(wallet.router)
app.include_router(jobs.router)
app.include_router(exercises.router)
app.include_router(me.router)
app.include_router(admin.router)


@app.get('/metrics')
def metrics():
    return metrics_response()
