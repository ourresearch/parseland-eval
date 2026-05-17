"""Merge targeted-rerun subset CSVs back into the 10K universe.

Inputs:
  --good   the triage's good-records.csv (rows that were already good in v2)
  --reruns one or more rerun subset CSVs (e.g. rerun/elsevier-no-authors.csv)
  --output  destination for merged-10k-final.csv

Behavior:
  - Start from good-records keyed by DOI (these are never downgraded).
  - For each rerun CSV: iterate rows. For any DOI not present in good-records,
    insert the rerun row. For any DOI already present, the row is already good
    — log it as `redundant_rerun` and SKIP (never overwrite good rows).
  - Conflict policy between two rerun CSVs for the same DOI:
        pick the row whose Authors JSON parses to more entries;
        tie-break: filename alphabetical (deterministic).
        Log every conflict.
  - "Never downgrade" guarantee:
        if a rerun row has empty Authors, it is NEVER written when the
        existing entry (good or earlier rerun) has non-empty Authors.

Stats printed at the end:
  - before/after counts of non-empty Authors and non-empty rases
  - per-doi_prefix remaining empty (after merge)
  - overall fill rate

The script has no `--gold` parameter (unlike merge_livefetch.py): the 10K DOIs
are not in the human goldie set, so the gold-N/A guardrail doesn't apply here.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from merge_livefetch import _has_empty_rases, _is_empty_authors  # noqa: E402
from triage_10k import REQUIRED_COLS, doi_prefix  # noqa: E402

log = logging.getLogger("merge-reruns")


def _author_count(authors_json: str | None) -> int:
    if not authors_json:
        return 0
    try:
        arr = json.loads(authors_json)
    except (json.JSONDecodeError, TypeError):
        return 0
    if not isinstance(arr, list):
        return 0
    return sum(1 for a in arr if isinstance(a, dict) and (a.get("name") or "").strip())


def _is_better_rerun(candidate: dict[str, str], incumbent: dict[str, str]) -> bool:
    """Decide whether `candidate` should replace `incumbent` (both rerun rows).

    Higher author count wins. Tie → False (incumbent stays). The caller is
    expected to drive deterministic alphabetical filename ordering so that
    ties always resolve to the alphabetically-first file's row.
    """
    return _author_count(candidate.get("Authors", "")) > _author_count(
        incumbent.get("Authors", "")
    )


def _row_stats(row: dict[str, str]) -> tuple[bool, bool]:
    """(has_authors, any_author_has_rases) for one row."""
    authors_str = row.get("Authors") or ""
    has_authors = not _is_empty_authors(authors_str)
    has_rases = False
    if has_authors:
        has_rases = not _has_empty_rases(authors_str)
    return has_authors, has_rases


def merge(
    good_path: Path,
    rerun_paths: list[Path],
    output_path: Path,
) -> int:
    if not good_path.exists():
        log.error("good-records not found: %s", good_path)
        return 2

    missing_reruns = [p for p in rerun_paths if not p.exists()]
    if missing_reruns:
        log.error("rerun CSVs not found: %s", missing_reruns)
        return 2

    # Sort rerun paths alphabetically for deterministic tie-breaking.
    rerun_paths = sorted(rerun_paths, key=lambda p: p.name)

    # ---- load good baseline -------------------------------------------------
    by_doi: dict[str, dict[str, str]] = {}
    # Stats: snapshot the "before merge" counts using the good baseline +
    # original rerun rows we're about to overlay.
    before_has_authors = 0
    before_has_rases = 0

    with good_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            doi = (r.get("DOI") or "").strip()
            if not doi:
                continue
            normalized = {k: r.get(k, "") for k in REQUIRED_COLS}
            by_doi[doi] = normalized
            ha, hr = _row_stats(normalized)
            before_has_authors += int(ha)
            before_has_rases += int(hr)

    n_good = len(by_doi)
    log.info("loaded %d good baseline rows from %s", n_good, good_path)

    # ---- ingest reruns ------------------------------------------------------
    rerun_baseline_rows: dict[str, dict[str, str]] = {}
    # Track every DOI we see in any rerun input — its baseline (empty)
    # contributes to the "before" stats so the delta is meaningful.
    redundant = 0
    inserted = 0
    upgraded = 0  # rerun-vs-rerun conflict where new row wins
    conflicts: list[tuple[str, Path, Path]] = []

    for path in rerun_paths:
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                doi = (r.get("DOI") or "").strip()
                if not doi:
                    continue
                normalized = {k: r.get(k, "") for k in REQUIRED_COLS}

                # Capture this row in the "before" baseline once per DOI —
                # use the first time we see it, since the rerun CSV
                # preserves the original (likely empty) cells when the rerun
                # produced nothing.
                if doi not in rerun_baseline_rows:
                    rerun_baseline_rows[doi] = normalized
                    ha, hr = _row_stats(normalized)
                    before_has_authors += int(ha)
                    before_has_rases += int(hr)

                if doi in by_doi and doi not in rerun_baseline_rows:
                    # Should never happen — defensive log.
                    continue

                if doi in by_doi:
                    # Was either (a) already good — skip; OR (b) added by a
                    # prior rerun — apply conflict resolution.
                    if doi not in rerun_baseline_rows:
                        redundant += 1
                        log.info("redundant rerun %s already in good baseline", doi)
                        continue
                    # Conflict between rerun CSVs
                    incumbent = by_doi[doi]
                    if _is_better_rerun(normalized, incumbent):
                        # Find which prior rerun produced incumbent for logging
                        prior_path = _find_owning_rerun(
                            doi, incumbent, rerun_paths, before=path,
                        )
                        conflicts.append((doi, prior_path, path))
                        # Never downgrade: only replace if candidate has authors
                        if _author_count(normalized.get("Authors", "")) > 0:
                            by_doi[doi] = normalized
                            upgraded += 1
                    else:
                        # incumbent wins; still log the conflict if both
                        # have authors (silent if both empty)
                        if _author_count(normalized.get("Authors", "")) > 0:
                            prior_path = _find_owning_rerun(
                                doi, incumbent, rerun_paths, before=path,
                            )
                            conflicts.append((doi, prior_path, path))
                else:
                    # New rerun row → just insert. Never inserts an empty-author
                    # row over a non-empty one because there is no incumbent.
                    by_doi[doi] = normalized
                    inserted += 1

    # ---- write merged output ------------------------------------------------
    try:
        _atomic_write_csv(output_path, list(REQUIRED_COLS), by_doi.values())
    except OSError as e:
        log.error("atomic write failed: %s", e)
        return 1

    # ---- after-stats --------------------------------------------------------
    after_has_authors = 0
    after_has_rases = 0
    after_empty_by_prefix: dict[str, Counter[str]] = defaultdict(Counter)
    for doi, row in by_doi.items():
        ha, hr = _row_stats(row)
        after_has_authors += int(ha)
        after_has_rases += int(hr)
        p = doi_prefix(doi)
        if not ha:
            after_empty_by_prefix[p]["no_authors"] += 1
        elif not hr:
            after_empty_by_prefix[p]["no_rases"] += 1

    total = len(by_doi)
    _print_stats(
        total=total,
        before_has_authors=before_has_authors,
        after_has_authors=after_has_authors,
        before_has_rases=before_has_rases,
        after_has_rases=after_has_rases,
        inserted=inserted,
        upgraded=upgraded,
        redundant=redundant,
        conflicts=conflicts,
        after_empty_by_prefix=after_empty_by_prefix,
    )
    log.info("wrote %s (%d rows)", output_path, total)
    return 0


def _find_owning_rerun(
    doi: str, row: dict[str, str], rerun_paths: list[Path], *, before: Path,
) -> Path:
    """Walk rerun_paths in order, return the first one whose row for `doi`
    matches `row.Authors`. Used only for human-readable conflict logging."""
    target_authors = (row.get("Authors") or "").strip()
    for p in rerun_paths:
        if p == before:
            return p
        with p.open("r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if (r.get("DOI") or "").strip() == doi:
                    if (r.get("Authors") or "").strip() == target_authors:
                        return p
                    break
    return before  # fallback


def _atomic_write_csv(path: Path, fieldnames: list[str], rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
    os.replace(tmp, path)


def _print_stats(
    *, total: int,
    before_has_authors: int, after_has_authors: int,
    before_has_rases: int, after_has_rases: int,
    inserted: int, upgraded: int, redundant: int,
    conflicts: list[tuple[str, Path, Path]],
    after_empty_by_prefix: dict[str, Counter[str]],
) -> None:
    print()
    print(f"=== merge_reruns summary ({total:,} total rows) ===")
    print(f"  Authors non-empty : before {before_has_authors:>6,} -> "
          f"after {after_has_authors:>6,}  (Δ {after_has_authors - before_has_authors:+,})")
    print(f"  rases non-empty   : before {before_has_rases:>6,} -> "
          f"after {after_has_rases:>6,}  (Δ {after_has_rases - before_has_rases:+,})")
    if total:
        print(f"  Overall fill rate : "
              f"Authors {after_has_authors/total*100:5.1f}%  "
              f"rases {after_has_rases/total*100:5.1f}%")
    print()
    print(f"  rerun row dispositions:")
    print(f"    inserted (new DOI in merged set) : {inserted:>5,}")
    print(f"    upgraded (rerun-vs-rerun winner) : {upgraded:>5,}")
    print(f"    redundant (already in good set)  : {redundant:>5,}")
    print(f"    conflicts logged                  : {len(conflicts):>5,}")
    if conflicts[:10]:
        print("  first 10 conflicts (prior_source vs new_source):")
        for doi, prior, new in conflicts[:10]:
            print(f"    {doi:<55s} {prior.name} <- {new.name}")
    print()
    if after_empty_by_prefix:
        print(f"  remaining empties by prefix (top 20):")
        ranked = sorted(
            after_empty_by_prefix.items(),
            key=lambda kv: -(kv[1]["no_authors"] + kv[1]["no_rases"]),
        )
        print(f"    {'prefix':<14} {'no_authors':>11} {'no_rases':>10}")
        for prefix, c in ranked[:20]:
            print(f"    {prefix:<14} {c['no_authors']:>11,} {c['no_rases']:>10,}")
        print()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--good", type=Path, required=True,
                    help="Path to triage's good-records.csv")
    ap.add_argument("--reruns", type=Path, nargs="+", required=True,
                    help="One or more rerun subset CSVs to merge")
    ap.add_argument("--output", type=Path, required=True,
                    help="Destination for merged 10K final CSV")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )

    return merge(args.good, list(args.reruns), args.output)


if __name__ == "__main__":
    sys.exit(main())
