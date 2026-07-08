# Push the required Railway variables for the Weekly Review Pulse cron service.
#
# Prerequisites (run these interactively FIRST — they need your browser/account):
#   railway login
#   railway link            # select the project + service for this repo
#
# Then run this script from the repo root:
#   powershell -File scripts/railway_set_vars.ps1
#
# The Groq API key is read from .env and set via stdin, so the secret never
# appears in the command line, shell history, or logs.

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot ".env"

if (-not (Test-Path $envPath)) {
    throw ".env not found at $envPath — create it (copy .env.example) and add GROQ_API_KEY."
}

# Parse GROQ_API_KEY from .env (strip optional surrounding quotes/whitespace).
$match = Select-String -Path $envPath -Pattern '^\s*GROQ_API_KEY\s*=\s*(.+?)\s*$' | Select-Object -First 1
if (-not $match) {
    throw "GROQ_API_KEY not found in .env."
}
$groqKey = $match.Matches[0].Groups[1].Value.Trim().Trim('"').Trim("'")
if ([string]::IsNullOrWhiteSpace($groqKey)) {
    throw "GROQ_API_KEY is empty in .env."
}

# Confirm a project/service is linked before we try to write anything.
Write-Host "Checking linked project..." -ForegroundColor Cyan
railway status

# 1) Secret: piped via stdin so it is never exposed as an argument.
Write-Host "Setting GROQ_API_KEY (via stdin)..." -ForegroundColor Cyan
$groqKey | railway variable set GROQ_API_KEY --stdin --skip-deploys

# 2) Non-secret configuration.
Write-Host "Setting RUN_CONFIG, PYTHONUNBUFFERED, GROQ_MODEL..." -ForegroundColor Cyan
railway variable set "RUN_CONFIG=config/run_config.prod.yaml" --skip-deploys
railway variable set "PYTHONUNBUFFERED=1" --skip-deploys
railway variable set "GROQ_MODEL=llama-3.3-70b-versatile" --skip-deploys

Write-Host ""
Write-Host "Done. Variables set (deploys skipped)." -ForegroundColor Green
Write-Host "Verify names with:  railway variable list" -ForegroundColor Green
Write-Host "Then trigger a deploy:  railway up   (or redeploy from the dashboard)" -ForegroundColor Green
