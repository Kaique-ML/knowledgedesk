$ErrorActionPreference = "Stop"
& .\.venv\Scripts\python.exe -m ruff check .
if ($LASTEXITCODE -ne 0) { throw "ruff failed" }
& .\.venv\Scripts\python.exe -m mypy src
if ($LASTEXITCODE -ne 0) { throw "mypy failed" }
& .\.venv\Scripts\python.exe -m pytest tests\unit -ra --basetemp=.test-tmp-unit -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw "unit tests failed" }
& powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-integration.ps1
if ($LASTEXITCODE -ne 0) { throw "integration tests failed" }
