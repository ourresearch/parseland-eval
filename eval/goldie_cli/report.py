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
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from .config import EVAL_DIR
from .transforms.conventions import convention_labels

# Skill's reporting order (see module docstring for why it differs from the plan).
FIELD_ORDER = ["rases", "pdf_url", "ca", "abstract", "authors"]
BAR = 0.85

_CA_BOOK_CHAPTER_RE = re.compile(r"(^978[-0-9]|^oso/978|/978[-0-9]|978-\d)", re.I)
_CA_SUPPLEMENT_RE = re.compile(r"(supplement|_supplement|_supp\b|fasebj\.[^/]+\.a\d+)", re.I)
_CA_PROCEEDINGS_PREFIXES = ("10.1109", "10.2118", "10.1063")
_CA_PREPRINT_PREFIXES = ("10.31234",)
_CA_REPORT_PREFIXES = ("10.17226",)


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


def _field_presence(row: dict) -> dict[str, bool]:
    authors = _authors(row)
    ca = any(bool(a.get("corresponding_author")) for a in authors)
    rases = any(bool((a.get("rasses") or "").strip()) for a in authors)
    return {
        "authors": bool(authors),
        "ca": ca,
        "rases": rases,
        "abstract": not _empty(row.get("Abstract")),
        "pdf_url": not _empty(row.get("PDF URL")),
    }


def _flag(row: dict, field: str) -> bool:
    return (row.get(field) or "").strip().upper() == "TRUE"


def _terminal_flags(row: dict) -> dict[str, bool]:
    return {
        "has_bot_check": _flag(row, "Has Bot Check"),
        "resolves_to_pdf": _flag(row, "Resolves To PDF"),
        "broken_doi": _flag(row, "broken_doi"),
        "no_english": _flag(row, "no english"),
    }


def _status_counts(rows: list[dict]) -> dict[str, int]:
    true = false = other = 0
    for row in rows:
        status = (row.get("Status") or "").strip().upper()
        if status == "TRUE":
            true += 1
        elif status == "FALSE":
            false += 1
        else:
            other += 1
    return {"true": true, "false": false, "other": other}


def _terminal_note_label(row: dict) -> str | None:
    flags = _terminal_flags(row)
    if flags["has_bot_check"]:
        return "iter-R:bot-check:flagged"
    if flags["resolves_to_pdf"]:
        return "iter-R:pdf-redirect"
    if flags["broken_doi"]:
        return "iter-R:terminal:broken_doi"
    if flags["no_english"]:
        return "iter-R:terminal:no_english"
    return None


def _note_labels(row: dict) -> list[str]:
    labels = [
        p.strip() for p in (row.get("Notes") or "").split("|")
        if p.strip() and (p.strip().startswith("iter-R:") or p.strip().startswith("taxicab-reharvest:"))
    ]
    terminal = _terminal_note_label(row)
    if terminal:
        # Older artifacts may have been classified before terminal flags were
        # propagated. Do not let stale extraction-miss notes pollute quality queues.
        labels = [label for label in labels if label != "iter-R:extraction-miss"]
        if terminal not in labels:
            labels.append(terminal)
    elif any(label.startswith("iter-R:") and label != "iter-R:extraction-miss" for label in labels):
        labels = [label for label in labels if label != "iter-R:extraction-miss"]
    return labels


def _normalized_notes(row: dict) -> str:
    """Report-facing note string after terminal-label normalization.

    Older artifacts can contain stale ``iter-R:extraction-miss`` notes on rows that now
    have explicit terminal flags. Keep the raw row immutable, but make report queues
    internally consistent.
    """
    labels = _note_labels(row)
    if not labels:
        return (row.get("Notes") or "").strip()
    return " | ".join(labels)


