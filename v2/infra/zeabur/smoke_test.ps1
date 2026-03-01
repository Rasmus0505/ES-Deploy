param(
  [string]$ApiBase = "https://your-v2-api-domain",
  [string]$AdminBase = "https://your-v2-admin-domain",
  [string]$AdminToken = "replace-admin-token"
)

Write-Host "[1/4] API health"
Invoke-RestMethod -Method Get -Uri "$ApiBase/healthz" | Out-Null

Write-Host "[2/4] Admin health"
Invoke-RestMethod -Method Get -Uri "$AdminBase/healthz" | Out-Null

Write-Host "[3/4] Admin model routes"
Invoke-RestMethod -Method Get -Uri "$AdminBase/api/v2/admin/model-routes" -Headers @{"x-admin-token"=$AdminToken} | Out-Null

Write-Host "[4/4] Metrics endpoints"
Invoke-WebRequest -Method Get -Uri "$ApiBase/metrics" | Out-Null
Invoke-WebRequest -Method Get -Uri "$AdminBase/metrics" | Out-Null

Write-Host "Smoke checks done."
