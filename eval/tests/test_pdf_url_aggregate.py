"""Micro-aggregated precision/recall for PDF URLs across rows.

We feed pre-built ``PdfUrlResult`` values into ``_pdf_micro_pr`` to exercise
the aggregation math independently of the per-row scorer.
"""
from __future__ import annotations

from parseland_eval.score.abstract import AbstractResult
from parseland_eval.score.aggregate import RowScore, _pdf_micro_pr
from parseland_eval.score.pdf_url import PdfUrlResult


def _row(pdf: PdfUrlResult) -> RowScore:
    return RowScore(
        doi="10.1/fake",
        no=0,
        publisher_domain="x.com",
        parser_name=None,
        duration_ms=0.0,
        error=None,
        gold_quality="ok",
        failure_modes=(),
        has_bot_check=False,
        authors=None,
        affiliations=None,
        abstract=AbstractResult(False, 0.0, 0.0, 0.0, False, False),
        pdf_url=pdf,
        bot_check_flag=False,
    )


class TestPdfMicroPrecisionRecall:
    def test_all_correct(self) -> None:
        rows = [
            _row(PdfUrlResult(strict_match=True, present=True, expected_present=True, divergent=False))
            for _ in range(5)
        ]
        p, r = _pdf_micro_pr(rows)
        assert p == 1.0
        assert r == 1.0

    def test_mixed_presence_micro_excludes_true_negatives(self) -> None:
        # 2 correct matches, 1 row where gold has no PDF and parser returned none
        # (trivially correct but not in numerator — micro just ignores it).
        rows = [
            _row(PdfUrlResult(True, True, True, False)),
            _row(PdfUrlResult(True, True, True, False)),
            _row(PdfUrlResult(True, False, False, False)),
        ]
        p, r = _pdf_micro_pr(rows)
        assert p == 1.0
        assert r == 1.0

    def test_false_positive_drops_precision(self) -> None:
        # Parser returned a PDF when gold expected none → FP
        rows = [
            _row(PdfUrlResult(True, True, True, False)),
            _row(PdfUrlResult(False, True, False, False)),  # FP
        ]
        p, r = _pdf_micro_pr(rows)
        assert p == 0.5
        assert r == 1.0

    def test_false_negative_drops_recall(self) -> None:
        # Parser missed a PDF that gold expected → FN
        rows = [
            _row(PdfUrlResult(True, True, True, False)),
            _row(PdfUrlResult(False, False, True, False)),  # FN
        ]
        p, r = _pdf_micro_pr(rows)
        assert p == 1.0
        assert r == 0.5

    def test_divergent_is_fp_and_fn(self) -> None:
        # Both sides returned a URL, but they differ → it's wrong both ways
        rows = [
            _row(PdfUrlResult(False, True, True, True)),
        ]
        p, r = _pdf_micro_pr(rows)
        assert p == 0.0
        assert r == 0.0

    def test_zero_division_safe_on_empty_inputs(self) -> None:
        p, r = _pdf_micro_pr([])
        assert p == 0.0
        assert r == 0.0
