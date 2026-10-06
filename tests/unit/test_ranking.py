import pytest

from knowledgedesk.retrieval.ranking import RankedItem, reciprocal_rank_fusion


def test_rrf_combines_rankings_and_removes_duplicate_ids() -> None:
    lexical = [RankedItem("a", "A"), RankedItem("a", "duplicate"), RankedItem("b", "B")]
    vector = [RankedItem("b", "B"), RankedItem("a", "A")]

    fused = reciprocal_rank_fusion([lexical, vector], limit=2)

    assert [item[0] for item in fused] == ["a", "b"]
    assert fused[0][1] == pytest.approx(1 / 61 + 1 / 62)
    assert fused[0][2] == "A"


@pytest.mark.parametrize(("constant", "limit"), [(0, 2), (60, -1)])
def test_rrf_rejects_invalid_parameters(constant: int, limit: int) -> None:
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([], constant=constant, limit=limit)


def test_rrf_ties_use_stable_identifier_order() -> None:
    fused = reciprocal_rank_fusion(
        [
            [RankedItem("z", None), RankedItem("a", None)],
            [RankedItem("a", None), RankedItem("z", None)],
        ],
        constant=60,
    )

    assert [item[0] for item in fused] == ["a", "z"]
