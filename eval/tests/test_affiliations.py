"""Precision/recall tests for the affiliation scorer.

Author match isn't exercised here — ``score_affiliations`` takes one gold
and one parsed author object directly and scores their affiliation lists.
"""
from __future__ import annotations

from parseland_eval.score.affiliations import score_affiliations


def _author(affs: list[str]) -> dict:
    return {"name": "X", "affiliations": [{"name": a} for a in affs]}


class TestAffiliationPrecisionRecall:
    def test_exact_match_all_perfect(self) -> None:
        res = score_affiliations(_author(["MIT"]), _author(["MIT"]))
        assert res.strict_f1 == 1.0
        assert res.strict_precision == 1.0
        assert res.strict_recall == 1.0
        assert res.fuzzy_precision == 1.0
        assert res.fuzzy_recall == 1.0

    def test_over_generation_drops_precision_not_recall(self) -> None:
        # Gold has one, parsed returned two — the extra is a hallucination.
        gold = _author(["MIT"])
        parsed = _author(["MIT", "Totally Unrelated Institute of Nowhere"])
        res = score_affiliations(gold, parsed)
        assert res.fuzzy_recall == 1.0
        assert res.fuzzy_precision < 1.0

    def test_under_generation_drops_recall_not_precision(self) -> None:
        gold = _author(["MIT", "Harvard", "Stanford"])
        parsed = _author(["MIT"])
        res = score_affiliations(gold, parsed)
        assert res.fuzzy_precision == 1.0
        assert res.fuzzy_recall < 1.0

    def test_empty_both_is_perfect(self) -> None:
        res = score_affiliations(_author([]), _author([]))
        assert res.strict_precision == 1.0
        assert res.strict_recall == 1.0
        assert res.fuzzy_precision == 1.0
        assert res.fuzzy_recall == 1.0

    def test_empty_gold_nonempty_parsed_is_zero(self) -> None:
        res = score_affiliations(_author([]), _author(["MIT"]))
        assert res.fuzzy_precision == 0.0
        assert res.fuzzy_recall == 0.0

    def test_fuzzy_relaxation_improves_recall_over_strict(self) -> None:
        gold = _author(["Department of Biology, University of Aveiro"])
        parsed = _author(["Univ. Aveiro, Biology"])
        res = score_affiliations(gold, parsed)
        assert res.fuzzy_recall >= res.strict_recall
        assert res.fuzzy_precision >= res.strict_precision
