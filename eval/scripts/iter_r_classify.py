"""Apply iter-R classification to a merged batch CSV.

For each row, examines the extraction state (Authors / Abstract / PDF URL fill)
and the resolved DOI landing-page URL, then writes a single iter-R label to
the Notes column when a structural reason for emptiness applies.

Labels (priority order):
    iter-R:bot-check[:perimeterx]    explicit bot-check interstitial
    iter-R:pdf-redirect              DOI resolves directly to a PDF
    iter-R:paywalled=<publisher>     paywalled publisher pattern (rule #10)
    iter-R:extraction-miss           honest failure (no structural reason)

Rule #10 publishers: Elsevier, Springer, Wiley, Taylor & Francis, SAGE, APS,
OUP, Karger, Thieme, IEEE, JSTOR, DeGruyter.

Run:
    eval/.venv/bin/python eval/scripts/iter_r_classify.py \\
        --input  runs/10k/batch-1/ai-goldie-1.merged.csv \\
        --output runs/10k/batch-1/ai-goldie-1.v2.csv
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

USER_AGENT = "parseland-eval/iter-R (mailto:reach2shubhankar@gmail.com)"
TIMEOUT = 8

PAYWALL_PATTERNS = [
    (re.compile(r"linkinghub\.elsevier\.com|sciencedirect\.com"), "elsevier"),
    (re.compile(r"link\.springer\.com"), "springer"),
    (re.compile(r"onlinelibrary\.wiley\.com"), "wiley"),
    (re.compile(r"tandfonline\.com"), "taylor-francis"),
    (re.compile(r"journals\.sagepub\.com"), "sage"),
    (re.compile(r"link\.aps\.org|journals\.aps\.org"), "aps"),
    (re.compile(r"academic\.oup\.com"), "oup"),
    (re.compile(r"karger\.com"), "karger"),
    (re.compile(r"thieme\.com|thieme-connect\.com"), "thieme"),
    (re.compile(r"ieeexplore\.ieee\.org"), "ieee"),
    (re.compile(r"jstor\.org"), "jstor"),
    (re.compile(r"degruyter(brill)?\.com"), "degruyter"),
]

BOT_CHECK_PATTERNS = [
    (re.compile(r"validate\.perfdrive\.com"), "perimeterx"),
    (re.compile(r"challenges\.cloudflare\.com"), "cloudflare"),
]


def _is_empty(s: str | None) -> bool:
    if not s:
        return True
    s = s.strip()
    return not s or s.lower() in {"n/a", "na", "none", "null", "[]"}


def resolve_url(doi: str, session: requests.Session) -> tuple[str | None, str | None]:
    """Follow DOI redirect chain. Returns (final_url, error)."""
    try:
        r = session.get(
            f"https://doi.org/{doi}",
            allow_redirects=True,
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        return r.url, None
    except Exception as exc:
        return None, str(exc)


def classify(row: dict, resolved: str | None) -> str | None:
    authors_empty = _is_empty(row.get("Authors"))
    abstract_empty = _is_empty(row.get("Abstract"))
    pdf_empty = _is_empty(row.get("PDF URL"))

    # If everything is filled, no label needed.
    if not (authors_empty or abstract_empty or pdf_empty):
        return None

    if not resolved:
        return "iter-R:extraction-miss"

    # Priority 1: bot-check (most distinctive URL patterns)
    for pat, name in BOT_CHECK_PATTERNS:
        if pat.search(resolved):
            return f"iter-R:bot-check:{name}"

    # Priority 2: pdf-redirect
    if urlparse(resolved).path.lower().endswith(".pdf"):
        return "iter-R:pdf-redirect"

    # Priority 3: paywalled publisher
    for pat, name in PAYWALL_PATTERNS:
        if pat.search(resolved):
            return f"iter-R:paywalled={name}"

    # Default: extraction-miss
    return "iter-R:extraction-miss"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--sleep", type=float, default=0.1)
    args = ap.parse_args()

    with args.input.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    session = requests.Session()
    label_counts: dict[str, int] = {}
    error_count = 0

    for i, row in enumerate(rows, 1):
        doi = (row.get("DOI") or "").strip()
        if not doi:
            continue
        # Skip resolution if row is fully filled
        if not (_is_empty(row.get("Authors")) or _is_empty(row.get("Abstract")) or _is_empty(row.get("PDF URL"))):
            continue
        resolved, err = resolve_url(doi, session)
        if err:
            error_count += 1
        label = classify(row, resolved)
        if label:
            row["Notes"] = label
            label_counts[label] = label_counts.get(label, 0) + 1
        if i % 20 == 0:
            print(f"  classified {i}/{len(rows)}", file=sys.stderr)
        time.sleep(args.sleep)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {args.output} ({len(rows)} rows)")
    print(f"resolution errors: {error_count}")
    print("label distribution:")
    for lbl, n in sorted(label_counts.items(), key=lambda x: -x[1]):
        print(f"  {n:4d}  {lbl}")
    fully_filled = sum(
        1 for r in rows
        if not (_is_empty(r.get("Authors")) or _is_empty(r.get("Abstract")) or _is_empty(r.get("PDF URL")))
    )
    explained = sum(label_counts.values())
    print(f"summary: {fully_filled} fully filled + {explained} explained = {fully_filled + explained}/{len(rows)} filled-or-explained")
    return 0


if __name__ == "__main__":
    sys.exit(main())
