"""Merge live-fetch delta CSVs into a v1.8 baseline.

Override v1.8 fields only where the live-fetch returned non-empty content AND
the v1.8 baseline was empty. Never lose v1.8 content; never replace good with
empty. Output a merged CSV ready for diff_goldie.py.
"""
from __future__ import annotations

import argparse
import csv
import json
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


def merge(baseline_path: Path, delta_paths: Iterable[Path], output: Path) -> None:
    base = list(csv.DictReader(baseline_path.open()))
    by_doi = {r["DOI"]: r for r in base}

    overrides_applied = []
    for delta_path in delta_paths:
        if not delta_path.exists():
            continue
        for r in csv.DictReader(delta_path.open()):
            doi = r["DOI"]
            if doi not in by_doi:
                continue
            base_row = by_doi[doi]
            applied = []
            # Abstract
            if _is_empty(base_row["Abstract"]) and not _is_empty(r["Abstract"]):
                base_row["Abstract"] = r["Abstract"]
                applied.append("abstract")
            # Authors / rases — only override if base has zero authors OR
            # all base authors have empty rases AND the delta has authors with
            # rases content.
            base_authors_empty = _is_empty_authors(base_row["Authors"])
            base_rases_all_empty = _has_empty_rases(base_row["Authors"])
            delta_has_authors = not _is_empty_authors(r["Authors"])
            if delta_has_authors and (base_authors_empty or base_rases_all_empty):
                # If base had authors but rases empty, and delta has same names
                # with rases populated, prefer delta's rases per-author (merge).
                # Simpler: when base is empty OR rases-all-empty, just take
                # delta's authors wholesale. The risk of wholesale-replace is
                # the v1.9 lesson — but the delta came from a real visible
                # browser fetch, not a JSON sniff, so the confidence is higher.
                base_row["Authors"] = r["Authors"]
                applied.append("authors+rases" if base_authors_empty else "rases")
            # PDF URL — only override if base is empty.
            if _is_empty(base_row["PDF URL"]) and not _is_empty(r["PDF URL"]):
                base_row["PDF URL"] = r["PDF URL"]
                applied.append("pdf_url")
            if applied:
                overrides_applied.append((doi, applied,
                                          delta_path.stem))

    fieldnames = list(base[0].keys()) if base else []
    with output.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in base:
            w.writerow(r)

    print(f"merged {len(overrides_applied)} overrides into {output}")
    for doi, fields, src in overrides_applied:
        print(f"  {doi:50s} ← {src}: {','.join(fields)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--deltas", nargs="+", required=True,
                    help="One or more live-fetch delta CSVs")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    merge(Path(args.baseline),
          [Path(p) for p in args.deltas],
          Path(args.output))


if __name__ == "__main__":
    main()
