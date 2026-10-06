# Architecture

## Boundaries

KnowledgeDesk is a modular monolith. `api` owns HTTP/session boundaries, `ingestion` parses untrusted files, `workers` performs recoverable background work, `retrieval` owns authorized SQL, `generation` validates model output, and `persistence` owns schema/migrations. The Jinja/JavaScript interface calls only the HTTP API.

PostgreSQL stores application state, full-text vectors, pgvector values and durable jobs. Originals are stored under opaque names in a private volume. Ollama is optional and receives only the current question plus already-authorized evidence text.

## Main flow

1. An editor uploads bytes; the API validates type/limits, writes an opaque file, and atomically commits a logical document, immutable version and queued job.
2. A worker claims a row with `FOR UPDATE SKIP LOCKED`, records a lease, parses the original and optionally embeds chunks.
3. The worker locks job/document/version again. Deletion, revocation, a changed security epoch or a newer active version prevents activation.
4. Search joins only current, ready versions and the active index generation. Workspace and group predicates are applied in the same SQL statement.
5. RAG mode sends bounded evidence to Ollama. Pydantic validates status and evidence IDs; the server revalidates every source before persisting and returning the response.

## Consistency and lifecycle

- Logical document: stable user-visible identity and current access policy.
- Document version: immutable bytes/hash/provenance and processing state.
- Index generation: embedding model/dimension/chunker configuration.
- Replacement: the old version stays active until the new job commits successfully.
- Deletion/revocation: clears the active pointer and increments `security_epoch` immediately. Physical purge remains observable as `pending` until an operator removes retained bytes according to policy.
- Queue: at-least-once processing. Unique constraints, leases and transactional activation make retries idempotent.

## Invariant ownership

Database constraints guarantee foreign keys, unique document-version numbers, job identity and enumerated states. Application transactions guarantee authorization, active-version swaps, security-epoch checks and evidence resolution. Semantic support, relevance and answer quality are evaluated rather than claimed as deterministic guarantees.

## Trade-offs

The database queue avoids Redis and an enqueue/commit gap for this small deployment. Exact vector search is preferred for the 24-document demonstration. PostgreSQL RLS is not enabled; explicit query filters and negative integration tests are the current defense. A future RLS layer must use the real non-owner application role and transaction-local identity.
