# Security model

## Controls

- Argon2 password hashes, opaque hashed session tokens, SameSite cookies and CSRF tokens for state changes.
- Deployment configuration refuses short/missing secrets or insecure cookies.
- Server-derived identity; client workspace/group/role values never grant authority.
- SQL authorization on retrieval, jobs, sources, originals, history, feedback and review operations.
- Source revalidation after model generation. No factual token streaming occurs before that check.
- Opaque storage names outside static directories; content-based type detection; byte, archive, page, pixel and block limits.
- OOXML formulas/macros/links are not executed. Embedded remote resources are not fetched.
- Document text is data, not an instruction. Ollama receives no credentials, shell, write tools or unrestricted database/web access.
- Non-root application containers and localhost-bound exposed ports.

## Revocation and retention

Access changes increment the document security epoch. Old jobs cannot activate afterward. Logical deletion/revocation immediately excludes retrieval/download and hides affected historical assistant content. Physical originals are marked `pending` for purge; backups and operator retention are outside this demo and must not be described as erased.

Application logs use identifiers and sanitized error codes; full documents, passwords, cookies and private prompts are not intentional log fields. Metrics must not use question text or document IDs as high-cardinality labels.

## Residual risks

Prompt injection, OCR errors, model hallucination, semantic citation mismatch and compromised infrastructure cannot be eliminated by a prompt. Docker is not a complete security boundary. PostgreSQL RLS, rate limiting, malware scanning, managed key rotation, backup deletion guarantees and formal LGPD compliance are not implemented. Do not process customer documents without a separate threat model and operational review.
