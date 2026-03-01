# API
curl -sS https://your-v2-api-domain/healthz

# Admin
curl -sS https://your-v2-admin-domain/healthz

# Auth register
curl -X POST https://your-v2-api-domain/api/v2/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@example.com","password":"P@ssw0rd123"}'

# Create redeem codes (admin)
curl -X POST https://your-v2-admin-domain/api/v2/admin/redeem-codes \
  -H "x-admin-token: ${ADMIN_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"count":2,"credits":5000,"expires_days":30,"prefix":"V2"}'
