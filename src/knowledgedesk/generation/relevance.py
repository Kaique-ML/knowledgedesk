from __future__ import annotations

import re
import unicodedata

from knowledgedesk.generation.contracts import GroundedAnswer

_STOP_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "é",
    "is",
    "o",
    "os",
    "qual",
    "que",
    "the",
    "what",
}


def _tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    ascii_value = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return {
        token
        for token in re.findall(r"[a-z0-9]+", ascii_value)
        if len(token) >= 2 and token not in _STOP_WORDS
    }


def keep_most_relevant_blocks(
    answer: GroundedAnswer,
    question: str,
    evidence_text: dict[str, str],
) -> GroundedAnswer:
    """Remove model-added adjacent facts while retaining cited conflict explanations."""
    if answer.status == "conflicting_sources" or len(answer.answer_blocks) < 2:
        return answer
    question_tokens = _tokens(question)
    if not question_tokens:
        return answer
    scored = []
    for block in answer.answer_blocks:
        context = " ".join(evidence_text.get(item, "") for item in block.evidence_ids)
        score = len(question_tokens & _tokens(f"{block.text} {context}"))
        scored.append((block, score))
    best = max(score for _, score in scored)
    if best == 0:
        return answer
    selected = [block for block, score in scored if score == best]
    return answer.model_copy(update={"answer_blocks": selected})
