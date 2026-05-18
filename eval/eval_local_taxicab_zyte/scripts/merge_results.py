"""Merge the two recovery jobs back with the GOOD bucket → merged-FINAL.csv + CASEY-SUMMARY.md.

Inputs:
    runs/authors-local-<label>-*/results.csv  (latest by ts)
    runs/rases-zyte-<label>-*/results.csv     (latest by ts)
    runs/10k/triage/good-records.csv          (carry-through good rows)

Output:
    runs/merged/merged-<label>-<ts>/
        merged-FINAL.csv          gold-shape rows for all 10K DOIs
        CASEY-SUMMARY.md          per-publisher before/after fill rates

The "before" baseline is the upstream triage counts; "after" reflects
this workspace's recovery output. Numbers framed as fill-rate deltas per
DOI prefix (Elsevier, IEEE, Springer, …).
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _common import (  # noqa: E402
    OUTPUT_COLUMNS,
    REPO_ROOT,
    WORKSPACE_DIR,
    setup_logging,
)

log = logging.getLogger("merge_results")

DEFAULT_GOOD = REPO_ROOT / "runs" / "10k" / "triage" / "good-records.csv"


# --- helpers ----------------------------------------------------------------

def latest_run_dir(parent: Path, prefix: str, label: str) -> Path | None:
    """Pick the most recently created `runs/<prefix>-<label>-<ts>/`."""
    candidates = sorted(
        parent.glob(f"{prefix}-{label}-*"),
        key=lambda p: p.stat().st_mtime,
    )
    return candidates[-1] if candidates else None


def doi_prefix(doi: str) -> str:
    if not doi:
        return ""
    return doi.strip().split("/", 1)[0]


def _is_empty_authors_str(authors_str: str) -> bool:
    s = (authors_str or "").strip()
    if not s or s in ("[]", "N/A", "N/A`"):
        return True
    try:
        parsed = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return True
    if not isinstance(parsed, list) or not parsed:
        return True
    return False


def _all_rases_empty(authors_str: str) -> bool:
    """True iff Authors parses to a non-empty list AND every entry's
    `rasses` is empty. `rasses` may be a string OR a list (the upstream
    schema accepts both — list shape comes from the Agent's ExtractionOut)."""
    try:
        parsed = json.loads(authors_str or "")
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(parsed, list) or not parsed:
        return False
    for a in parsed:
        if not isinstance(a, dict):
            continue
        r = a.get("rasses")
        if isinstance(r, list):
            if any(str(x or "").strip() for x in r):
                return False
        elif isinstance(r, str) and r.strip():
            return False
        elif r and not isinstance(r, (list, str)):
            # any other truthy non-empty type counts as populated
            return False
    return True


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# --- merge ------------------------------------------------------------------

def merge(
    *,
    good_path: Path,
    authors_dir: Path,
    rases_dir: Path,
    output_dir: Path,
    label: str,
) -> Path:
    good_rows = read_csv(good_path)
    authors_rows = read_csv(authors_dir / "results.csv")
    rases_rows = read_csv(rases_dir / "results.csv")

    log.info("good=%d authors=%d rases=%d", len(good_rows), len(authors_rows), len(rases_rows))

    # Build a DOI→row map so reruns override their triage partners.
    by_doi: dict[str, dict[str, str]] = {}
    for r in good_rows:
        doi = (r.get("DOI") or "").strip()
        if doi:
            by_doi[doi] = {k: r.get(k, "") for k in OUTPUT_COLUMNS}

    # The two rerun outputs land into the same gold-shape — the rerun
    # row wins over the (empty) triage row for the same DOI.
    for src_label, rows in (("authors", authors_rows), ("rases", rases_rows)):
        for r in rows:
            doi = (r.get("DOI") or "").strip()
            if not doi:
                continue
            by_doi[doi] = {k: r.get(k, "") for k in OUTPUT_COLUMNS}

    final_rows = sorted(by_doi.values(), key=lambda r: (
        int(r.get("No") or 0), r.get("DOI") or "",
    ))

    # Renumber `No` 1..N globally — input CSVs carried per-batch No values
    # that collide across the three sources (each 1..100 repeated). Make
    # the merged file's No column a unique sequential row index.
    for i, r in enumerate(final_rows, start=1):
        r["No"] = str(i)

    out_csv = output_dir / "merged-FINAL.csv"
    tmp = out_csv.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        w.writeheader()
        for r in final_rows:
            w.writerow(r)
    tmp.replace(out_csv)
    log.info("wrote %s (%d rows)", out_csv, len(final_rows))

    # CASEY-SUMMARY.md
    summary_path = output_dir / "CASEY-SUMMARY.md"
    _write_summary(
        summary_path=summary_path,
        good_rows=good_rows,
        authors_rows=authors_rows,
        rases_rows=rases_rows,
        final_rows=final_rows,
        label=label,
    )
    log.info("wrote %s", summary_path)
    return out_csv


def _stats_per_prefix(rows: Iterable[dict[str, str]]) -> dict[str, Counter[str]]:
    out: dict[str, Counter[str]] = defaultdict(Counter)
    for r in rows:
        prefix = doi_prefix(r.get("DOI") or "")
        out[prefix]["total"] += 1
        if not _is_empty_authors_str(r.get("Authors") or ""):
            out[prefix]["has_authors"] += 1
        if not _all_rases_empty(r.get("Authors") or "") and not _is_empty_authors_str(r.get("Authors") or ""):
            out[prefix]["has_rases"] += 1
    return out


_PREFIX_LABELS = {
    "10.1016": "Elsevier",
    "10.1109": "IEEE",
    "10.1007": "Springer",
    "10.1002": "Wiley",
    "10.1080": "T&F",
    "10.1177": "SAGE",
    "10.1021": "ACS",
    "10.1093": "OUP",
    "10.1017": "Cambridge",
    "10.1515": "DeGruyter",
    "10.1201": "T&F Books",
    "10.3390": "MDPI",
    "10.1186": "BMC",
    "10.1371": "PLOS",
    "10.1145": "ACM",
    "10.1042": "Portland",
}


def _write_summary(
    *,
    summary_path: Path,
    good_rows: list[dict[str, str]],
    authors_rows: list[dict[str, str]],
    rases_rows: list[dict[str, str]],
    final_rows: list[dict[str, str]],
    label: str,
) -> None:
    final_stats = _stats_per_prefix(final_rows)
    before_stats = _stats_per_prefix(good_rows + [
        # before-recovery, the rerun buckets contributed empty rows;
        # mimic that by zero-ing their authors/rases for the baseline.
        {**r, "Authors": "[]"} for r in (authors_rows + rases_rows)
    ])

    ts = datetime.now().strftime("%Y-%m-%d %H:%M %Z").strip()
    lines: list[str] = []
    a = lines.append
    a(f"# eval_local_taxicab_zyte merge — {label}")
    a("")
    a(f"_Generated {ts}_")
    a("")
    a(f"- Good rows carried through: **{len(good_rows):,}**")
    a(f"- Authors-rerun rows: **{len(authors_rows):,}**")
    a(f"- Rases-rerun rows: **{len(rases_rows):,}**")
    a(f"- Merged FINAL row count: **{len(final_rows):,}**")
    a("")
    a("## Per-publisher fill rates (after merge)")
    a("")
    a("| Prefix | Publisher | Total | Has Authors | % | Has Rases | % | Δ Authors | Δ Rases |")
    a("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    rows = sorted(final_stats.items(), key=lambda kv: -kv[1]["total"])
    for prefix, c in rows:
        if c["total"] < 10:
            continue
        label_pub = _PREFIX_LABELS.get(prefix, "—")
        b = before_stats.get(prefix, Counter())
        pct_a = 100 * c["has_authors"] / c["total"] if c["total"] else 0.0
        pct_r = 100 * c["has_rases"] / c["total"] if c["total"] else 0.0
        d_a = c["has_authors"] - b["has_authors"]
        d_r = c["has_rases"] - b["has_rases"]
        a(
            f"| `{prefix}` | {label_pub} | {c['total']:,} | "
            f"{c['has_authors']:,} | {pct_a:.1f}% | "
            f"{c['has_rases']:,} | {pct_r:.1f}% | "
            f"+{d_a:,} | +{d_r:,} |"
        )
    a("")
    a("## Notes")
    a("")
    a("- Recovery cascade: Taxicab re-harvest → local Chrome (authors job) / Zyte API (rases job, with local Chrome fallback).")
    a("- Empty-authors rule: gold-shape rows with Authors in {[], \"\", \"N/A\"}.")
    a("- Empty-rases rule: Authors populated but every entry's `rasses` is blank.")
    a("- This file is generated; edits will be overwritten next merge.")
    a("")
    summary_path.write_text("\n".join(lines), encoding="utf-8")


# --- driver -----------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    setup_logging()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--label", required=True, help="Label matching the rerun runs")
    ap.add_argument("--good", type=Path, default=DEFAULT_GOOD,
                    help=f"GOOD bucket CSV (default: {DEFAULT_GOOD})")
    ap.add_argument("--authors-run", type=Path, default=None,
                    help="Override run dir for authors (default: latest matching label)")
    ap.add_argument("--rases-run", type=Path, default=None,
                    help="Override run dir for rases (default: latest matching label)")
    args = ap.parse_args(argv)

    if not args.good.exists():
        log.error("good CSV not found: %s", args.good)
        return 2

    runs_parent = WORKSPACE_DIR / "runs"
    authors_dir = args.authors_run or latest_run_dir(runs_parent, "authors-local", args.label)
    rases_dir = args.rases_run or latest_run_dir(runs_parent, "rases-zyte", args.label)
    if not authors_dir or not (authors_dir / "results.csv").exists():
        log.error("authors run dir not found for label=%s", args.label)
        return 2
    if not rases_dir or not (rases_dir / "results.csv").exists():
        log.error("rases run dir not found for label=%s", args.label)
        return 2

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = runs_parent / "merged" / f"merged-{args.label}-{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    merge(
        good_path=args.good,
        authors_dir=authors_dir,
        rases_dir=rases_dir,
        output_dir=out_dir,
        label=args.label,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
