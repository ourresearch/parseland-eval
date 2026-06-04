"""iter-R structural labeling of rows with empty fields (lifted from iter_r_classify.py).

Labels: bot-check:<name> | pdf-redirect | paywalled=<publisher> | extraction-miss. Lets the
report attribute an empty field to a structural cause (gold/harvest/bot) vs an honest miss.
The DOI resolver is DOI.org only; the resolved URL is passed in (no network here).
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from ._util import is_empty

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


def classify_row(row: dict, resolved_url: str | None = None) -> str | None:
    """Return an iter-R label, or None if the row is fully populated."""
    if not (is_empty(row.get("Authors")) or is_empty(row.get("Abstract")) or is_empty(row.get("PDF URL"))):
        return None
    if not resolved_url:
        return "iter-R:extraction-miss"
    for pat, name in BOT_CHECK_PATTERNS:
        if pat.search(resolved_url):
            return f"iter-R:bot-check:{name}"
    if urlparse(resolved_url).path.lower().endswith(".pdf"):
        return "iter-R:pdf-redirect"
    for pat, name in PAYWALL_PATTERNS:
        if pat.search(resolved_url):
            return f"iter-R:paywalled={name}"
    return "iter-R:extraction-miss"
