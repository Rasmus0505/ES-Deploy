from __future__ import annotations

import datetime as dt
import secrets
import string
import time
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy.orm import Session

from listening_v2_shared.config import get_settings
from listening_v2_shared.db import SessionLocal, init_db
from listening_v2_shared.model_routes import ensure_default_model_routes
from listening_v2_shared.models import ModelRoute, RedeemCode

settings = get_settings()
app = FastAPI(title='Listening V2 Admin', version='2.0.0')
REQUEST_TOTAL = Counter('listening_v2_admin_requests_total', 'Admin HTTP requests', ['method', 'path', 'status_code'])
REQUEST_DURATION = Histogram('listening_v2_admin_request_duration_seconds', 'Admin request duration', ['method', 'path'])


class CreateCodesPayload(BaseModel):
    count: int = Field(default=10, ge=1, le=1000)
    credits: int = Field(default=1000, ge=1)
    expires_days: int = Field(default=30, ge=1, le=365)
    prefix: str = Field(default='V2')


class RouteItem(BaseModel):
    model_name: str
    enabled: bool
    cost_per_unit: float = Field(ge=0)
    multiplier: float = Field(ge=0)


class PatchRoutesPayload(BaseModel):
    items: list[RouteItem]


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def require_admin(request: Request) -> None:
    token = str(request.headers.get('x-admin-token') or '').strip()
    if token != settings.admin_api_token:
        raise HTTPException(status_code=403, detail='admin_forbidden')


@app.middleware('http')
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration = max(0.0, time.perf_counter() - start)
    REQUEST_DURATION.labels(method=request.method, path=request.url.path).observe(duration)
    REQUEST_TOTAL.labels(method=request.method, path=request.url.path, status_code=str(response.status_code)).inc()
    return response


@app.on_event('startup')
def startup() -> None:
    if settings.auto_init_db:
        init_db()
    with SessionLocal() as db:
        ensure_default_model_routes(db)
        db.commit()


@app.get('/healthz')
def healthz():
    return {'status': 'ok'}


@app.get('/metrics')
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get('/', response_class=HTMLResponse)
def index():
    return """
    <html>
      <head><title>Listening V2 Admin</title></head>
      <body>
        <h1>Listening V2 Admin</h1>
        <p>Use header <code>x-admin-token</code> to access admin APIs.</p>
        <ul>
          <li>POST /api/v2/admin/redeem-codes</li>
          <li>PATCH /api/v2/admin/model-routes</li>
          <li>GET /api/v2/admin/model-routes</li>
        </ul>
      </body>
    </html>
    """


@app.post('/api/v2/admin/redeem-codes')
def create_codes(payload: CreateCodesPayload, request: Request, _: None = Depends(require_admin), db: Session = Depends(get_db)):
    alphabet = string.ascii_uppercase + string.digits
    expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=payload.expires_days)
    codes: list[str] = []
    while len(codes) < payload.count:
        body = ''.join(secrets.choice(alphabet) for _ in range(12))
        code = f"{payload.prefix.upper()[:6]}{body}"
        if db.get(RedeemCode, code):
            continue
        db.add(RedeemCode(code=code, credits=payload.credits, status='active', created_by='admin', expires_at=expires_at))
        codes.append(code)
    db.flush()
    return {'requestId': uuid.uuid4().hex, 'code': 'ok', 'message': 'created', 'data': {'codes': codes, 'count': len(codes)}}


@app.patch('/api/v2/admin/model-routes')
def patch_routes(payload: PatchRoutesPayload, request: Request, _: None = Depends(require_admin), db: Session = Depends(get_db)):
    ensure_default_model_routes(db)
    now = dt.datetime.now(dt.timezone.utc)
    out: list[dict] = []
    for item in payload.items:
        row = db.get(ModelRoute, item.model_name)
        if row is None:
            row = ModelRoute(model_name=item.model_name)
            db.add(row)
        row.enabled = item.enabled
        row.cost_per_unit = item.cost_per_unit
        row.multiplier = item.multiplier
        row.updated_at = now
        out.append({'modelName': row.model_name, 'enabled': row.enabled, 'costPerUnit': float(row.cost_per_unit), 'multiplier': float(row.multiplier)})
    db.flush()
    return {'requestId': uuid.uuid4().hex, 'code': 'ok', 'message': 'patched', 'data': {'items': out}}


@app.get('/api/v2/admin/model-routes')
def list_routes(request: Request, _: None = Depends(require_admin), db: Session = Depends(get_db)):
    ensure_default_model_routes(db)
    rows = db.query(ModelRoute).all()
    return {
        'requestId': uuid.uuid4().hex,
        'code': 'ok',
        'message': 'ok',
        'data': {
            'items': [
                {
                    'modelName': row.model_name,
                    'enabled': bool(row.enabled),
                    'costPerUnit': float(row.cost_per_unit),
                    'multiplier': float(row.multiplier),
                }
                for row in rows
            ]
        },
    }
