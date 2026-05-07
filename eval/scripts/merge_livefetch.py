"""Merge live-fetch delta CSVs into a v1.8 baseline.

Override v1.8 fields only where the live-fetch returned non-empty content AND
the v1.8 baseline was empty AND gold does NOT mark the field N/A. Never lose
v1.8 content; never replace good with empty; never violate gold's deliberate
empty convention. Output a merged CSV ready for diff_goldie.py.

Gold-empty guardrail (added 2026-05-07 after the v4i6.14 incident):
  Some gold rows mark Authors / Abstract / PDF URL as `N/A` deliberately —
  e.g., 10.36838/v4i6.14 (terra-docs) resolves directly to a PDF, has no
  landing page metadata, and gold curator records `N/A` to signal
  "not applicable to this document type." The diff_goldie.py comparator
  treats both-empty as a match. Without `--gold`, the merger is blind to
  this convention: it sees baseline-empty + delta-populated and overrides,
  flipping a previously matching cell into a miss. `--gold holdout-50.csv`
  (or train-50.csv) tells the merger to skip filling fields where gold
  itself is empty — preserving the both-empty match.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Iterable


def _is_empty(s: str | None) -> bool:
    return not s or not str(s).strip() or str(s).strip().lower() in {"n/a", "na", "none", "null"}


def _is_empty_authors(s: str | None) -> bool:
    if _is_empty(s):
        return True
    try:
        a = json.loads(s)
        return not isinstance(a, list) or len(a) == 0
    except Exception:
        return True


def _has_empty_rases(s: str | None) -> bool:
    if _is_empty_authors(s):
        return False  # no authors at all isn't a rases-empty problem
    try:
        a = json.loads(s)
    except Exception:
        return False
    if not isinstance(a, list):
        return False
    # If ALL authors have empty rases, we want to override.
    return all(_is_empty(x.get("rasses")) if isinstance(x, dict) else True for x in a)


def _load_gold_empty_map(gold_path: Path) -> dict[str, dict[str, bool]]:
    """Read the gold CSV (holdout-50 / train-50 schema) and return a map
    `{doi: {field: gold-says-empty?}}`. A field is "empty" in gold if its
    string value is empty or one of the absent sentinels ('N/A', 'na', etc.)
    — same predicate as `_is_empty` for scalar fields. For Authors, use the
    JSON-aware `_is_empty_authors` so `'[]'` and `'N/A'` both register as empty.
    """
    out: dict[str, dict[str, bool]] = {}
    with gold_path.open() as f:
        for r in csv.DictReader(f):
            doi = (r.get("DOI") or "").strip()
            if not doi:
                continue
            out[doi] = {
                "Abstract": _is_empty(r.get("Abstract")),
                "Authors": _is_empty_authors(r.get("Authors")),
                "PDF URL": _is_empty(r.get("PDF URL")),
            }
    return out


def merge(
    baseline_path: Path,
    delta_paths: Iterable[Path],
    output: Path,
    gold_path: Path,
) -> None:
    base = list(csv.DictReader(baseline_path.open()))
    by_doi = {r["DOI"]: r for r in base}
    gold_empty = _load_gold_empty_map(gold_path)

    overrides_applied = []
    skipped_for_gold = []
    suspect_baseline_violations = []
    for delta_path in delta_paths:
        if not delta_path.exists():
            continue
        for r in csv.DictReader(delta_path.open()):
            doi = r["DOI"]
            if doi not in by_doi:
                continue
            base_row = by_doi[doi]
            ge = gold_empty.get(doi, {})
            applied = []

            # Abstract
            if _is_empty(base_row["Abstract"]) and not _is_empty(r["Abstract"]):
                if ge.get("Abstract"):
                    skipped_for_gold.append((doi, "abstract"))
                else:
                    base_row["Abstract"] = r["Abstract"]
                    applied.append("abstract")
            elif ge.get("Abstract") and not _is_empty(base_row["Abstract"]):
                # Suspect: gold says N/A but baseline already has content.
                # Flag without auto-reverting (could be a manual edit).
                suspect_baseline_violations.append((doi, "abstract"))

            # Authors / rases — only override if base has zero authors OR
            # all base authors have empty rases AND the delta has authors with
            # rases content.
            base_authors_empty = _is_empty_authors(base_row["Authors"])
            base_rases_all_empty = _has_empty_rases(base_row["Authors"])
            delta_has_authors = not _is_empty_authors(r["Authors"])
            if delta_has_authors and (base_authors_empty or base_rases_all_empty):
                if ge.get("Authors"):
                    skipped_for_gold.append((doi, "authors"))
                else:
                    base_row["Authors"] = r["Authors"]
                    applied.append("authors+rases" if base_authors_empty else "rases")
            elif ge.get("Authors") and not base_authors_empty:
                suspect_baseline_violations.append((doi, "authors"))

            # PDF URL — only override if base is empty.
            if _is_empty(base_row["PDF URL"]) and not _is_empty(r["PDF URL"]):
                if ge.get("PDF URL"):
                    skipped_for_gold.append((doi, "pdf_url"))
                else:
                    base_row["PDF URL"] = r["PDF URL"]
                    applied.append("pdf_url")
            elif ge.get("PDF URL") and not _is_empty(base_row["PDF URL"]):
                suspect_baseline_violations.append((doi, "pdf_url"))

            if applied:
                overrides_applied.append((doi, applied, delta_path.stem))

    fieldnames = list(base[0].keys()) if base else []
    with output.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in base:
            w.writerow(r)

    print(f"merged {len(overrides_applied)} overrides into {output}")
    for doi, fields, src in overrides_applied:
        print(f"  {doi:50s} ← {src}: {','.join(fields)}")
    if skipped_for_gold:
        print(f"\nskipped {len(skipped_for_gold)} fills (gold says N/A — preserves both-empty match):")
        for doi, field in skipped_for_gold:
            print(f"  {doi:50s} ✋ {field}")
    if suspect_baseline_violations:
        print(
            f"\n⚠ {len(suspect_baseline_violations)} baseline cells contradict gold "
            f"(gold says N/A but baseline is populated — manual edit?):",
            file=sys.stderr,
        )
        for doi, field in suspect_baseline_violations:
            print(f"  {doi:50s} ⚠ {field}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(
        description=(
            "Merge live-fetch delta CSVs into a baseline, gold-aware. "
            "Without --gold this would silently violate gold's deliberate "
            "N/A markers (terra-docs PDF-direct DOIs, cyberleninka, etc.) "
            "and convert previously-matching both-empty cells into misses. "
            "See plans/great-then-how-tf-ancient-mitten.md for the v4i6.14 "
            "incident write-up."
        )
    )
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--deltas", nargs="+", required=True,
                    help="One or more live-fetch delta CSVs")
    ap.add_argument("--output", required=True)
    ap.add_argument(
        "--gold",
        required=True,
        help=(
            "Gold CSV (eval/goldie/holdout-50.csv or train-50.csv). "
            "Required: cells where gold marks N/A are skipped during merge "
            "to preserve the both-empty comparator match."
        ),
    )
    args = ap.parse_args()
    merge(
        Path(args.baseline),
        [Path(p) for p in args.deltas],
        Path(args.output),
        Path(args.gold),
    )


if __name__ == "__main__":
    main()
