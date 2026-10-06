# Retrieval

## Modes

- Lexical uses PostgreSQL `plainto_tsquery('simple', ...)`, `ts_rank_cd`, and an escaped substring fallback useful for exact product codes. This is PostgreSQL full-text search, not BM25.
- Vector uses exact pgvector cosine distance. Results must match the active generation's model identifier and vector dimensions.
- Hybrid combines lexical and vector rankings with reciprocal-rank fusion (RRF, constant 60). It does not average incomparable raw scores.

All paths filter membership, workspace, knowledge base, group visibility, logical deletion/revocation, active version, version validity, ready index and active generation before returning chunks. The same filter is rerun after generation.

Candidate `top_k` and final context are separate. Context defaults to six results, at most two chunks per logical document and 16,000 characters. These character limits are approximations, not tokenizer counts. Repetitive overlap is deduplicated by persistent chunk ID during RRF.

## Known behavior

The `simple` text-search configuration preserves cross-language tokens and codes but does not stem Portuguese or English. Exact-code substring matching complements it. No reranker, query rewriting or ANN index is enabled because no measured improvement currently justifies them.

Scores are ranking signals, not probabilities. The application does not use a universal similarity threshold as proof of sufficient evidence. Missing candidates produce an explicit insufficient-evidence response; broader semantic sufficiency is measured in the evaluation suite.
