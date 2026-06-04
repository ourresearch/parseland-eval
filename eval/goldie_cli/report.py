"""Holdout-scored run report.

Reuses the LOCKED comparator (``eval/scripts/diff_goldie.py``) with ``relaxed=True`` — the
official scoring contract (substring rases, same-host+DOI pdf). Does NOT reimplement
matching.

FIELD ORDER — ``rases > pdf_url > ca > abstract > authors``:
    This is Casey's priority order from the ``openalex-goldie-extractor`` skill (rases is the
    most important + biggest gap; authors least). It DIFFERS from the earlier approved-plan
    wording (``rases > corresponding > abstract > pdf_url > authors``). The skill is the
    current project source of truth for reporting, so we follow it here and record the
    divergence explicitly (see PLAN note + docs/report-field-order.md).

Reports per field: accuracy on ALL matched rows AND on FETCH-OK rows separately (never
conflated), gap-to-bar (85%), and a 4-bucket failure taxonomy
(empty / punctuation-only / hallucination / dropped-detail).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .config import EVAL_DIR

# Skill's reporting order (see module docstring for why it differs from the plan).
FIELD_ORDER = ["rases", "pdf_url", "ca", "abstract", "authors"]
BAR = 0.85


def _load_diff_goldie():
    scripts = EVAL_DIR / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import diff_goldie  # noqa: E402
    return diff_goldie


_D = _load_diff_goldie()


def _authors(row: dict) -> list[dict]:
    s = (row.get("Authors") or "").strip()
    if not s or s.lower() in {"n/a", "na", "[]", "none", "null"}:
        return []
    try:
        a = json.loads(s)
        return a if isinstance(a, list) else []
    except json.JSONDecodeError:
        return []


def _empty(s: str | None) -> bool:
    return not s or s.strip().lower() in {"n/a", "na", "none", "null", "[]", ""}


def _fetch_ok(produced: dict) -> bool:
    """A row 'fetched' if extraction produced anything (Status TRUE or any field present)."""
    if (produced.get("Status") or "").upper() == "TRUE":
        return True
    return not (_empty(produced.get("Authors")) and _empty(produced.get("Abstract"))
                and _empty(produced.get("PDF URL")))


def score_row(gold: dict, ai: dict) -> dict[str, bool]:
    """Per-field match using the locked relaxed comparator."""
    doi = (gold.get("DOI") or ai.get("DOI") or "").strip()
    h_auth, a_auth = _authors(gold), _authors(ai)
    return {
        "authors": _D.authors_match(h_auth, a_auth, relaxed=True),
        "rases": _D.rases_match(h_auth, a_auth, relaxed=True),
        "ca": _D.corresponding_match(h_auth, a_auth, relaxed=True),
        "abstract": _D.abstract_match(gold.get("Abstract"), ai.get("Abstract"), relaxed=True),
        "pdf_url": _D._pdf_url_match_relaxed(
            _D.normalize_absent(gold.get("PDF URL")), _D.normalize_absent(ai.get("PDF URL")), doi),
    }


def _bucket(field: str, gold: dict, ai: dict) -> str:
    """Classify a per-field miss into one of the four buckets."""
    if field in ("authors", "rases", "ca"):
        g_empty, a_empty = not _authors(gold), not _authors(ai)
    else:
        col = "Abstract" if field == "abstract" else "PDF URL"
        g_empty, a_empty = _empty(gold.get(col)), _empty(ai.get(col))
    if g_empty and not a_empty:
        return "hallucination"
    if a_empty and not g_empty:
        return "empty"
    if not g_empty and not a_empty:
        if field in ("abstract", "pdf_url"):
            col = "Abstract" if field == "abstract" else "PDF URL"
            gn = " ".join((gold.get(col) or "").lower().split())
            an = " ".join((ai.get(col) or "").lower().split())
            if gn == an:
                return "punctuation-only"
        return "dropped-detail"
    return "dropped-detail"


def compute_report(gold_rows: list[dict], produced_rows: list[dict]) -> dict[str, Any]:
    """Score produced rows against gold (matched by DOI). Returns the report dict."""
    prod_by = {(r.get("DOI") or "").strip(): r for r in produced_rows}
    matched = [(g, prod_by[(g.get("DOI") or "").strip()])
               for g in gold_rows if (g.get("DOI") or "").strip() in prod_by]

    fields: dict[str, Any] = {}
    for field in FIELD_ORDER:
        all_n = len(matched)
        all_hits = 0
        fo_n = fo_hits = 0
        buckets: dict[str, int] = {}
        for gold, ai in matched:
            ok = score_row(gold, ai)[field]
            all_hits += int(ok)
            if not ok:
                b = _bucket(field, gold, ai)
                buckets[b] = buckets.get(b, 0) + 1
            if _fetch_ok(ai):
                fo_n += 1
                fo_hits += int(ok)
        acc_all = round(all_hits / all_n, 4) if all_n else 0.0
        acc_fo = round(fo_hits / fo_n, 4) if fo_n else 0.0
        gap = round(acc_all - BAR, 4)
        status = "above" if acc_all >= BAR else ("close" if acc_all >= BAR - 0.10 else "far")
        fields[field] = {
            "accuracy_all": acc_all,
            "accuracy_fetch_ok": acc_fo,
            "n_all": all_n,
            "n_fetch_ok": fo_n,
            "gap_to_bar": gap,
            "status": status,
            "failure_buckets": buckets,
        }

    return {
        "bar": BAR,
        "field_order": FIELD_ORDER,
        "field_order_note": "Casey's skill order (rases>pdf_url>ca>abstract>authors); "
                            "differs from approved-plan wording — see report.py docstring.",
        "matched_rows": len(matched),
        "gold_rows": len(gold_rows),
        "produced_rows": len(produced_rows),
        "fetch_ok_rows": sum(1 for _, ai in matched if _fetch_ok(ai)),
        "fields": fields,
    }


def summary_report(produced_rows: list[dict], manifest: dict[str, Any]) -> dict[str, Any]:
    """Unscored run summary (written when no --holdout is supplied, e.g. a fresh corpus)."""
    n = len(produced_rows)
    fo = sum(1 for r in produced_rows if _fetch_ok(r))
    return {
        "type": "summary",
        "rows": n,
        "fetch_ok_rows": fo,
        "fetch_ok_rate": round(fo / n, 4) if n else 0.0,
        "cost_usd": manifest.get("cost_usd"),
        "status": manifest.get("status"),
        "fallback": manifest.get("fallback"),
        "note": "unscored summary — pass --holdout <gold.csv> to score against gold",
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
