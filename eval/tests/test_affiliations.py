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


class TestSemicolonRepresentationEquivalence:
    """Gold stores multi-affiliations as one ';'-joined rasses string; parsers
    return a list. _extract_affs splits on ';' symmetrically so the two
    representations score equal — and a parser that itself joins is NOT
    penalised (no list-vs-joined regression)."""

    def test_gold_joined_vs_parsed_list_now_matches(self) -> None:
        gold = _author(["MIT; Harvard"])  # gold rasses semicolon-joined
        parsed = _author(["MIT", "Harvard"])  # parser list-form
        res = score_affiliations(gold, parsed)
        assert res.strict_f1 == 1.0

    def test_parsed_joined_vs_gold_list_no_regression(self) -> None:
        # A parser that semicolon-joins must not be penalised vs list-form gold.
        gold = _author(["MIT", "Harvard"])
        parsed = _author(["MIT; Harvard"])
        res = score_affiliations(gold, parsed)
        assert res.strict_f1 == 1.0

    def test_both_joined_still_perfect(self) -> None:
        res = score_affiliations(_author(["MIT; Harvard"]), _author(["MIT; Harvard"]))
        assert res.strict_f1 == 1.0

    def test_both_list_unchanged(self) -> None:
        res = score_affiliations(_author(["MIT", "Harvard"]), _author(["MIT", "Harvard"]))
        assert res.strict_f1 == 1.0

    def test_single_aff_no_semicolon_unaffected(self) -> None:
        res = score_affiliations(_author(["MIT"]), _author(["MIT"]))
        assert res.strict_f1 == 1.0

    def test_gold_semicolon_email_fragment_not_a_separate_aff(self) -> None:
        # Gold ';'-appends an email to the affiliation; the email fragment must
        # NOT be split off as a spurious second affiliation (regression seen on
        # CUP 10.1017/s0021223719000190: 1.0 -> 0.667 with a naive split).
        gold = _author([
            "Law Faculty of the Hebrew University of Jerusalem; "
            "shani.friedman@mail.huji.ac.il."
        ])
        parsed = _author(["Law Faculty of the Hebrew University of Jerusalem"])
        res = score_affiliations(gold, parsed)
        assert res.fuzzy_f1 == 1.0

    def test_ssrn_no_affiliation_placeholder_counts_empty(self) -> None:
        gold = _author(["affiliation not provided to SSRN"])
        parsed = _author([])
        res = score_affiliations(gold, parsed)
        assert res.fuzzy_f1 == 1.0

    def test_ssrn_independent_no_affiliation_placeholder_counts_empty(self) -> None:
        gold = _author([])
        parsed = _author(["Independent - affiliation not provided to SSRN"])
        res = score_affiliations(gold, parsed)
        assert res.fuzzy_f1 == 1.0
