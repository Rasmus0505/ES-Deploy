# Monitoring and Alerts

## Metrics endpoints
- API: `/metrics`
- Admin: `/metrics`
- Worker: `:${WORKER_METRICS_PORT}` (prometheus pull)

## Alert thresholds (recommended)
- `listening_v2_http_requests_total{status_code=~"5.."}` 5-minute error ratio > 3%
- API p95 latency (`listening_v2_http_request_duration_seconds`) > 2.5s for 10 minutes
- Worker pipeline failures (`listening_v2_worker_pipeline_total{status="failed"}`) > 5 in 10 minutes
- Queue backlog (Redis queue depth) > 100 for 10 minutes
- Wallet incidents: any duplicate `idempotency_key` insert errors

## Mandatory dashboards
- API request rate / error rate / p95 latency
- Worker success/fail counts and duration histogram
- Job stage distribution (`queued/running/succeeded/failed`)
- Credits consume vs redeem trend
