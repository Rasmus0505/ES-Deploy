# Gray Release and Rollback Runbook

## Gray release
1. Deploy all v2 services on new subdomains.
2. Smoke test: auth -> redeem -> create job -> complete -> practice submit.
3. Route 10% users (or internal users only) to v2 domain for 24h.
4. Monitor alerts from `alerts.md`.
5. Increase to 50% then 100% only if no P1/P2 incidents.

## Rollback
1. Keep legacy domain and service running in read-only mode for 14 days.
2. If P1 incident occurs:
   - Stop new writes in v2 (`maintenance mode` toggle or API gateway rule).
   - Switch DNS/CNAME back to legacy web and api.
   - Keep v2 DB snapshot for forensic analysis.
3. Publish incident summary and ETA before re-enable.

## P1 definition
- Login unavailable > 5 minutes
- Recharge/consume ledger inconsistency
- Job completion success rate < 70% for 30 minutes
