from __future__ import annotations

from collections.abc import Sequence


def recall_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float | None:
    if not relevant:
        return None
    return len(set(retrieved[:k]) & relevant) / len(relevant)


def reciprocal_rank(retrieved: Sequence[str], relevant: set[str]) -> float | None:
    if not relevant:
        return None
    for rank, identifier in enumerate(retrieved, start=1):
        if identifier in relevant:
            return 1.0 / rank
    return 0.0
