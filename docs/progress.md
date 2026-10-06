# Project Progress

Last validated: 2026-10-05 (America/Manaus).

KnowledgeDesk is implemented as a reproducible local portfolio project. The final local validation used PostgreSQL 18/pgvector 0.8.7, Ollama, `qwen3-embedding:0.6b` (1024 dimensions), and `qwen2.5:1.5b-instruct-q4_K_M`.

- 24 synthetic documents indexed successfully, including Markdown, PDF, scanned PDF, PNG, DOCX and XLSX.
- All 24 ingestion jobs reached `ready`; OCR and XLSX cached-value paths were exercised.
- Development and holdout retrieval reports were generated from the 60-case dataset.
- Unit, static-analysis, PostgreSQL/pgvector integration and authenticated Edge desktop/mobile checks passed.
- A live RAG question returned the expected 24-month warranty answer with a direct citation. CPU-only generation took about 103 seconds.
- A sales-user negative retrieval check did not expose the finance-only marker or finance documents.

The remaining external step is publication to GitHub. It requires a valid GitHub CLI login and a selected public repository name.
