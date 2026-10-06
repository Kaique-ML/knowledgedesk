# KnowledgeDesk

KnowledgeDesk is a portfolio-grade corporate knowledge assistant for a fictional distributor. It ingests controlled documents, retrieves only sources the signed-in user may access, and either shows search evidence or generates a source-bound answer through a configured local Ollama model.

The project demonstrates document processing, PostgreSQL full-text search, pgvector retrieval, authorization, recoverable ingestion, source validation, human review, and reproducible evaluation. It does not claim to eliminate hallucinations or prompt injection and is not presented as production-ready for arbitrary organizations.

## What is implemented

- FastAPI/Jinja application with password sessions, CSRF protection and admin/editor/reader roles.
- Workspace, knowledge-base and group-scoped authorization enforced before retrieval and source delivery.
- Content-validated PDF, scanned PDF, PNG/JPEG, Markdown, DOCX and XLSX ingestion.
- Persistent PostgreSQL job queue with leases, retry limits, idempotency and security epochs.
- Logical documents, immutable content versions and explicit index generations.
- PostgreSQL lexical search, exact pgvector cosine search and hybrid reciprocal-rank fusion.
- Structured Ollama answers with opaque evidence IDs and server-resolved citations.
- Document replacement, access changes, logical deletion, protected originals and protected history.
- Feedback and an internal human-review queue.
- A deterministic 24-document synthetic corpus and 60 grouped development/holdout cases.
- Docker Compose, Alembic, locked Python dependencies and GitHub Actions CI.

## Architecture

```text
browser -> FastAPI/Jinja -> PostgreSQL + pgvector
             |                    ^
             v                    |
       private file volume -> ingestion worker -> Ollama (RAG mode only)
```

The database is also the recoverable queue. Upload commits the document version and job together; workers claim jobs with row locks and leases. Search SQL applies workspace, group, active-version, validity and index-generation predicates before a chunk can reach the model.

See [architecture](docs/architecture.md), [retrieval](docs/retrieval.md), [security](docs/security.md), and [decisions](docs/decisions.md).

## Requirements

- Docker Desktop with Linux containers and Compose.
- Python 3.14 only for host-side development (the container pins Python 3.14.4).
- About 1.5 GB free memory for search mode. RAG mode additionally needs Ollama model memory and disk.
- Ollama plus the documented models only for real vector/RAG mode.

## Quickstart: real lexical search without an LLM

```powershell
Copy-Item .env.example .env
$env:KD_DEMO_PASSWORD = Read-Host "Local demo password"
.\scripts\bootstrap.ps1 -Mode search
```

Open `http://127.0.0.1:8000`. Synthetic local accounts are `admin@demo.invalid`, `sales@demo.invalid`, `finance@demo.invalid`, and `reader@demo.invalid`; all use the password supplied at seed time. These accounts are local fixtures, never deployment credentials.

## Simulated UI/contract mode

```powershell
$env:KD_DEMO_PASSWORD = Read-Host "Local demo password"
.\scripts\bootstrap.ps1 -Mode demo
```

The UI labels this mode as simulated. It exercises application contracts and search sources; it is not included in AI-quality metrics and does not impersonate a real model.

## Real local RAG

Prepare Ollama separately. The locally verified lightweight configuration is:

- Embeddings: `qwen3-embedding:0.6b`, 1024 dimensions.
- Generation: `qwen2.5:1.5b-instruct-q4_K_M`.

Then configure `.env` and run:

```powershell
$env:KD_MODE = "rag"
$env:KD_EMBEDDING_MODEL = "qwen3-embedding:0.6b"
$env:KD_EMBEDDING_DIMENSIONS = "1024"
$env:KD_GENERATION_MODEL = "qwen2.5:1.5b-instruct-q4_K_M"
$env:KD_DEMO_PASSWORD = Read-Host "Local demo password"
.\scripts\bootstrap.ps1 -Mode rag
```

Model downloads are intentionally not automatic. Review model licenses, disk size and memory requirements before pulling them. Changing an embedding model requires a new index generation; vectors from different models are never treated as interchangeable.

## Verification

```powershell
.\scripts\test-all.ps1
```

Run retrieval evaluation only after seeding/indexing:

```powershell
python -m knowledgedesk.evaluation.runner `
  --dataset evals/datasets/questions-v1.jsonl `
  --split development `
  --output evals/results/development-$(Get-Date -Format yyyyMMdd-HHmmss).json
```

Results are generated artifacts and are not prefilled. See [testing](docs/testing.md), [evaluation](docs/evaluation.md), and the factual [acceptance matrix](docs/acceptance.md).

## Limits

- OCR extracts text; it does not provide general visual understanding of charts or diagrams.
- XLSX formulas are never executed. Cached results may be absent or stale.
- Numeric aggregation over an entire workbook is refused unless a bounded structured operation is added.
- Search relevance and generated answer quality are probabilistic and corpus-dependent.
- Logical deletion blocks new access immediately; physical purge/backup retention requires an operator policy.
- PostgreSQL row-level security is not enabled; negative integration tests cover the application filters.
- No public deployment, paid provider, customer data, or external messaging integration is included.

No distribution license has been selected yet.
