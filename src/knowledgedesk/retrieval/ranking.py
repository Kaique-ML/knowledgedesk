from collections.abc import Hashable, Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RankedItem:
    identifier: Hashable
    payload: object


def reciprocal_rank_fusion(
    rankings: Sequence[Iterable[RankedItem]], *, constant: int = 60, limit: int = 10
) -> list[tuple[Hashable, float, object]]:
    """Fuse ranked lists without comparing scores from different retrieval systems."""
    if constant < 1 or limit < 0:
        raise ValueError("constant must be positive and limit cannot be negative")

    scores: dict[Hashable, float] = {}
    payloads: dict[Hashable, object] = {}
    for ranking in rankings:
        seen: set[Hashable] = set()
        for rank, item in enumerate(ranking, start=1):
            if item.identifier in seen:
                continue
            seen.add(item.identifier)
            scores[item.identifier] = scores.get(item.identifier, 0.0) + 1.0 / (constant + rank)
            payloads.setdefault(item.identifier, item.payload)

    ordered = sorted(scores, key=lambda identifier: (-scores[identifier], str(identifier)))
    return [
        (identifier, scores[identifier], payloads[identifier]) for identifier in ordered[:limit]
    ]
