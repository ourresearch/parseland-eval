"""Aggregate per-row scores into scorecard (overall / per-publisher / per-failure-mode)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from statistics import mean
from typing import Any

from parseland_eval.gold import GoldRow
from parseland_eval.runner import ParserRun
from parseland_eval.score.abstract import (
    ABSTRACT_MATCH_THRESHOLD,
    AbstractResult,
    score_abstract,
)
from parseland_eval.score.affiliations import AffiliationResult, score_affiliations
from parseland_eval.score.authors import AuthorResult, score_authors
from parseland_eval.score.pdf_url import PdfUrlResult, score_pdf_url


@dataclass(frozen=True)
class RowScore:
    doi: str
    no: int
    publisher_domain: str
    parser_name: str | None
    duration_ms: float
    error: str | None
    gold_quality: str
    failure_modes: tuple[str, ...]
    has_bot_check: bool | None
    authors: AuthorResult | None
    affiliations: AffiliationResult | None
    abstract: AbstractResult
    pdf_url: PdfUrlResult
    bot_check_flag: bool  # from fetch layer, if available (None → False here)


def _parser_name(run: ParserRun) -> str | None:
    if not run.parsed:
        return None
    # parseland-lib's parse.py doesn't emit parser name in the ordered response.
    # We treat "which parser ran" as unknown for now; future work: expose it.
    return None


def _aff_for_row(gold: GoldRow, run: ParserRun, authors_result: AuthorResult | None) -> AffiliationResult | None:
    if not authors_result or not gold.score_authors:
        return None
    parsed_authors = (run.parsed or {}).get("authors") or []
    per_pair: list[AffiliationResult] = []
    for match in authors_result.matched:
        gold_author = gold.authors[match.gold_index]
        parsed_author = parsed_authors[match.parsed_index] if match.parsed_index < len(parsed_authors) else None
        per_pair.append(score_affiliations(gold_author, parsed_author))
    if not per_pair:
        return None
    # Mean across matched author pairs.
    return AffiliationResult(
        strict_f1=mean(p.strict_f1 for p in per_pair),
        soft_f1=mean(p.soft_f1 for p in per_pair),
        fuzzy_f1=mean(p.fuzzy_f1 for p in per_pair),
        strict_precision=mean(p.strict_precision for p in per_pair),
        strict_recall=mean(p.strict_recall for p in per_pair),
        soft_precision=mean(p.soft_precision for p in per_pair),
        soft_recall=mean(p.soft_recall for p in per_pair),
        fuzzy_precision=mean(p.fuzzy_precision for p in per_pair),
        fuzzy_recall=mean(p.fuzzy_recall for p in per_pair),
        matched=sum(p.matched for p in per_pair),
        gold_total=sum(p.gold_total for p in per_pair),
        parsed_total=sum(p.parsed_total for p in per_pair),
    )


def score_row(gold: GoldRow, run: ParserRun) -> RowScore:
    parsed = run.parsed or {}
    if gold.score_authors:
        authors_result = score_authors(list(gold.authors), parsed.get("authors") or [])
    else:
        authors_result = None
    abs_res = score_abstract(gold.abstract, parsed.get("abstract"))
    pdf_res = score_pdf_url(gold.pdf_url, parsed)
    aff_res = _aff_for_row(gold, run, authors_result)

    return RowScore(
        doi=gold.doi,
        no=gold.no,
        publisher_domain=run.publisher_domain,
        parser_name=_parser_name(run),
        duration_ms=run.duration_ms,
        error=run.error,
        gold_quality=gold.gold_quality,
        failure_modes=gold.failure_modes,
        has_bot_check=gold.has_bot_check,
        authors=authors_result,
        affiliations=aff_res,
        abstract=abs_res,
        pdf_url=pdf_res,
        bot_check_flag=bool(gold.has_bot_check),
    )


def _mean_f1(scores: list[RowScore], accessor) -> float:
    vals = [v for v in (accessor(s) for s in scores) if v is not None]
    return float(mean(vals)) if vals else 0.0


def _pdf_micro_pr(rs: list[RowScore]) -> tuple[float, float]:
    """Micro-aggregated precision/recall over PDF URLs.

    Macro would treat every row equally, including rows where the gold has no
    PDF URL at all. That hides real misses behind "easy" true-negatives. We
    want the fraction of *correctly returned* URLs (P) and the fraction of
    *expected* URLs that were returned correctly (R).
    """
    tp = fp = fn = 0
    for s in rs:
        p = s.pdf_url
        if p.strict_match and (p.present or p.expected_present):
            tp += 1
        elif p.present and not p.strict_match:
            fp += 1
        if p.expected_present and (not p.present or not p.strict_match):
            fn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return precision, recall


def _abstract_match_rate(rs: list[RowScore]) -> float:
    """Fraction of rows whose abstract matches at ABSTRACT_MATCH_THRESHOLD.

    Rows where neither gold nor parsed have an abstract are counted as
    trivially correct (both empty), matching how accuracy is framed elsewhere.
    """
    return _mean_f1(rs, lambda s: 1.0 if s.abstract.match_at_threshold else 0.0)


def summarize(scores: list[RowScore]) -> dict[str, Any]:
    authors_rows = [s for s in scores if s.authors is not None]
    aff_rows = [s for s in scores if s.affiliations is not None]
    pdf_p, pdf_r = _pdf_micro_pr(scores)

    overall = {
        "rows": len(scores),
        "authors_scored_rows": len(authors_rows),
        "authors_f1_strict": _mean_f1(authors_rows, lambda s: s.authors.f1 if s.authors else None),
        "authors_f1_soft": _mean_f1(authors_rows, lambda s: s.authors.f1_soft if s.authors else None),
        "authors_precision_strict": _mean_f1(authors_rows, lambda s: s.authors.precision if s.authors else None),
        "authors_recall_strict": _mean_f1(authors_rows, lambda s: s.authors.recall if s.authors else None),
        "authors_precision_soft": _mean_f1(authors_rows, lambda s: s.authors.precision_soft if s.authors else None),
        "authors_recall_soft": _mean_f1(authors_rows, lambda s: s.authors.recall_soft if s.authors else None),
        "affiliations_f1_strict": _mean_f1(aff_rows, lambda s: s.affiliations.strict_f1 if s.affiliations else None),
        "affiliations_f1_soft": _mean_f1(aff_rows, lambda s: s.affiliations.soft_f1 if s.affiliations else None),
        "affiliations_f1_fuzzy": _mean_f1(aff_rows, lambda s: s.affiliations.fuzzy_f1 if s.affiliations else None),
        "affiliations_precision_strict": _mean_f1(aff_rows, lambda s: s.affiliations.strict_precision if s.affiliations else None),
        "affiliations_recall_strict": _mean_f1(aff_rows, lambda s: s.affiliations.strict_recall if s.affiliations else None),
        "affiliations_precision_soft": _mean_f1(aff_rows, lambda s: s.affiliations.soft_precision if s.affiliations else None),
        "affiliations_recall_soft": _mean_f1(aff_rows, lambda s: s.affiliations.soft_recall if s.affiliations else None),
        "affiliations_precision_fuzzy": _mean_f1(aff_rows, lambda s: s.affiliations.fuzzy_precision if s.affiliations else None),
        "affiliations_recall_fuzzy": _mean_f1(aff_rows, lambda s: s.affiliations.fuzzy_recall if s.affiliations else None),
        "abstract_ratio_soft": _mean_f1(scores, lambda s: s.abstract.soft_ratio),
        "abstract_ratio_fuzzy": _mean_f1(scores, lambda s: s.abstract.fuzzy_ratio),
        "abstract_strict_match_rate": _mean_f1(scores, lambda s: 1.0 if s.abstract.strict_match else 0.0),
        "abstract_present_rate": _mean_f1(scores, lambda s: 1.0 if s.abstract.present else 0.0),
        "abstract_match_rate": _abstract_match_rate(scores),
        "abstract_match_threshold": ABSTRACT_MATCH_THRESHOLD,
        "pdf_url_accuracy": _mean_f1(scores, lambda s: 1.0 if s.pdf_url.strict_match else 0.0),
        "pdf_url_divergence_rate": _mean_f1(scores, lambda s: 1.0 if s.pdf_url.divergent else 0.0),
        "pdf_url_precision": pdf_p,
        "pdf_url_recall": pdf_r,
        "errors": sum(1 for s in scores if s.error),
        "duration_ms_mean": _mean_f1(scores, lambda s: s.duration_ms),
    }

    by_publisher: dict[str, list[RowScore]] = defaultdict(list)
    for s in scores:
        by_publisher[s.publisher_domain or "unknown"].append(s)
    per_publisher = {}
    for domain, rs in sorted(by_publisher.items(), key=lambda kv: -len(kv[1])):
        rs_authors = [r for r in rs if r.authors]
        rs_aff = [r for r in rs if r.affiliations]
        p_pub, r_pub = _pdf_micro_pr(rs)
        per_publisher[domain] = {
            "rows": len(rs),
            "authors_f1_soft": _mean_f1(rs_authors, lambda s: s.authors.f1_soft if s.authors else None),
            "authors_precision_soft": _mean_f1(rs_authors, lambda s: s.authors.precision_soft if s.authors else None),
            "authors_recall_soft": _mean_f1(rs_authors, lambda s: s.authors.recall_soft if s.authors else None),
            "affiliations_f1_fuzzy": _mean_f1(rs_aff, lambda s: s.affiliations.fuzzy_f1 if s.affiliations else None),
            "affiliations_precision_fuzzy": _mean_f1(rs_aff, lambda s: s.affiliations.fuzzy_precision if s.affiliations else None),
            "affiliations_recall_fuzzy": _mean_f1(rs_aff, lambda s: s.affiliations.fuzzy_recall if s.affiliations else None),
            "abstract_ratio_fuzzy": _mean_f1(rs, lambda s: s.abstract.fuzzy_ratio),
            "abstract_match_rate": _abstract_match_rate(rs),
            "pdf_url_accuracy": _mean_f1(rs, lambda s: 1.0 if s.pdf_url.strict_match else 0.0),
            "pdf_url_precision": p_pub,
            "pdf_url_recall": r_pub,
            "errors": sum(1 for r in rs if r.error),
        }

    by_failure: dict[str, list[RowScore]] = defaultdict(list)
    for s in scores:
        tags = s.failure_modes or ("clean",)
        for t in tags:
            by_failure[t].append(s)
    per_failure_mode = {}
    for mode, rs in sorted(by_failure.items(), key=lambda kv: -len(kv[1])):
        rs_authors = [r for r in rs if r.authors]
        p_fm, r_fm = _pdf_micro_pr(rs)
        per_failure_mode[mode] = {
            "rows": len(rs),
            "authors_f1_soft": _mean_f1(rs_authors, lambda s: s.authors.f1_soft if s.authors else None),
            "authors_precision_soft": _mean_f1(rs_authors, lambda s: s.authors.precision_soft if s.authors else None),
            "authors_recall_soft": _mean_f1(rs_authors, lambda s: s.authors.recall_soft if s.authors else None),
            "abstract_ratio_fuzzy": _mean_f1(rs, lambda s: s.abstract.fuzzy_ratio),
            "abstract_match_rate": _abstract_match_rate(rs),
            "pdf_url_accuracy": _mean_f1(rs, lambda s: 1.0 if s.pdf_url.strict_match else 0.0),
            "pdf_url_precision": p_fm,
            "pdf_url_recall": r_fm,
        }

    return {
        "overall": overall,
        "per_publisher": per_publisher,
        "per_failure_mode": per_failure_mode,
    }
