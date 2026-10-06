# Decisions and Evidence

## Validated facts

- PostgreSQL 18 with pgvector 0.8.7 ran successfully in Docker on 2026-10-01. A three-dimensional exact cosine query honored workspace and group predicates in SQL. This validates the local database component only; it is not an application authorization test.
- The official pgvector project documentation describes exact nearest-neighbor search as the default and cosine distance as `<=>`; the probe used exact search without an approximate index. Source: https://github.com/pgvector/pgvector
- The official Docling documentation lists PDF/image parsing and OCR support, but Docling has not been installed or tested here. Source: https://docling-project.github.io/docling/
- The official Ollama embeddings documentation shows batch inputs, returns vector lengths from the API, and describes returned vectors as L2-normalized. No Ollama runtime or embedding model was available during discovery. Source: https://docs.ollama.com/capabilities/embeddings
- Docker Compose 5.1.0 and a running Linux Docker daemon were observed. Source: https://docs.docker.com/compose/
- On 2026-10-02, Ollama 0.35.0 ran locally in a CPU-only container pinned to image digest `sha256:2a6e883b917fc543389599dae79918f5cac9e1438890506982f44aa4f5625d01`.
- Real bilingual embedding smoke test passed using `qwen3-embedding:0.6b`, model digest `ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d`, Q8_0, 32K context, and 1024 dimensions. All four returned vectors had norm approximately 1.0; PT and EN query/document cosine values were 0.8332 and 0.8369 in one run. These four prompts are a smoke test, not a retrieval benchmark.
- Real structured generation passed a single-source Portuguese answer with `qwen2.5:1.5b-instruct-q4_K_M`, digest `65ec06548149b04c096a120e4a6da9d4017ea809c91734ea5631e89f96ddc57b`, Q4_K_M, 1.5B parameters, and 32K context. It returned `answered` and the supplied evidence ID. A smaller `qwen2.5:0.5b` returned `conflicting_sources` for a one-source question; the server now rejects conflict statuses unless citations span two distinct ephemeral source groups. This illustrates model output variability.
- The 1.5B generation model used under 2 GiB container memory in the observed request. The final live RAG sample answered the warranty question correctly in about 103 seconds on CPU; throughput and broader quality are not established.

## Provisional decisions

- Prefer PostgreSQL/pgvector exact search for the small demonstration corpus; do not add ANN until measured against exact results.
- Recommend the verified Ollama tags above for the local RAG profile. Keep the adapter configurable and rerun smoke tests after model/runtime upgrades.
- No project license has been selected. Preserve this as an explicit open decision; do not imply a distribution license.

## Remaining boundaries

- Python 3.14.4 is the supported local/container runtime; Python 3.11 is not part of the supported matrix.
- The corpus and evaluation set are synthetic and intentionally narrow. Perfect retrieval scores do not establish production relevance.
- No project license has been selected, no public deployment is included, and remote GitHub CI remains pending valid CLI authentication.
