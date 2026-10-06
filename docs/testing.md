# Testing record

Last updated: 2026-10-05 (America/Manaus).

## Commands executed locally

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit -ra --basetemp=.test-tmp -p no:cacheprovider
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy src
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-integration.ps1
```

Observed before final publication review:

- Unit: 17 passed.
- PostgreSQL/pgvector integration: 7 passed using the pinned PostgreSQL 18/pgvector 0.8.7 image.
- Ruff: passed on source, tests and scripts.
- Mypy: passed on 32 source files.
- Full synthetic corpus: 24/24 jobs ready, including OCR and XLSX cached-value extraction.
- Retrieval evaluation: development and holdout reports generated with Recall@6 and MRR of 1.0 for lexical, vector and hybrid methods on the relevant cases.
- Live RAG: warranty question answered with the expected 24-month value and one direct citation; measured CPU-only latency was about 103 seconds.
- Security negative check: the sales identity received no finance-only marker or finance document title.
- UI: authenticated Edge headless checks passed with 24 documents in RAG mode; desktop and 390px mobile screenshots are in `docs/screenshots/`.

The integration script starts an ephemeral test database, applies Alembic migrations, executes real lexical/vector authorization and worker recovery tests, then removes only its named test container/network. It does not delete project data volumes.

## Not yet claimed

Remote GitHub CI has not yet run because the local GitHub CLI token is no longer valid. The local RAG quality sample is narrow and synthetic; its perfect retrieval scores must not be generalized to production corpora.