def _fetch_ok(produced: dict) -> bool:
    """A row is fetch-ok only if page evidence produced usable content or a terminal flag."""
    if any(_field_presence(produced).values()):
        return True
    if "iter-R:terminal:no_article_metadata" in _note_labels(produced):
        return True
    return any(
        (produced.get(k) or "").upper() == "TRUE"
        for k in ("Resolves To PDF", "broken_doi", "no english")
    )


def _ca_triage(row: dict) -> dict[str, str]:
    """Explain CA-only gaps without changing gold values.

    This is deliberately conservative and evidence-routing-only: a DOI can be labeled as a
    content-type/convention candidate, but the report still counts the CA field as missing.
    """
    notes = (row.get("Notes") or "").strip()
    if "page-probe:ca-no-explicit-marker" in notes:
        return {
            "reason": "page_probe_no_explicit_ca_marker",
            "note": "Taxicab/publisher page HTML was checked and no explicit corresponding-author marker was found.",
        }
    if "page-probe:ca-marker-candidate" in notes:
        return {
            "reason": "page_probe_ca_marker_candidate",
            "note": "Taxicab/publisher page HTML contains corresponding-author language; needs targeted evidence review.",
        }
    if "live-probe:ca-no-explicit-marker" in notes:
        return {
            "reason": "live_probe_no_explicit_ca_marker",
            "note": "Live DOI/publisher page was checked and no explicit corresponding-author marker was found.",
        }
    if "live-probe:ca-marker-candidate" in notes:
        return {
            "reason": "live_probe_ca_marker_candidate",
            "note": "Live DOI/publisher page contains corresponding-author language; needs targeted evidence review.",
        }
    if "live-probe:ca-bot-check=" in notes:
        return {
            "reason": "live_probe_blocked_by_bot_check",
            "note": "Live DOI/publisher page was blocked by a browser challenge before CA evidence could be verified.",
        }
    if "live-probe:ca-router-only=" in notes:
        return {
            "reason": "live_probe_router_only",
            "note": "Live DOI resolution reached a router-only page before article-level CA evidence.",
        }
    if "live-probe:ca-error=" in notes:
        return {
            "reason": "live_probe_error",
            "note": "Live CA probe errored; rerun with browser evidence if this row matters.",
        }
    doi = (row.get("DOI") or "").strip().lower()
    suffix = doi.split("/", 1)[1] if "/" in doi else doi
    conv = convention_labels(doi)
    if conv.corresp_gold_thin:
        return {"reason": "known_ca_thin_prefix", "note": conv.corresp_gold_thin}
    if _CA_BOOK_CHAPTER_RE.search(suffix):
        return {
            "reason": "book_chapter_or_ebook",
            "note": "DOI suffix looks like an ISBN/eBook chapter; CA is often absent from landing-page metadata.",
        }
    if _CA_SUPPLEMENT_RE.search(doi):
        return {
            "reason": "supplement_or_meeting_abstract",
            "note": "Supplement/meeting abstract landing pages often list authors but no explicit corresponding author.",
        }
    if doi.startswith(_CA_PROCEEDINGS_PREFIXES):
        return {
            "reason": "conference_or_proceedings",
            "note": "Conference/proceedings pages often omit explicit corresponding-author markers.",
        }
    if doi.startswith(_CA_PREPRINT_PREFIXES):
        return {
            "reason": "preprint_or_repository_record",
            "note": "Repository/preprint landing pages may expose authors but no corresponding-author marker.",
        }
    if doi.startswith(_CA_REPORT_PREFIXES):
        return {
            "reason": "report_or_book_record",
            "note": "Report/book records often have organizational author pages without a corresponding-author field.",
        }
    return {
        "reason": "needs_explicit_ca_evidence_audit",
        "note": "Authors/rases/abstract/pdf are present, but no explicit page-evidence CA marker was found.",
    }


