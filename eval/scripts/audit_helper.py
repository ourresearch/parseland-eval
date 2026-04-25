"""Resolve each DOI in audit-checklist.csv to its final landing-page URL.

Polite GET to https://doi.org/{DOI}, follows redirects, writes the final URL
back into the `landing_page_url` column. Idempotent — rows that already have
a non-empty `landing_page_url` are skipped, so re-running only fills gaps.

Does NOT scrape any content beyond the URL (no authors, abstract, PDF parsing
— manual audit handles that).

Usage:
    python eval/scripts/audit_helper.py
    python eval/scripts/audit_helper.py --input eval/goldie/audit-checklist.csv

Behavior:
    - 1 req/sec sleep between requests.
    - 20s timeout per request.
    - Errors written as 'ERROR: <message>' so you can see and retry by clearing the cell.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import requests

DEFAULT_INPUT = Path("eval/goldie/audit-checklist.csv")
USER_AGENT = "parseland-eval-audit (mailto:reach2shubhankar@gmail.com)"
TIMEOUT = 20
SLEEP_SEC = 1.0


def resolve(doi: str, session: requests.Session) -> str:
    url = f"https://doi.org/{doi}"
    try:
        resp = session.get(url, allow_redirects=True, timeout=TIMEOUT)
    except requests.RequestException as e:
        return f"ERROR: {type(e).__name__}: {e}"
    if resp.status_code >= 400:
        return f"ERROR: HTTP {resp.status_code} (final URL: {resp.url})"
    return resp.url


def run(input_path: Path) -> tuple[int, int]:
    with input_path.open(newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    if not fieldnames or "landing_page_url" not in fieldnames or "doi" not in fieldnames:
        raise SystemExit(f"{input_path} missing required columns 'doi' and 'landing_page_url'")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    resolved = 0
    errors = 0
    pending = [r for r in rows if not (r.get("landing_page_url") or "").strip()]
    print(f"resolving {len(pending)} of {len(rows)} rows ({len(rows) - len(pending)} already filled)")

    for i, row in enumerate(pending, 1):
        doi = (row.get("doi") or "").strip()
        if not doi:
            continue
        result = resolve(doi, session)
        row["landing_page_url"] = result
        if result.startswith("ERROR:"):
            errors += 1
            print(f"  [{i}/{len(pending)}] {doi} -> {result}")
        else:
            resolved += 1
            print(f"  [{i}/{len(pending)}] {doi} -> {result}")
        time.sleep(SLEEP_SEC)

    with input_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"done: {resolved} resolved, {errors} errors, written back to {input_path}")
    return resolved, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help=f"default: {DEFAULT_INPUT}")
    args = parser.parse_args(argv)
    run(args.input)
    return 0


if __name__ == "__main__":
    sys.exit(main())
