# Acceptance matrix

States reflect observed evidence, not intent. They are updated during final verification.

| Requirement | Evidence | State |
|---|---|---|
| Reproducible initialization | Locked dependencies, Compose, generator, seed and bootstrap scripts | PASS |
| Authentication and workspace/group separation | Backend filters plus real PostgreSQL negative retrieval tests | PASS |
| Declared ingestion formats | 24-file corpus completed; OCR, PDF, image, DOCX and XLSX paths exercised | PASS |
| Real embeddings/model identity | Ollama model and 1024-dimension vectors verified in the local RAG run | PASS |
| Lexical/vector/hybrid retrieval | Real SQL tests plus development/holdout reports | PASS |
| Structured citations | Live RAG answer validated against one direct evidence item per block | PASS |
| Insufficient/ambiguous/conflict behavior | Contracts and grouped 60-case retrieval dataset; answer-level review remains manual | PARTIAL |
| No restricted source in retrieval | Real PostgreSQL negative lexical/vector tests | PASS |
| Replacement/deletion/revocation/jobs | Transactional implementation and 7 real integration tests | PASS |
| Technical error distinct from no evidence | Typed provider errors and HTTP 502/503 paths | PASS |
| Feedback and internal review | API/UI implementation; authenticated UI loaded successfully | PASS |
| UI and narrow-screen inspection | Edge headless screenshots captured for desktop and 390px mobile | PASS |
| Development/holdout evaluation | 60 grouped cases; both reports generated | PASS |
| Secret/publication review | Working-tree scan passed; repository initialized with no secrets committed | PASS |
| GitHub repository and same-SHA CI | Pending valid GitHub CLI authentication | BLOCKED |
