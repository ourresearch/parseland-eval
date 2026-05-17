"""Triage the 10K judge-output into good / rerun-no-authors / rerun-no-rases buckets.

Reads `runs/10k/batch-{1..100}-judge/ai-goldie-1.v2.csv` (one file per batch,
all named `ai-goldie-1.*` regardless of batch — the batch identity is in the
directory name). Emits three CSVs under `--output-dir`:

  good-records.csv        rows with non-empty Authors AND ≥1 author with non-empty rases
  rerun-no-authors.csv    rows whose Authors field is empty / [] / "N/A"
  rerun-no-rases.csv      rows whose Authors are populated but ALL authors have empty rases

Each rerun CSV adds a `doi_prefix` column (e.g. `10.1016`, `10.1109`) so the
downstream `rerun_targeted.py` can filter per publisher.

The classification predicates are imported from `merge_livefetch` — single
source of truth for "what counts as empty?".

Loud failures (per project no-silent-failure rule):
  - fewer than 100 batch files          -> exit 2, list missing batches
  - any input CSV missing required cols -> exit 3
  - file-system error                   -> exit 1
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# Reuse the existing predicates so "empty" is defined once across the codebase.
from merge_livefetch import (  # noqa: E402
    _has_empty_rases,
    _is_empty_authors,
)

log = logging.getLogger("triage-10k")

EXPECTED_BATCHES = 100
V2_FILENAME = "ai-goldie-1.v2.csv"
REQUIRED_COLS = (
    "No", "DOI", "Link", "Authors", "Abstract", "PDF URL",
    "Status", "Notes", "Has Bot Check", "Resolves To PDF",
    "broken_doi", "no english",
)

GOOD = "good"
RERUN_NO_AUTHORS = "rerun_no_authors"
RERUN_NO_RASES = "rerun_no_rases"


def doi_prefix(doi: str) -> str:
    """First two `/`-delimited segments of a DOI: `10.1016/j.foo` -> `10.1016`.

    DOIs without a `/` (degenerate) return the raw stripped value so they
    still group together in the summary.
    """
    if not doi:
        return ""
    parts = doi.strip().split("/", 1)
    return parts[0]


def classify(row: dict[str, str]) -> str:
    """Bucket a v2 CSV row into GOOD / RERUN_NO_AUTHORS / RERUN_NO_RASES."""
    authors = row.get("Authors") or ""
    if _is_empty_authors(authors):
        return RERUN_NO_AUTHORS
    if _has_empty_rases(authors):
        return RERUN_NO_RASES
    return GOOD


def _atomic_write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, str]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    n = 0
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
            n += 1
    os.replace(tmp, path)
    return n


def find_batch_files(batches_dir: Path) -> tuple[list[Path], list[int]]:
    """Return (existing batch CSV paths, missing batch numbers)."""
    found: list[Path] = []
    missing: list[int] = []
    for n in range(1, EXPECTED_BATCHES + 1):
        p = batches_dir / f"batch-{n}-judge" / V2_FILENAME
        if p.exists():
            found.append(p)
        else:
            missing.append(n)
    return found, missing


def triage(batches_dir: Path, output_dir: Path) -> int:
    found, missing = find_batch_files(batches_dir)
    if missing:
        log.error(
            "missing %d/%d batch CSVs under %s: batches %s",
            len(missing), EXPECTED_BATCHES, batches_dir,
            ",".join(str(m) for m in missing),
        )
        return 2

    good: list[dict[str, str]] = []
    no_authors: list[dict[str, str]] = []
    no_rases: list[dict[str, str]] = []
    seen_dois: dict[str, str] = {}  # DOI -> first source path, for dup detection

    per_prefix: dict[str, Counter[str]] = defaultdict(Counter)
    total_rows = 0

    for csv_path in found:
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            missing_cols = [c for c in REQUIRED_COLS if c not in (reader.fieldnames or [])]
            if missing_cols:
                log.error("%s: missing required columns %s", csv_path, missing_cols)
                return 3
            for row in reader:
                doi = (row.get("DOI") or "").strip()
                if not doi:
                    continue
                total_rows += 1
                if doi in seen_dois:
                    log.warning(
                        "duplicate DOI %s (first in %s, also in %s) — last-write-wins",
                        doi, seen_dois[doi], csv_path,
                    )
                seen_dois[doi] = str(csv_path)

                bucket = classify(row)
                prefix = doi_prefix(doi)
                per_prefix[prefix][bucket] += 1

                # Add doi_prefix only to rerun rows (good-records keeps original schema).
                row_with_prefix = {**row, "doi_prefix": prefix}
                if bucket == GOOD:
                    good.append(row)
                elif bucket == RERUN_NO_AUTHORS:
                    no_authors.append(row_with_prefix)
                else:
                    no_rases.append(row_with_prefix)

    # Write outputs
    good_path = output_dir / "good-records.csv"
    nra_path = output_dir / "rerun-no-authors.csv"
    nrs_path = output_dir / "rerun-no-rases.csv"

    try:
        n_good = _atomic_write_csv(good_path, list(REQUIRED_COLS), good)
        n_nra = _atomic_write_csv(
            nra_path, list(REQUIRED_COLS) + ["doi_prefix"], no_authors,
        )
        n_nrs = _atomic_write_csv(
            nrs_path, list(REQUIRED_COLS) + ["doi_prefix"], no_rases,
        )
    except OSError as e:
        log.error("atomic write failed: %s", e)
        return 1

    # Summary
    _print_summary(total_rows, n_good, n_nra, n_nrs, per_prefix)
    log.info("wrote %s (%d rows)", good_path, n_good)
    log.info("wrote %s (%d rows)", nra_path, n_nra)
    log.info("wrote %s (%d rows)", nrs_path, n_nrs)
    return 0


def _print_summary(
    total: int, n_good: int, n_nra: int, n_nrs: int,
    per_prefix: dict[str, Counter[str]],
) -> None:
    print()
    print(f"=== triage summary ({total:,} rows across {EXPECTED_BATCHES} batches) ===")
    print(f"  GOOD                : {n_good:>6,}  ({n_good/total*100:5.1f}%)")
    print(f"  RERUN_NO_AUTHORS    : {n_nra:>6,}  ({n_nra/total*100:5.1f}%)")
    print(f"  RERUN_NO_RASES      : {n_nrs:>6,}  ({n_nrs/total*100:5.1f}%)")
    print()

    # Highlighted publishers
    print("=== priority publishers (Elsevier + IEEE) ===")
    for prefix, label in [("10.1016", "Elsevier"), ("10.1109", "IEEE")]:
        c = per_prefix.get(prefix, Counter())
        total_p = sum(c.values())
        rerun_p = c[RERUN_NO_AUTHORS] + c[RERUN_NO_RASES]
        print(
            f"  {prefix} ({label:8s}) : total={total_p:>5,}  "
            f"good={c[GOOD]:>5,}  no_authors={c[RERUN_NO_AUTHORS]:>5,}  "
            f"no_rases={c[RERUN_NO_RASES]:>5,}  rerun_total={rerun_p:>5,}"
        )
    print()

    # All prefixes ranked by total rerun count
    print("=== all prefixes by rerun volume (top 30) ===")
    print(f"  {'prefix':<14} {'total':>7} {'good':>7} {'no_auth':>8} {'no_rases':>9} {'rerun':>7}")
    ranked = sorted(
        per_prefix.items(),
        key=lambda kv: -(kv[1][RERUN_NO_AUTHORS] + kv[1][RERUN_NO_RASES]),
    )
    for prefix, c in ranked[:30]:
        total_p = sum(c.values())
        rerun_p = c[RERUN_NO_AUTHORS] + c[RERUN_NO_RASES]
        if rerun_p == 0 and prefix not in {"10.1016", "10.1109"}:
            continue
        print(
            f"  {prefix:<14} {total_p:>7,} {c[GOOD]:>7,} "
            f"{c[RERUN_NO_AUTHORS]:>8,} {c[RERUN_NO_RASES]:>9,} {rerun_p:>7,}"
        )
    print()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--batches-dir", type=Path, default=Path("runs/10k"),
        help="Directory containing batch-{1..100}-judge subdirs (default: runs/10k)",
    )
    ap.add_argument(
        "--output-dir", type=Path, default=Path("runs/10k/triage"),
        help="Output directory for the three triage CSVs (default: runs/10k/triage)",
    )
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )

    return triage(args.batches_dir, args.output_dir)


if __name__ == "__main__":
    sys.exit(main())
