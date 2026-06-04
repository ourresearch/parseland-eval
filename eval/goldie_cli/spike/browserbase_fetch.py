"""Browserbase raw-Fetch vs Taxicab spike (REPORT-ONLY, never promoted to default).

Fetches RAW HTML only (no Browserbase json/structured extraction, no Search/Sessions/
Functions) on the SAME DOI.org-resolved URLs, beside Taxicab, and compares: block/challenge
rate, useful-HTML completeness, JS-dependence, and source agreement. The result is advisory.

Live-doc note (verify before hard-coding): Browserbase Fetch has NO JS execution; formats
raw/markdown/json; official docs conflict on the content cap (5MB vs 1MB); blog pricing
$1/1k pages. We request raw HTML only.

The per-DOI fetch callables are injected, so the comparison logic tests fully offline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

_BLOCK_MARKERS = re.compile(
    r"challenges\.cloudflare\.com|validate\.perfdrive\.com|captcha|are you a robot|"
    r"access denied|just a moment|enable javascript",
    re.IGNORECASE,
)
# Cheap "useful HTML" signal: scholarly meta tags / JSON-LD present.
_USEFUL = re.compile(r"citation_author|citation_title|application/ld\+json|og:title", re.IGNORECASE)


@dataclass(frozen=True)
class FetchOutcome:
    ok: bool          # got non-trivial HTML
    blocked: bool     # block/challenge interstitial detected
    useful: bool      # carries scholarly metadata
    length: int


def assess_html(html: str | None) -> FetchOutcome:
    h = html or ""
    blocked = bool(_BLOCK_MARKERS.search(h))
    ok = len(h) > 2000 and not blocked
    useful = bool(_USEFUL.search(h))
    return FetchOutcome(ok=ok, blocked=blocked, useful=useful, length=len(h))


def compare_one(taxicab_html: str | None, browserbase_html: str | None) -> dict:
    t, b = assess_html(taxicab_html), assess_html(browserbase_html)
    return {
        "taxicab": t.__dict__, "browserbase": b.__dict__,
        "both_useful": t.useful and b.useful,
        "browserbase_only_useful": b.useful and not t.useful,
        "taxicab_only_useful": t.useful and not b.useful,
    }


def summarize(comparisons: list[dict]) -> dict:
    n = len(comparisons) or 1
    tx_block = sum(c["taxicab"]["blocked"] for c in comparisons)
    bb_block = sum(c["browserbase"]["blocked"] for c in comparisons)
    tx_useful = sum(c["taxicab"]["useful"] for c in comparisons)
    bb_useful = sum(c["browserbase"]["useful"] for c in comparisons)
    bb_only = sum(c["browserbase_only_useful"] for c in comparisons)
    return {
        "n": len(comparisons),
        "taxicab_block_rate": round(tx_block / n, 4),
        "browserbase_block_rate": round(bb_block / n, 4),
        "taxicab_useful_rate": round(tx_useful / n, 4),
        "browserbase_useful_rate": round(bb_useful / n, 4),
        "browserbase_only_useful": bb_only,
        "recommendation": ("browserbase materially better"
                           if bb_only >= max(1, len(comparisons) // 10)
                           else "no material improvement over taxicab — keep taxicab"),
    }


def run_spike(
    dois: list[str],
    *,
    taxicab_fetch: Callable[[str], str | None],
    browserbase_fetch: Callable[[str], str | None],
) -> dict:
    """Run the comparison over DOI.org-resolved URLs. Fetchers are injected (creds-gated
    in the CLI). Returns a report-only summary + per-DOI comparisons."""
    comparisons = []
    for doi in dois:
        comparisons.append(compare_one(taxicab_fetch(doi), browserbase_fetch(doi)))
    return {"summary": summarize(comparisons), "per_doi": dict(zip(dois, comparisons))}
