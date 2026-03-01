# Zeabur AI copy/paste instructions

Create a new project named `listening-v2-prod`.

1) Create managed `PostgreSQL` service.
2) Create managed `Redis` service.
3) Deploy service `v2-api`:
- Build from repo root.
- Dockerfile path: `v2/services/api/Dockerfile`.
- Expose HTTP port from container.

4) Deploy service `v2-worker`:
- Build from repo root.
- Dockerfile path: `v2/services/worker/Dockerfile`.
- Note: this container runs celery worker + beat for scheduled cleanup.

5) Deploy service `v2-web`:
- Build from repo root.
- Dockerfile path: `v2/apps/web/Dockerfile`.
- Set `NEXT_PUBLIC_API_BASE_URL` to the public URL of `v2-api`.

6) Deploy service `v2-admin`:
- Build from repo root.
- Dockerfile path: `v2/services/admin/Dockerfile`.

7) Set env vars for API and Worker:
- `DATABASE_URL` from PostgreSQL connection.
- `REDIS_URL` from Redis connection.
- `JWT_SECRET` strong random string.
- `ADMIN_API_TOKEN` strong random string.
- `APP_MASTER_KEY` strong random string.
- `DASHSCOPE_API_KEY` valid DashScope key.
- `DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`
- `MAX_VIDEO_MINUTES=20`
- `KEEP_SOURCE_HOURS=24`
- `KEEP_INTERMEDIATE_HOURS=72`
- `ENABLE_MOCK_PIPELINE=false`
- `AUTO_INIT_DB=false`
- `ENABLE_METRICS=true`
- `WORKER_METRICS_PORT=9101`

8) Set env vars for Admin:
- `DATABASE_URL`
- `ADMIN_API_TOKEN`
- `AUTO_INIT_DB=false`
- `ENABLE_METRICS=true`

9) Run DB migration once on API service:
- `alembic -c alembic.ini upgrade head`

10) Health check after deploy:
- API: `GET /healthz` returns 200.
- API: `GET /metrics` returns text metrics.
- Admin: `GET /healthz` returns 200.
- Admin: `GET /metrics` returns text metrics.
- Web: `/login` loads.
- Worker metrics exposed on `WORKER_METRICS_PORT`.

11) Smoke test:
- Register user from web.
- Create redeem code from admin API.
- Redeem from web.
- Create URL or upload job.
- Poll job status to `succeeded`.
- Open practice page and submit one sentence.

12) Rollback readiness:
- Keep legacy domains unchanged during gray release.
- Prepare DNS switch back plan before 100% cutover.
