"""Sample N random Crossref DOIs that aren't already in the manual gold standard.

Default target: 10,000. Output schema matches `eval/gold-standard.csv` — DOI/Link
populated, extraction columns blank for the downstream cloud Tasks pipeline to
fill via `extract_batch_cloud.py`.

Differences from `sample_50_random_dois.py`:
  - Resumable. Maintains `<output>.partial.jsonl` of accepted DOIs; re-running
    resumes from there. Pass `--force` to start fresh.
  - Filters non-article Crossref `type` values (book, dataset, journal-issue,
    report-component, etc.).
  - Polite-pool friendly: 1 req/sec between Crossref hits, full mailto in UA.

Usage:
    eval/.venv/bin/python eval/scripts/sample_10k_dois.py
    eval/.venv/bin/python eval/scripts/sample_10k_dois.py --target 10000 \
        --output eval/data/ai-goldie-source-10k.csv
    eval/.venv/bin/python eval/scripts/sample_10k_dois.py --force        # rebuild
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import requests

EVAL_DIR = Path(__file__).resolve().parent.parent
GOLD_CSV = EVAL_DIR / "gold-standard.csv"
DEFAULT_OUTPUT = EVAL_DIR / "data" / "ai-goldie-source-10k.csv"
DEFAULT_TARGET = 10_000

CROSSREF_URL = "https://api.crossref.org/works"
POLITE_EMAIL = "reach2shubhankar@gmail.com"
SAMPLE_BATCH = 100  # Crossref's max per call
SLEEP_SEC = 1.0
PROGRESS_EVERY = 500

COLUMNS = [
    "No", "DOI", "Link", "Authors", "Abstract", "PDF URL",
    "Status", "Notes", "Has Bot Check", "Resolves To PDF",
    "broken_doi", "no english",
]

# Crossref `type` values to drop. Target is journal-article-like content.
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


def load_gold_dois(gold_csv: Path) -> set[str]:
    if not gold_csv.exists():
        return set()
    with gold_csv.open("r", encoding="utf-8", newline="") as f:
        return {(r["DOI"] or "").strip().lower() for r in csv.DictReader(f) if r.get("DOI")}


def load_partial(partial_path: Path) -> list[str]:
    """Load already-accepted DOIs from the resume file (newline-delimited JSON)."""
    if not partial_path.exists():
        return []
    accepted: list[str] = []
    with partial_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                doi = (obj.get("doi") or "").strip().lower()
                if doi:
                    accepted.append(doi)
            except json.JSONDecodeError:
                continue
    return accepted


def append_partial(partial_path: Path, doi: str) -> None:
    partial_path.parent.mkdir(parents=True, exist_ok=True)
    with partial_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"doi": doi}) + "\n")


def fetch_sample() -> list[dict]:
    params = {"sample": SAMPLE_BATCH, "mailto": POLITE_EMAIL}
    headers = {"User-Agent": f"parseland-eval/0.2 (mailto:{POLITE_EMAIL})"}
    resp = requests.get(CROSSREF_URL, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json().get("message", {}).get("items", [])


def keep_item(item: dict) -> bool:
    t = (item.get("type") or "").strip().lower()
    if t in SKIP_TYPES:
        return False
    return True


def write_csv(path: Path, dois: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for i, doi in enumerate(dois, start=1):
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
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", type=int, default=DEFAULT_TARGET)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--gold", type=Path, default=GOLD_CSV)
    ap.add_argument("--force", action="store_true",
                    help="discard any existing partial + final CSV and rebuild from scratch")
    args = ap.parse_args()

    partial_path = args.output.with_suffix(args.output.suffix + ".partial.jsonl")

    if args.force:
        if partial_path.exists():
            partial_path.unlink()
        if args.output.exists():
            args.output.unlink()

    gold = load_gold_dois(args.gold)
    print(f"loaded {len(gold)} DOIs from gold standard ({args.gold})", file=sys.stderr)

    accepted = load_partial(partial_path)
    print(f"resuming with {len(accepted)} DOIs from {partial_path.name}", file=sys.stderr)

    seen = set(accepted) | gold
    rejected_types = 0
    rejected_dups = 0
    api_calls = 0
    last_progress = len(accepted)

    while len(accepted) < args.target:
        items = fetch_sample()
        api_calls += 1
        for item in items:
            doi = (item.get("DOI") or "").strip().lower()
            if not doi:
                continue
            if doi in seen:
                rejected_dups += 1
                continue
            if not keep_item(item):
                rejected_types += 1
                seen.add(doi)
                continue
            accepted.append(doi)
            seen.add(doi)
            append_partial(partial_path, doi)
            if len(accepted) >= args.target:
                break

        if len(accepted) - last_progress >= PROGRESS_EVERY or len(accepted) >= args.target:
            print(
                f"  {len(accepted)}/{args.target} accepted | "
                f"api={api_calls} dup_drop={rejected_dups} type_drop={rejected_types}",
                file=sys.stderr,
            )
            last_progress = len(accepted)

        if len(accepted) < args.target:
            time.sleep(SLEEP_SEC)

    accepted = accepted[: args.target]
    write_csv(args.output, accepted)

    print(f"wrote {len(accepted)} unique DOIs to {args.output}")
    print(f"  api calls: {api_calls}")
    print(f"  duplicates dropped: {rejected_dups}")
    print(f"  non-article types dropped: {rejected_types}")
    print(f"  resume file: {partial_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