def _load_event_meta(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Read optional live event metadata for DOI-level quality triage.

    The report should stay useful even if the event log is missing or stale, so this is
    deliberately best-effort and small: include stable live-session links and booleans for
    ephemeral screenshot URLs, but do not copy huge signed screenshot URLs into report.json.
    """
    events = manifest.get("events")
    if not events:
        return {}
    path = Path(events)
    if not path.exists():
        return {}

    meta: dict[str, dict[str, Any]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    for line in lines:
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        doi = (ev.get("doi") or "").strip()
        if not doi:
            continue
        dst = meta.setdefault(doi, {})
        typ = ev.get("type")
        if typ == "backend_result":
            if ev.get("tier") == "cloud":
                if ev.get("live_url"):
                    dst["browser_live_url"] = ev["live_url"]
                if ev.get("screenshot_url"):
                    dst["browser_screenshot_present"] = True
                if ev.get("step_count") is not None:
                    dst["browser_step_count"] = ev["step_count"]
                if ev.get("is_task_successful") is not None:
                    dst["browser_is_task_successful"] = ev["is_task_successful"]
                if ev.get("last_step_summary"):
                    dst["browser_last_step"] = ev["last_step_summary"]
            if ev.get("reharvest"):
                rh = ev["reharvest"]
                dst["taxicab_reharvest"] = {
                    k: rh.get(k)
                    for k in ("status", "reason", "duration_s", "http_status_post", "http_status_get", "error")
                    if rh.get(k) is not None
                }
        elif typ == "fallback_result":
            dst["fallback_returned"] = bool(ev.get("returned", ev.get("landed")))
    return meta


def _row_entry(row: dict, missing_fields: list[str], event_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "no": row.get("No") or "",
        "doi": (row.get("DOI") or "").strip(),
        "link": row.get("Link") or "",
        "status": row.get("Status") or "",
        "fetch_ok": _fetch_ok(row),
        "missing_fields": missing_fields,
        "labels": _note_labels(row),
    }
    notes = (row.get("Notes") or "").strip()
    if notes:
        entry["notes"] = _normalized_notes(row)
        if entry["notes"] != notes:
            entry["raw_notes"] = notes
    flags = {k: v for k, v in _terminal_flags(row).items() if v}
    if flags:
        entry["terminal_flags"] = flags
    if event_meta:
        entry["event"] = event_meta
    return entry


def _quality_focus(produced_rows: list[dict], manifest: dict[str, Any], *, limit: int = 20) -> dict[str, Any]:
    """Bounded DOI queues for the next quality pass.

    Fresh corpora do not have scored misses yet, so the useful signal is field absence
    plus run evidence: fully empty rows, CA-only convention candidates, extraction-miss
    labels, and broad missing-field rows.
    """
    event_meta = _load_event_meta(manifest)
    buckets: dict[str, list[dict[str, Any]]] = {
        "all_core_empty": [],
        "terminal_flagged_empty": [],
        "bot_check_empty": [],
        "unresolved_all_core_empty": [],
        "ca_only_missing": [],
        "ca_convention_candidates": [],
        "ca_needs_evidence_audit": [],
        "ca_live_no_marker": [],
        "ca_live_marker_candidates": [],
        "ca_live_blocked": [],
        "ca_page_no_marker": [],
        "ca_page_marker_candidates": [],
        "extraction_miss": [],
        "page_checked_residual_missing": [],
        "multi_priority_missing": [],
    }
    counts = {k: 0 for k in buckets}
    ca_triage_counts: Counter[str] = Counter()

    for row in produced_rows:
        present = _field_presence(row)
        missing_fields = [field for field in FIELD_ORDER if not present[field]]
        if not missing_fields:
            continue
        labels = _note_labels(row)
        entry = _row_entry(row, missing_fields, event_meta.get((row.get("DOI") or "").strip()))
        targets: list[str] = []
        core_empty = not present["authors"] and not present["abstract"] and not present["pdf_url"]
        if core_empty:
            terminal = _terminal_flags(row)
            terminal_label = any(
                label.startswith("iter-R:terminal:") or label == "iter-R:pdf-redirect"
                for label in labels
            )
            terminal_no_metadata = "iter-R:terminal:no_article_metadata" in labels
            structural_label = any(
                label.startswith("iter-R:") and label != "iter-R:extraction-miss"
                for label in labels
            )
            targets.append("all_core_empty")
            if terminal["has_bot_check"] and not terminal_no_metadata:
                targets.append("bot_check_empty")
            if terminal["resolves_to_pdf"] or terminal["broken_doi"] or terminal["no_english"] or terminal_label:
                targets.append("terminal_flagged_empty")
            if not any(terminal.values()) and not structural_label:
                targets.append("unresolved_all_core_empty")
        if missing_fields == ["ca"]:
            ca_triage = _ca_triage(row)
            entry["ca_triage"] = ca_triage
            ca_triage_counts[ca_triage["reason"]] += 1
            targets.append("ca_only_missing")
            reason = ca_triage["reason"]
            if reason == "needs_explicit_ca_evidence_audit":
                targets.append("ca_needs_evidence_audit")
            elif reason == "live_probe_no_explicit_ca_marker":
                targets.append("ca_live_no_marker")
            elif reason == "live_probe_ca_marker_candidate":
                targets.append("ca_live_marker_candidates")
                targets.append("ca_needs_evidence_audit")
            elif reason == "page_probe_no_explicit_ca_marker":
                targets.append("ca_page_no_marker")
            elif reason == "page_probe_ca_marker_candidate":
                targets.append("ca_page_marker_candidates")
                targets.append("ca_needs_evidence_audit")
            elif reason in {"live_probe_blocked_by_bot_check", "live_probe_router_only", "live_probe_error"}:
                targets.append("ca_live_blocked")
                targets.append("ca_needs_evidence_audit")
            else:
                targets.append("ca_convention_candidates")
        if "iter-R:extraction-miss" in labels:
            targets.append("extraction_miss")
        if "iter-R:page-checked-residual-missing-fields" in labels:
            targets.append("page_checked_residual_missing")
        if len(missing_fields) >= 3:
            targets.append("multi_priority_missing")
        for target in targets:
            counts[target] += 1
            if len(buckets[target]) < limit:
                buckets[target].append(entry)

    return {
        "limit": limit,
        "counts": counts,
        "ca_triage_counts": dict(ca_triage_counts),
        **buckets,
        "note": "Bounded DOI queues for manual/live-evidence quality follow-up; counts are full-run counts.",
    }


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
    fallback = manifest.get("fallback")
    if isinstance(fallback, dict):
        fallback = dict(fallback)
        if "fallback_used" in fallback and "fallback_returned" not in fallback:
            fallback["legacy_fallback_used_semantics"] = (
                "This run predates fallback_returned; fallback_used counted returned "
                "fallback rows and may overstate rows actually improved."
            )
    presence = Counter()
    missing = Counter()
    missing_combos = Counter()
    labels = Counter()
    for row in produced_rows:
        present = _field_presence(row)
        missing_fields = [field for field in FIELD_ORDER if not present[field]]
        for field, ok in present.items():
            presence[field] += int(ok)
            missing[field] += int(not ok)
        if missing_fields:
            missing_combos[",".join(missing_fields)] += 1
        labels.update(_note_labels(row))
    return {
        "type": "summary",
        "rows": n,
        "fetch_ok_rows": fo,
        "fetch_ok_rate": round(fo / n, 4) if n else 0.0,
        "field_presence": {k: {"present": int(presence[k]), "rows": n} for k in FIELD_ORDER},
        "field_missing": {k: {"missing": int(missing[k]), "rows": n} for k in FIELD_ORDER},
        "missing_field_combinations": missing_combos.most_common(20),
        "extraction_miss": labels.get("iter-R:extraction-miss", 0),
        "top_labels": labels.most_common(20),
        "quality_focus": _quality_focus(produced_rows, manifest),
        "taxicab_reharvest": manifest.get("taxicab_reharvest") or {},
        "cost_usd": manifest.get("cost_usd"),  # tier-1 cost
        "total_cost_usd": manifest.get("total_cost_usd", manifest.get("cost_usd")),  # tier-1 + fallback
        "status": manifest.get("status"),
        "final_status": manifest.get("final_status") or _status_counts(produced_rows),
        "tier1_landed": manifest.get("tier1_landed"),
        "tier1_failed": manifest.get("tier1_failed"),
        "fallback": fallback,
        "targeted_rerun": manifest.get("targeted_rerun"),
        "quality_probes": manifest.get("quality_probes"),
        "events": manifest.get("events"),
        "live_html": manifest.get("live_html"),
        "note": "unscored summary — pass --holdout <gold.csv> to score against gold",
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _pct(numerator: int | float | None, denominator: int | float | None) -> str:
    if not denominator:
        return "n/a"
    return f"{(float(numerator or 0) / float(denominator)):.1%}"


def _money(value: int | float | None) -> str:
    if value is None:
        return "n/a"
    return f"${float(value):.2f}"


def _operator_status(report: dict[str, Any], manifest: dict[str, Any]) -> tuple[str, str]:
    status = str(manifest.get("status") or report.get("status") or "unknown")
    fallback = manifest.get("fallback") if isinstance(manifest.get("fallback"), dict) else {}
    qcounts = ((report.get("quality_focus") or {}).get("counts") or {})
    if status not in {"complete", "success"}:
        return "blocked", f"manifest status is `{status}`"
    if fallback.get("status") in {"fallback_error", "fallback_interrupted"}:
        return "blocked", f"fallback status is `{fallback.get('status')}`"
    if qcounts.get("unresolved_all_core_empty", 0):
        return "blocked", "unresolved all-core-empty rows remain"
    if (manifest.get("failed") or 0) or report.get("extraction_miss", 0) or qcounts.get("ca_needs_evidence_audit", 0):
        return "needs review", "quality queues remain before audited accuracy claims"
    return "ready", "no unresolved infrastructure blocker in the summary report"


def _doi_sample(rows: list[dict[str, Any]], *, limit: int = 10) -> str:
    dois = [r.get("doi") for r in rows[:limit] if r.get("doi")]
    return ", ".join(dois) if dois else "-"


def _command_rows(run_dir: Path, manifest: dict[str, Any]) -> list[str]:
    source = manifest.get("source_csv") or "<source.csv>"
    corpus = manifest.get("corpus") or "<corpus>"
    tier = manifest.get("tier") or (manifest.get("tiers") or ["cached"])[0]
    fallback = manifest.get("fallback_tier") or (manifest.get("fallback") or {}).get("tier") or "cloud"
    return [
        f"`uv run --project eval goldie monitor --run {run_dir}`",
        f"`uv run --project eval goldie resume --run {run_dir}`",
        f"`uv run --project eval goldie report --run {run_dir} --operator --out {run_dir / 'OPERATOR_REPORT.md'}`",
        f"`uv run --project eval goldie run --source {source} --corpus {corpus} --tier {tier} --fallback-tier {fallback} --resume {run_dir}`",
    ]


def operator_report_markdown(run_dir: Path, report: dict[str, Any], manifest: dict[str, Any]) -> str:
    """Render a GitHub/oxjob-friendly operator report from report.json + manifest.json."""
    run_dir = Path(run_dir)
    state, reason = _operator_status(report, manifest)
    rows = int(report.get("rows") or report.get("produced_rows") or manifest.get("rows") or 0)
    fetch_ok = int(report.get("fetch_ok_rows") or 0)
    failed = int(manifest.get("failed") or 0)
    qf = report.get("quality_focus") or {}
    qcounts = qf.get("counts") or {}
    fallback = report.get("fallback") or manifest.get("fallback") or {}
    total_cost = report.get("total_cost_usd", manifest.get("total_cost_usd"))
    cost_per_100 = (float(total_cost) / rows * 100.0) if rows and total_cost is not None else None
    cost_10k = (float(total_cost) / rows * 10000.0) if rows and total_cost is not None else None

    lines: list[str] = [
        f"# Goldie Operator Report - {manifest.get('corpus') or run_dir.name}",
        "",
        f"**Status:** `{state}`",
        f"**Reason:** {reason}",
        "",
        "This report is generated from `manifest.json` and `report.json`. It measures extraction completeness and quality queues; it does not prove absolute field accuracy without audited truth.",
        "",
        "## Run Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Rows | {rows} |",
        f"| Fetch OK | {fetch_ok}/{rows} ({_pct(fetch_ok, rows)}) |",
        f"| Failed rows | {failed} |",
        f"| All-core-empty | {qcounts.get('all_core_empty', 0)} |",
        f"| Unresolved all-core-empty | {qcounts.get('unresolved_all_core_empty', 0)} |",
        f"| Extraction-miss rows | {report.get('extraction_miss', qcounts.get('extraction_miss', 0))} |",
        f"| Bot-check empty rows | {qcounts.get('bot_check_empty', 0)} |",
        f"| Fallback attempted | {fallback.get('fallback_attempted', 0)} |",
        f"| Fallback returned | {fallback.get('fallback_returned', '-')} |",
        f"| Fallback used | {fallback.get('fallback_used', 0)} |",
        f"| Tier-1 cost | {_money(report.get('cost_usd', manifest.get('cost_usd')))} |",
        f"| Fallback cost | {_money(fallback.get('cost_usd'))} |",
        f"| Total cost | {_money(total_cost)} |",
        "",
        "## Field Presence",
        "",
        "| Field | Present | Missing | Presence |",
        "|---|---:|---:|---:|",
    ]

    presence = report.get("field_presence") or {}
    missing = report.get("field_missing") or {}
    for field in ["authors", "rases", "ca", "abstract", "pdf_url"]:
        fp = presence.get(field, {})
        fm = missing.get(field, {})
        present = int(fp.get("present") or 0)
        field_rows = int(fp.get("rows") or rows)
        miss = int(fm.get("missing") if fm.get("missing") is not None else field_rows - present)
        lines.append(f"| {field} | {present}/{field_rows} | {miss} | {_pct(present, field_rows)} |")

    lines += [
        "",
        "## Quality Queues",
        "",
        "| Queue | Count | Sample DOIs |",
        "|---|---:|---|",
    ]
    queue_order = [
        "unresolved_all_core_empty",
        "all_core_empty",
        "terminal_flagged_empty",
        "bot_check_empty",
        "extraction_miss",
        "ca_needs_evidence_audit",
        "multi_priority_missing",
    ]
    for key in queue_order:
        lines.append(f"| {key} | {qcounts.get(key, 0)} | {_doi_sample(qf.get(key) or [])} |")

    lines += [
        "",
        "## 10K Projection",
        "",
        "| Projection | Value |",
        "|---|---:|",
        f"| Cost per 100 | {_money(cost_per_100)} |",
        f"| Cost per 10K | {_money(cost_10k)} |",
        f"| Fallback attempt rate | {_pct(fallback.get('fallback_attempted', 0), rows)} |",
        f"| Fallback utilization rate | {_pct(fallback.get('fallback_used', 0), rows)} |",
        "",
        "## Operator Commands",
        "",
    ]
    lines.extend(f"- {cmd}" for cmd in _command_rows(run_dir, manifest))
    lines += [
        "",
        "## Accuracy Boundary",
        "",
        "Crossref is sampling-only. Field values must come from DOI.org-resolved publisher pages, Taxicab/cache HTML, or rendered-browser evidence.",
        "",
        "Do not claim 98% absolute accuracy from this report alone. A 98% claim requires scored validation against audited truth, such as an existing holdout or a manually audited subset.",
        "",
    ]
    return "\n".join(lines)


def write_operator_report(path: Path, markdown: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(markdown, encoding="utf-8")
    tmp.replace(path)
