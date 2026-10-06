param(
    [ValidateSet("search", "demo", "rag")]
    [string]$Mode = "search"
)
$ErrorActionPreference = "Stop"
if (-not $env:KD_DEMO_PASSWORD -or $env:KD_DEMO_PASSWORD.Length -lt 12) {
    throw "Set KD_DEMO_PASSWORD to a local-only password of at least 12 characters."
}
$env:KD_MODE = $Mode
& .\.venv\Scripts\python.exe scripts\generate_demo_assets.py
if ($LASTEXITCODE -ne 0) { throw "asset generation failed" }
docker compose build migrate web worker
if ($LASTEXITCODE -ne 0) { throw "application image build failed" }
docker compose up -d db
if ($LASTEXITCODE -ne 0) { throw "database startup failed" }
docker compose run --rm migrate
if ($LASTEXITCODE -ne 0) { throw "migration failed" }
docker compose run --rm -e KD_DEMO_PASSWORD web python scripts/seed_demo.py
if ($LASTEXITCODE -ne 0) { throw "demo seed failed" }
docker compose up -d web worker
if ($LASTEXITCODE -ne 0) { throw "application startup failed" }
Write-Output "KnowledgeDesk is starting at http://127.0.0.1:8000 (mode: $Mode)."
