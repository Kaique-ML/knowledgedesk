$ErrorActionPreference = "Stop"
$project = "knowledgedesk-integration"
try {
    docker compose -p $project -f compose.test.yaml up -d --wait test-db
    $env:KD_TEST_DATABASE_URL = "postgresql+psycopg://postgres:integration-only@127.0.0.1:55433/knowledgedesk_test"
    $env:KD_DATABASE_URL = $env:KD_TEST_DATABASE_URL
    & .\.venv\Scripts\python.exe -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "migration test failed" }
    & .\.venv\Scripts\python.exe -m pytest tests\integration -ra --basetemp=.test-tmp-integration -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { throw "integration tests failed" }
}
finally {
    docker compose -p $project -f compose.test.yaml down --remove-orphans
}
