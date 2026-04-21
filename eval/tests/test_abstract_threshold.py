"""Tests for the binary abstract-match flag, summary aggregation, and
the threshold-tuning helpers under ``scripts/``."""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

from parseland_eval.score import abstract as abstract_mod
from parseland_eval.score.abstract import ABSTRACT_MATCH_THRESHOLD, score_abstract

# Load the tuning script as a module so we can unit-test its helpers.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))
import tune_abstract_threshold as tune  # type: ignore[import-not-found]  # noqa: E402


class TestMatchAtThreshold:
    def test_both_empty_is_match(self) -> None:
        res = score_abstract(None, None)
        assert res.match_at_threshold is True

    def test_asymmetric_empty_is_miss(self) -> None:
        res = score_abstract("abstract text", None)
        assert res.match_at_threshold is False

        res = score_abstract(None, "spurious")
        assert res.match_at_threshold is False

    def test_exact_match_is_above_threshold(self) -> None:
        text = "Quick brown fox jumps over the lazy dog" * 5
        res = score_abstract(text, text)
        assert res.fuzzy_ratio == 1.0
        assert res.match_at_threshold is True

    def test_drastically_different_misses(self) -> None:
        gold = "The mitochondrial DNA analysis revealed ancient lineages." * 3
        parsed = "Cookies and caramel are delightful desserts for everyone." * 3
        res = score_abstract(gold, parsed)
        assert res.match_at_threshold is False

    def test_threshold_boundary_respected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Pin threshold so the boundary is unambiguous regardless of future tuning.
        monkeypatch.setattr(abstract_mod, "ABSTRACT_MATCH_THRESHOLD", 0.90)
        gold = "The mitochondrial DNA analysis revealed ancient lineages of corvids."
        parsed = "The mitochondrial DNA analysis reveals ancient lineages of corvids."  # tiny edit
        res = score_abstract(gold, parsed)
        assert res.fuzzy_ratio >= 0.90
        assert res.match_at_threshold is True

        # Parser replaces half the words → should fall under threshold.
        parsed_worse = "Cookies analysis reveals ancient cakes of corvids 123 xyz"
        res2 = score_abstract(gold, parsed_worse)
        assert res2.fuzzy_ratio < 0.90
        assert res2.match_at_threshold is False


class TestTuningHelpers:
    def test_otsu_splits_bimodal_between_modes(self) -> None:
        rng = random.Random(0)
        low = [max(0.0, min(1.0, rng.gauss(0.2, 0.04))) for _ in range(80)]
        high = [max(0.0, min(1.0, rng.gauss(0.9, 0.04))) for _ in range(80)]
        t = tune._otsu(low + high)
        assert 0.3 < t < 0.85, f"Otsu should land between modes, got {t}"

    def test_largest_gap_respects_floor(self) -> None:
        # With a floor of 0.5, gaps below the floor are ignored; the only
        # remaining gap is (0.55, 0.90).
        values = [0.01, 0.02, 0.55, 0.90, 0.92]
        mid, lo, hi = tune._largest_gap(values, floor=0.5)
        assert (lo, hi) == (0.55, 0.90)
        assert mid == pytest.approx(0.725)

    def test_largest_gap_fallback_when_nothing_above_floor(self) -> None:
        values = [0.0, 0.1, 0.2]
        mid, lo, hi = tune._largest_gap(values, floor=0.5)
        # Degenerate input: fall back to the floor itself.
        assert mid == 0.5
        assert lo == 0.5
        assert hi == 1.0


class TestSummaryKeys:
    def test_match_rate_in_summary(self) -> None:
        # Import here to avoid shadowing the monkeypatch in other tests.
        from parseland_eval.gold import GoldAuthor, GoldRow
        from parseland_eval.runner import ParserRun
        from parseland_eval.score.aggregate import score_row, summarize

        gold = GoldRow(
            no=1,
            doi="10.1/t",
            link="https://doi.org/10.1/t",
            authors=(GoldAuthor("Jane Doe", ("MIT",), True),),
            abstract="Exact abstract.",
            pdf_url="https://example.com/p.pdf",
            status=True,
            notes="",
            has_bot_check=False,
            resolves_to_pdf=False,
        )
        run = ParserRun(
            doi="10.1/t",
            parsed={
                "authors": [{"name": "Jane Doe", "affiliations": [{"name": "MIT"}]}],
                "abstract": "Exact abstract.",
                "urls": [{"url": "https://example.com/p.pdf", "content_type": "pdf"}],
            },
            error=None,
            duration_ms=1.0,
            publisher_domain="example.com",
        )
        summary = summarize([score_row(gold, run)])
        overall = summary["overall"]
        assert "abstract_match_rate" in overall
        assert "abstract_match_threshold" in overall
        assert overall["abstract_match_threshold"] == ABSTRACT_MATCH_THRESHOLD
        assert overall["abstract_match_rate"] == 1.0
