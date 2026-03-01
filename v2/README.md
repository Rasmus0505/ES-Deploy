# Listening V2 (Rebuild)

This `v2/` directory is an isolated rewrite and does not affect legacy services under `frontend-v2/` and `backend/`.

## Architecture

- `apps/web`: Next.js web app
- `services/api`: FastAPI business API (`/api/v2/*`)
- `services/worker`: Celery worker for async pipeline
- `services/admin`: FastAPI admin panel and admin APIs
- `packages/shared_py`: shared DB models and config
- `infra/zeabur`: deployment docs, monitoring, rollout and rollback

## Core features included

- Login/register + cross-device data sync by account
- Job creation via URL or video upload
- Async pipeline with queue states, retry and cancel endpoints
- Exercise generation: sentence-by-sentence, word-by-word dictation workflow
- Wallet credits and redeem-code charging (no monthly reset)
- User data deletion endpoint (DB + media references)
- Admin redeem-code creation and model-route patching
- Prometheus metrics endpoints for API/Admin and worker metrics port

## Media retention policy

- Source media expires after `KEEP_SOURCE_HOURS` (default 24h).
- Intermediate sentence clips expire after `KEEP_INTERMEDIATE_HOURS` (default 72h).
- Learning records and wallet ledger stay in database for cross-device continuity.

## Not included (intentionally removed)

- OneAPI dependency in core path
- WeChat/Alipay direct payment
- Legacy V1 compatibility layer

## API response contract

Every API returns:

```json
{
  "requestId": "...",
  "code": "ok|...",
  "message": "...",
  "data": {}
}
```

## Quick local run

1. Start PostgreSQL and Redis:
   - `docker compose -f v2/docker-compose.yml up -d`
2. Export envs from `v2/services/api/.env.example` and `v2/services/worker/.env.example`.
3. Run database migration:
   - `cd v2/services/api`
   - `pip install -r requirements.txt`
   - `alembic -c alembic.ini upgrade head`
4. Run API:
   - `uvicorn app.main:app --reload --port 8080`
5. Run Worker:
   - `cd v2/services/worker`
   - `pip install -r requirements.txt`
   - `celery -A app.worker_app worker --loglevel=INFO`
6. Run Admin:
   - `cd v2/services/admin`
   - `pip install -r requirements.txt`
   - `uvicorn app.main:app --reload --port 8082`
7. Run Web:
   - `cd v2/apps/web`
   - `npm install`
   - `NEXT_PUBLIC_API_BASE_URL=http://localhost:8080 npm run dev`

## Tests

- `cd v2`
- `pytest`

## Monitoring

- API: `/metrics`
- Admin: `/metrics`
- Worker: `WORKER_METRICS_PORT` (default `9101`)

See `v2/infra/zeabur/alerts.md` for alert thresholds.
