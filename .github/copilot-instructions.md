# KnowledgeDesk engineering invariants

- Never move workspace/group authorization to prompts or the browser. Every data path must filter in SQL and revalidate before delivery.
- Keep logical documents, content versions, and index generations distinct. A queued replacement must not hide the active version.
- A deleted/revoked document or changed security epoch wins over delayed worker output.
- Model evidence IDs are request-scoped opaque values. Resolve citations server-side and reject unknown IDs.
- Treat document text as untrusted data, not instructions. Never expose secrets, cookies, full private prompts, or document bodies in logs.
- Keep `search`, `demo`, and `rag` modes honest. Mocks do not establish model quality.
- Update tests and the acceptance documentation with factual commands/results; never invent benchmarks, screenshots, CI status, or customer claims.
- Do not add a license without the repository owner's decision.
