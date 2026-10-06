from knowledgedesk.evaluation.metrics import recall_at_k, reciprocal_rank


def test_retrieval_metrics_use_explicit_relevance_denominator() -> None:
    retrieved = ["b", "a", "c"]
    relevant = {"a", "d"}

    assert recall_at_k(retrieved, relevant, 2) == 0.5
    assert reciprocal_rank(retrieved, relevant) == 0.5


def test_no_relevant_documents_has_no_fictitious_recall() -> None:
    assert recall_at_k(["a"], set(), 1) is None
    assert reciprocal_rank(["a"], set()) is None
