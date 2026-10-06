from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledgedesk.evaluation.metrics import recall_at_k, reciprocal_rank
from knowledgedesk.persistence.database import engine
from knowledgedesk.persistence.models import KnowledgeBase, User, Workspace
from knowledgedesk.providers.ollama import OllamaProvider
from knowledgedesk.retrieval.service import hybrid_search, lexical_search, vector_search
from knowledgedesk.settings import get_settings


def _load(path: Path, split: str) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("evaluation IDs must be unique")
    return [case for case in cases if split == "all" or case["split"] == split]


async def run(dataset: Path, output: Path, *, split: str, top_k: int) -> dict[str, Any]:
    settings = get_settings()
    cases = _load(dataset, split)
    provider = None
    if settings.embedding_model:
        provider = OllamaProvider(
            settings.ollama_url,
            embedding_model=settings.embedding_model,
            generation_model=settings.generation_model or "",
        )
    rows: list[dict[str, Any]] = []
    with Session(engine) as session:
        workspace = session.scalar(select(Workspace).where(Workspace.name == "Aurora Demo"))
        if workspace is None:
            raise RuntimeError("demo workspace not found; run the seed first")
        base = session.scalar(
            select(KnowledgeBase).where(
                KnowledgeBase.workspace_id == workspace.id,
                KnowledgeBase.name == "Corporate Knowledge",
            )
        )
        if base is None:
            raise RuntimeError("demo knowledge base not found")
        users = {
            key: session.scalar(select(User).where(User.email == f"{key}@demo.invalid"))
            for key in ("admin", "sales", "finance", "reader")
        }
        for case in cases:
            user = users.get(case["identity"])
            if user is None:
                raise RuntimeError(f"demo identity not found: {case['identity']}")
            lexical_started = time.perf_counter()
            lexical = lexical_search(
                session,
                user_id=user.id,
                workspace_id=workspace.id,
                base_id=base.id,
                query=case["question"],
                limit=top_k,
            )
            timings = {"lexical": (time.perf_counter() - lexical_started) * 1000}
            vector = []
            if provider is not None:
                vector_started = time.perf_counter()
                query_vector = (await provider.embed([case["question"]], query=True))[0]
                vector = vector_search(
                    session,
                    user_id=user.id,
                    workspace_id=workspace.id,
                    base_id=base.id,
                    query_vector=query_vector,
                    model_name=settings.embedding_model or "",
                    limit=top_k,
                )
                timings["vector"] = (time.perf_counter() - vector_started) * 1000
            methods = {"lexical": lexical}
            if provider is not None:
                methods["vector"] = vector
                hybrid_started = time.perf_counter()
                methods["hybrid"] = hybrid_search(lexical, vector, limit=top_k)
                fusion_ms = (time.perf_counter() - hybrid_started) * 1000
                timings["hybrid"] = timings["lexical"] + timings["vector"] + fusion_ms
            relevant = {f"demo:{name}" for name in case["required_sources"]}
            for method, hits in methods.items():
                retrieved = [hit.document.conflict_key or "" for hit in hits]
                rows.append(
                    {
                        "case_id": case["id"],
                        "split": case["split"],
                        "method": method,
                        "retrieved": retrieved,
                        "recall_at_k": recall_at_k(retrieved, relevant, top_k),
                        "reciprocal_rank": reciprocal_rank(retrieved, relevant),
                        "latency_ms": round(timings[method], 3),
                    }
                )
    summary: dict[str, Any] = {}
    for method in sorted({row["method"] for row in rows}):
        method_rows = [row for row in rows if row["method"] == method]
        recalls = [row["recall_at_k"] for row in method_rows if row["recall_at_k"] is not None]
        reciprocal_ranks = [
            row["reciprocal_rank"] for row in method_rows if row["reciprocal_rank"] is not None
        ]
        latencies = [row["latency_ms"] for row in method_rows]
        summary[method] = {
            "evaluated_cases": len(method_rows),
            "relevance_denominator": len(recalls),
            "recall_at_k": statistics.fmean(recalls) if recalls else None,
            "mrr": statistics.fmean(reciprocal_ranks) if reciprocal_ranks else None,
            "latency_ms_median": statistics.median(latencies) if latencies else None,
        }
    report = {
        "manifest": {
            "created_at": datetime.now(UTC).isoformat(),
            "dataset": str(dataset),
            "split": split,
            "top_k": top_k,
            "python": platform.python_version(),
            "embedding_model": settings.embedding_model,
            "mode": settings.mode,
            "note": "No-answer cases are excluded from retrieval recall denominators.",
        },
        "summary": summary,
        "cases": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("evals/datasets/questions-v1.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=("development", "holdout", "all"), default="development")
    parser.add_argument("--top-k", type=int, default=6)
    args = parser.parse_args()
    if not 1 <= args.top_k <= 20:
        raise SystemExit("--top-k must be between 1 and 20")
    report = asyncio.run(run(args.dataset, args.output, split=args.split, top_k=args.top_k))
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
