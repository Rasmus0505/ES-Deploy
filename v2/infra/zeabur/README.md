# Zeabur Deployment (Listening V2)

## Services

Create these services in this order:

1. `postgres` (managed PostgreSQL)
2. `redis` (managed Redis)
3. `v2-api` from repo root with Dockerfile `v2/services/api/Dockerfile`
4. `v2-worker` from repo root with Dockerfile `v2/services/worker/Dockerfile`
5. `v2-web` from repo root with Dockerfile `v2/apps/web/Dockerfile`
6. `v2-admin` from repo root with Dockerfile `v2/services/admin/Dockerfile`

## Required environment variables

Use `env.example` as baseline. Required secrets:

- `DATABASE_URL`
- `REDIS_URL`
- `JWT_SECRET`
- `ADMIN_API_TOKEN`
- `APP_MASTER_KEY`
- `DASHSCOPE_API_KEY`
- `OSS_ACCESS_KEY_ID`
- `OSS_ACCESS_KEY_SECRET`
- `OSS_BUCKET`
- `OSS_ENDPOINT`

Recommended operational variables:

- `AUTO_INIT_DB=false`
- `ENABLE_METRICS=true`
- `WORKER_METRICS_PORT=9101`
- `ENABLE_MOCK_PIPELINE=false`

## Database migration

Run this before first traffic (API runtime):

- `alembic -c alembic.ini upgrade head`

## Health checks

- API: `/healthz`
- API metrics: `/metrics`
- Admin: `/healthz`
- Admin metrics: `/metrics`
- Worker metrics: `:${WORKER_METRICS_PORT}`
- Web: open `/login`

## Release order

1. Deploy `postgres/redis`
2. Deploy `v2-api` and run migration
3. Deploy `v2-worker` (runs worker + beat, includes periodic media cleanup)
4. Deploy `v2-web` and `v2-admin`
5. Verify redeem and job pipeline before switching main domain

## Monitoring and rollback docs

- `alerts.md`
- `rollout_rollback.md`
- `smoke_test.ps1`
- `healthcheck.md`
