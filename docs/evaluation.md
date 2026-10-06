# Evaluation

`evals/datasets/questions-v1.jsonl` contains 60 synthetic cases grouped by question family. Development and holdout labels are explicit; variants from the same base document use a stable family so future split changes can avoid sibling leakage.

Cases describe identity, expected behavior, required/forbidden facts and relevant source files. They cover direct questions, paraphrases, exact codes, English, multiple sources, tables, formulas without cached values, insufficient evidence, restricted content, conflict, ambiguity, continuity, revocation, malicious instructions and unsupported aggregation.

Run retrieval evaluation against the same indexed snapshot:

```powershell
python -m knowledgedesk.evaluation.runner --dataset evals/datasets/questions-v1.jsonl --split development --output evals/results/development-YYYYMMDD-HHMMSS.json
```

The report records configuration, sample counts, retrieved IDs, Recall@k, MRR and observed latency. Cases with no relevant document have a null retrieval recall and are excluded from that denominator rather than awarded fictional recall. Vector/hybrid rows are emitted only when a real embedding model is configured.

Answer-level factuality, citation support, correct abstention, unnecessary abstention, conflict handling and leakage require the RAG run plus human review. An LLM judge may be added as diagnosis, never as the sole correctness authority. Results are timestamped artifacts and must not be rewritten to imply improvement.

Final local reports are stored as `evals/results/development-final.json` and `evals/results/holdout-final.json`. Both contain 1.0 Recall@6 and MRR for lexical, vector and hybrid retrieval on the relevant synthetic cases; medians are approximately 9 ms lexical and 640–715 ms vector/hybrid. These are retrieval-only results, not a claim of broad answer quality.
