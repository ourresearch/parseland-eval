"""Sample 50 fresh random Crossref DOIs, deduped against existing gold sets.

One-shot helper. Mirrors the filter logic of `eval/scripts/sample_10k_dois.py`
(drops non-article Crossref `type` values; polite-pool friendly with mailto UA).

Excludes DOIs already present in:
  - eval/goldie/train-50.csv
  - eval/goldie/holdout-50.csv
  - eval/human-goldie.csv

Writes:
  - eval/scratch/sample-50-fresh.txt    (one DOI per line)
  - eval/scratch/sample-50-fresh.csv    (gold-standard schema, blank cells)
"""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

import requests

EVAL_DIR = Path(__file__).resolve().parent.parent
SCRATCH_DIR = Path(__file__).resolve().parent

EXCLUDE_CSVS = [
    EVAL_DIR / "goldie" / "train-50.csv",
    EVAL_DIR / "goldie" / "holdout-50.csv",
    EVAL_DIR / "human-goldie.csv",
]

OUT_TXT = SCRATCH_DIR / "sample-50-fresh.txt"
OUT_CSV = SCRATCH_DIR / "sample-50-fresh.csv"

CROSSREF_URL = "https://api.crossref.org/works"
POLITE_EMAIL = "reach2shubhankar@gmail.com"
TARGET = 50
SAMPLE_BATCH = 100
SLEEP_SEC = 1.0

SKIP_TYPES = {
    "book",
    "book-set",
    "book-series",
    "dataset",
    "journal",
    "journal-issue",
    "journal-volume",
    "report-component",
    "edited-book",
    "monograph",
    "reference-book",
    "component",
    "grant",
    "peer-review",
    "standard",
}

CSV_COLUMNS = [
    "No", "DOI", "Link", "Authors", "Abstract", "PDF URL",
    "Status", "Notes", "Has Bot Check", "Resolves To PDF",
    "broken_doi", "no english",
]


def load_existing_dois(paths: list[Path]) -> set[str]:
    seen: set[str] = set()
    for p in paths:
        if not p.exists():
            print(f"  warn: {p} not found, skipping", file=sys.stderr)
            continue
        with p.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                doi = (row.get("DOI") or "").strip().lower()
                if doi:
                    seen.add(doi)
        print(f"  loaded DOIs from {p.name}", file=sys.stderr)
    return seen


def fetch_sample() -> list[dict]:
    headers = {"User-Agent": f"parseland-eval/0.2 (mailto:{POLITE_EMAIL})"}
    params = {"sample": SAMPLE_BATCH, "mailto": POLITE_EMAIL}
    resp = requests.get(CROSSREF_URL, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json().get("message", {}).get("items", [])


def main() -> int:
    excluded = load_existing_dois(EXCLUDE_CSVS)
    print(f"excluding {len(excluded)} DOIs already in gold sets", file=sys.stderr)

    accepted: list[str] = []
    seen = set(excluded)
    api_calls = 0
    type_drop = 0
    dup_drop = 0

    while len(accepted) < TARGET:
        items = fetch_sample()
        api_calls += 1
        for item in items:
            doi = (item.get("DOI") or "").strip().lower()
            if not doi or doi in seen:
                if doi:
                    dup_drop += 1
                continue
            t = (item.get("type") or "").strip().lower()
            if t in SKIP_TYPES:
                type_drop += 1
                seen.add(doi)
                continue
            accepted.append(doi)
            seen.add(doi)
            if len(accepted) >= TARGET:
                break
        if len(accepted) < TARGET:
            time.sleep(SLEEP_SEC)

    OUT_TXT.write_text("\n".join(accepted) + "\n", encoding="utf-8")

    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for i, doi in enumerate(accepted, start=1):
            w.writerow({
                "No": i,
                "DOI": doi,
                "Link": f"https://doi.org/{doi}",
                "Authors": "",
                "Abstract": "",
                "PDF URL": "",
                "Status": "",
                "Notes": "",
                "Has Bot Check": "",
                "Resolves To PDF": "",
                "broken_doi": "",
                "no english": "",
            })

    print(f"\nwrote {len(accepted)} DOIs", file=sys.stderr)
    print(f"  txt: {OUT_TXT}", file=sys.stderr)
    print(f"  csv: {OUT_CSV}", file=sys.stderr)
    print(f"  api calls: {api_calls} | type_drop={type_drop} dup_drop={dup_drop}", file=sys.stderr)

    print()
    for i, doi in enumerate(accepted, start=1):
        print(f"{i:>2}. {doi}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
