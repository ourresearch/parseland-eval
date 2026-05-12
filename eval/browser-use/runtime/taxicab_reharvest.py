"""Tier 1.5 — POST the Taxicab harvester to trigger a fresh re-harvest of a DOI.

Endpoint discovered 2026-05-12 (supersedes memory 6918 "read-only" claim):

    POST $TAXICAB_HARVESTER_URL/taxicab
    Content-Type: application/json
    {
      "native_id": "<doi>",
      "native_id_namespace": "doi",
      "url": "https://doi.org/<doi>"
    }

After the POST returns, the refreshed page is readable at:

    GET $TAXICAB_HARVESTER_URL/taxicab/doi/<doi>

This script POSTs the harvester for each input DOI, waits for the refreshed
content to land, and emits a JSONL log of outcomes. Downstream: the orchestrator
re-runs `extract_via_taxicab.py` on the DOIs that successfully refreshed.

Cost: ~$0 per re-harvest (HTTP only). LLM cost is on the subsequent re-extract,
not on this step.

Concurrency default is 4 — conservative until rate-limits are characterized.

Usage:
    python taxicab_reharvest.py --dois 10.1016/x.y.z 10.1007/a.b.c --output reharvest.jsonl
    python taxicab_reharvest.py --input dois.txt --output reharvest.jsonl --concurrency 8
    python taxicab_reharvest.py --dois 10.5840/zfs19354333 --output - --dry-run
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests

DEFAULT_HARVESTER = os.environ.get(
    "TAXICAB_HARVESTER_URL",
    "http://harvester-load-balancer-366186003.us-east-1.elb.amazonaws.com",
)
DEFAULT_TIMEOUT_S = 60
DEFAULT_POLL_INTERVAL_S = 3
DEFAULT_CONCURRENCY = 4

USER_AGENT = "parseland-eval-reharvest/0.1 (mailto:reach2shubhankar@gmail.com)"


def _content_fingerprint(text: str) -> str:
    """Stable hash of response body so we can detect 'refreshed vs unchanged'."""
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()[:16]


def reharvest_one(
    doi: str,
    harvester_url: str,
    timeout_s: int,
    poll_interval_s: int,
    dry_run: bool = False,
) -> dict:
    """POST the harvester for `doi`, then poll until the GET returns refreshed
    content or timeout. Returns a result dict."""
    started = time.time()
    result = {
        "doi": doi,
        "status": "unknown",
        "duration_s": 0.0,
        "http_status_post": None,
        "http_status_get": None,
        "pre_fingerprint": None,
        "post_fingerprint": None,
        "error": None,
    }

    headers = {"User-Agent": USER_AGENT}
    get_url = f"{harvester_url}/taxicab/doi/{doi}"
    post_url = f"{harvester_url}/taxicab"
    post_body = {
        "native_id": doi,
        "native_id_namespace": "doi",
        "url": f"https://doi.org/{doi}",
    }

    if dry_run:
        result["status"] = "dry-run"
        print(f"[dry-run] curl -X POST {post_url} -H 'Content-Type: application/json' -d '{json.dumps(post_body)}'", file=sys.stderr)
        print(f"[dry-run] poll: curl {get_url}", file=sys.stderr)
        return result

    # 0. Capture the PRE fingerprint so we can detect whether re-harvest actually
    # changed the cached content (vs returning the same stale page).
    try:
        pre_resp = requests.get(get_url, headers=headers, timeout=15)
        result["pre_fingerprint"] = _content_fingerprint(pre_resp.text)
    except requests.RequestException:
        result["pre_fingerprint"] = None  # OK — first ever harvest

    # 1. POST the harvester to trigger fresh scrape
    try:
        post_resp = requests.post(
            post_url,
            json=post_body,
            headers={**headers, "Content-Type": "application/json"},
            timeout=30,
        )
        result["http_status_post"] = post_resp.status_code
        if post_resp.status_code >= 500:
            result["status"] = "harvester-5xx"
            result["error"] = f"POST returned {post_resp.status_code}: {post_resp.text[:200]}"
            result["duration_s"] = round(time.time() - started, 2)
            return result
        if post_resp.status_code == 429:
            result["status"] = "rate-limited"
            result["error"] = "POST returned 429 — back off"
            result["duration_s"] = round(time.time() - started, 2)
            return result
    except requests.RequestException as e:
        result["status"] = "post-error"
        result["error"] = str(e)[:200]
        result["duration_s"] = round(time.time() - started, 2)
        return result

    # 2. Poll the GET until content changes OR timeout
    deadline = started + timeout_s
    while time.time() < deadline:
        try:
            get_resp = requests.get(get_url, headers=headers, timeout=15)
            result["http_status_get"] = get_resp.status_code
            if get_resp.status_code == 200:
                fp = _content_fingerprint(get_resp.text)
                result["post_fingerprint"] = fp
                if result["pre_fingerprint"] is None or fp != result["pre_fingerprint"]:
                    result["status"] = "refreshed"
                    result["duration_s"] = round(time.time() - started, 2)
                    return result
                # Content unchanged; harvester may still be processing
        except requests.RequestException as e:
            result["error"] = str(e)[:200]
        time.sleep(poll_interval_s)

    # Timeout — return what we have. If GET returned 200 but fingerprint matches,
    # mark as "completed" (the POST succeeded but the cache didn't change — that's
    # still useful info: the existing cache is "current" per the harvester).
    if result["post_fingerprint"] == result["pre_fingerprint"] and result["pre_fingerprint"] is not None:
        result["status"] = "unchanged"
    else:
        result["status"] = "timeout"
    result["duration_s"] = round(time.time() - started, 2)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--dois", nargs="+", help="DOIs to re-harvest, space-separated")
    src.add_argument("--input", type=Path, help="File with one DOI per line")
    ap.add_argument("--output", default="-", help="JSONL output path; '-' for stdout (default)")
    ap.add_argument("--harvester-url", default=DEFAULT_HARVESTER,
                    help=f"Taxicab harvester base URL (default from $TAXICAB_HARVESTER_URL or {DEFAULT_HARVESTER})")
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                    help=f"Parallel re-harvests (default: {DEFAULT_CONCURRENCY})")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S,
                    help=f"Per-DOI poll timeout in seconds (default: {DEFAULT_TIMEOUT_S})")
    ap.add_argument("--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL_S,
                    help=f"Seconds between polls (default: {DEFAULT_POLL_INTERVAL_S})")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the curl-equivalent commands without executing")
    args = ap.parse_args()

    # Resolve DOI list
    if args.dois:
        dois = list(args.dois)
    else:
        dois = [line.strip() for line in args.input.read_text().splitlines() if line.strip()]

    if not dois:
        print("ERROR: no DOIs provided.", file=sys.stderr)
        return 2

    print(f"re-harvesting {len(dois)} DOIs via {args.harvester_url} (concurrency {args.concurrency}, timeout {args.timeout}s)",
          file=sys.stderr)

    out_stream = sys.stdout if args.output == "-" else open(args.output, "w")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futures = {
            ex.submit(reharvest_one, doi, args.harvester_url, args.timeout, args.poll_interval, args.dry_run): doi
            for doi in dois
        }
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            r = fut.result()
            results.append(r)
            out_stream.write(json.dumps(r) + "\n")
            out_stream.flush()
            print(f"  [{i:3d}/{len(dois)}] {r['doi']:50s} {r['status']:14s} {r['duration_s']:.1f}s",
                  file=sys.stderr)

    # Summary
    from collections import Counter
    summary = Counter(r["status"] for r in results)
    print(f"\nstatus summary:", file=sys.stderr)
    for s, n in summary.most_common():
        print(f"  {s:14s} {n:3d}", file=sys.stderr)

    if args.output != "-":
        out_stream.close()
        print(f"wrote {args.output}", file=sys.stderr)

    # Exit non-zero if EVERY DOI failed (so callers can detect catastrophic failure)
    if all(r["status"] in {"post-error", "harvester-5xx"} for r in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
