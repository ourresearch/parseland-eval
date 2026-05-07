"""Extract gold-standard metadata from Taxicab-cached HTML.

Strategy (cheapest → most expensive, fall through on miss):

  1. **Citation meta tags** (deterministic, free): parse `citation_title`,
     `citation_author`, `citation_author_institution`, `citation_abstract`,
     `citation_pdf_url`. Most scholarly publishers ship these (Google Scholar
     convention). When complete, no LLM needed.

  2. **Claude API on full HTML** (fallback, ~$0.05–0.20/DOI): pass cleaned HTML
     to Claude Sonnet 4.6 with the v1.4-style extraction prompt + Pydantic
     schema. Used only when citation_* tags are absent or incomplete.

Why this exists: Taxicab pre-harvested HTML for DOIs that browser-use Cloud
gets 403/bot-checked at fetch time (verified 2026-04-29 against APS, Oxford,
ScienceDirect, Érudit, OJS — all 11 holdout-50 fetch-fails are in S3). We
already paid the harvest cost; we should not pay again to re-fetch.

Output schema mirrors v1.4 (matches `human-goldie.csv` / `diff_goldie.py`).
Output CSV slots into the existing comparison pipeline alongside
`runs/holdout-v1.4/ai-goldie-1.csv`.

CLI surface mirrors `extract_batch_cloud.py` for consistency.

Usage:
    python eval/scripts/extract_via_taxicab.py \\
        --source eval/goldie/holdout-50.csv \\
        --output-dir runs/holdout-v1.4-taxicab \\
        --prompt eval/prompts/ai-goldie-v1.4.md \\
        --concurrency 10
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import html as html_lib
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

# Reuse Taxicab client + extraction prompt loader from sibling code.
SCRIPT_DIR = Path(__file__).resolve().parent
EVAL_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(EVAL_DIR))
sys.path.insert(0, str(EVAL_DIR / "scripts"))

from parseland_eval.api import TAXICAB_BASE, resolve_harvest_uuid  # noqa: E402
from extract_batch_cloud import (  # noqa: E402
    ExtractionOut,
    GOLD_COLUMNS,
    load_prompt,
)

log = logging.getLogger("taxicab-extract")

DEFAULT_CONCURRENCY = 10
DEFAULT_MODEL = "claude-sonnet-4-5"  # cost-optimized default; Sonnet 4.6 wire id
DEFAULT_LLM_MAX_TOKENS = 4096
HTML_BUDGET_CHARS = 60_000  # Claude API input cap; head + meta + body excerpt


# ---- Taxicab HTML retrieval ------------------------------------------------

def fetch_html(doi: str) -> tuple[str | None, str | None, str | None]:
    """Resolve DOI -> harvest UUID -> download HTML.

    Returns (html_text, resolved_url, error). HTML is decoded; gzip handled.
    """
    uuid, _call = resolve_harvest_uuid(doi)
    if not uuid:
        return None, None, "taxicab: no harvested html"

    # Direct download. The harvester returns whatever Taxicab cached.
    download_url = f"{TAXICAB_BASE}/taxicab/{uuid}"
    try:
        resp = requests.get(download_url, timeout=30)
    except requests.RequestException as exc:
        return None, None, f"download error: {exc}"
    if resp.status_code != 200:
        return None, None, f"download status: {resp.status_code}"

    # Some records arrive gzipped (s3_path ends .html.gz), some don't.
    body = resp.content
    try:
        text = gzip.decompress(body).decode("utf-8", errors="replace")
    except (OSError, gzip.BadGzipFile):
        text = resp.text

    # Pull resolved_url from a fresh Taxicab call so we record where we got it.
    try:
        meta = requests.get(f"{TAXICAB_BASE}/taxicab/doi/{doi}", timeout=10).json()
        resolved = (meta.get("html") or [{}])[0].get("resolved_url")
    except Exception:
        resolved = None

    return text, resolved, None


# ---- Tier 1: citation_* meta tag extraction --------------------------------

_META_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _meta_re(name: str) -> re.Pattern[str]:
    cached = _META_RE_CACHE.get(name)
    if cached is not None:
        return cached
    # Tolerate name="..." or property="..."; ordering varies; case-insensitive.
    pat = re.compile(
        rf'<meta\b[^>]*?\b(?:name|property)\s*=\s*"{re.escape(name)}"[^>]*?\bcontent\s*=\s*"([^"]*)"',
        re.IGNORECASE,
    )
    _META_RE_CACHE[name] = pat
    return pat


def _all_meta(html: str, name: str) -> list[str]:
    return [html_lib.unescape(m) for m in _meta_re(name).findall(html)]


def _first_meta(html: str, *names: str) -> str:
    """First non-empty value across alternative meta names."""
    for n in names:
        vals = _all_meta(html, n)
        for v in vals:
            v = v.strip()
            if v:
                return v
    return ""


def _fix_encoding(s: str) -> str:
    """Fix common mojibake — page is utf-8 but parser saw latin-1."""
    if not s:
        return s
    if "Ã" in s or "â" in s:
        try:
            return s.encode("latin-1", errors="ignore").decode("utf-8", errors="replace")
        except Exception:
            return s
    return s


_JSONLD_RE = re.compile(
    r'<script\b[^>]*\btype\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)

# Match a JSON string literal value for an `abstract` key. Restricted to
# `abstract` (not `description`) because schema.org's `description` field
# also appears on Organization / Periodical / Person / Publisher nodes, where
# its value is the *publisher's* boilerplate, not the article's abstract.
# Concrete failure mode: the ENCODE Project (10.17989/encsr569oav) ships
# Dataset-typed JSON-LD whose only `description` lives on the Organization
# node ("The ENCODE Data Coordination Center..."). That isn't an abstract.
# `abstract` is more semantically constrained and only appears on
# article-shaped types in practice.
#
# Tolerates whitespace and ignores escaped quotes inside the literal. We use
# regex (not json.loads) because publisher JSON-LD is frequently malformed —
# e.g. T&F ships an outer array with a missing comma between sibling objects,
# which strict parsing rejects. The abstract literal itself is almost always
# well-formed even when the surrounding structure isn't.
_JSONLD_ABSTRACT_KEY_RE = re.compile(
    r'"abstract"\s*:\s*"((?:\\.|[^"\\])*)"',
    re.IGNORECASE,
)


def _decode_json_string(raw: str) -> str:
    """Decode the contents of a JSON string literal — handles \\n, \\", \\\\, \\u escapes."""
    try:
        return json.loads('"' + raw + '"')
    except (json.JSONDecodeError, ValueError):
        # Fallback: only undo the most common escapes.
        return (raw.replace('\\"', '"')
                   .replace('\\\\', '\\')
                   .replace('\\n', '\n')
                   .replace('\\t', '\t'))


def _jsonld_abstract(html: str) -> str:
    """Pull the longest ``abstract`` literal out of any JSON-LD block on the
    page. ``description`` is intentionally ignored — see comment on
    ``_JSONLD_ABSTRACT_KEY_RE`` for the ENCODE-Project failure mode.

    Why this exists: ``_strip_for_llm`` removes script tags before sending HTML
    to the LLM, which hides JSON-LD's ``abstract`` field. Restoring JSON-LD to
    the LLM-visible HTML caused cross-field regressions on authors / pdf_url
    (the LLM started preferring JSON-LD's structured data over citation_*
    meta tags it had been correctly using). This function is the surgical
    alternative — backfill ONLY the abstract field, and only post-LLM, so
    other fields are untouched.

    Returns "" if no JSON-LD abstract is present.
    """
    best = ""
    for raw in _JSONLD_RE.findall(html):
        for m in _JSONLD_ABSTRACT_KEY_RE.finditer(raw):
            v = _decode_json_string(m.group(1))
            if v and len(v) > len(best):
                best = v
    return _fix_encoding(html_lib.unescape(best.strip()))


_TRUNCATED_TAIL_RE = re.compile(r"(\.{3}|…)\s*$")


def _looks_truncated(text: str) -> bool:
    """LLM abstract looks like a meta-tag truncation: short and either ends
    with an explicit ellipsis or ends mid-word (no sentence terminator)."""
    if not text:
        return True
    s = text.strip()
    if len(s) >= 400:
        return False
    if _TRUNCATED_TAIL_RE.search(s):
        return True
    # Ends mid-sentence: not on . ? ! and length under ~400 chars.
    last = s[-1]
    if last not in ".?!。":
        return True
    return False


def _is_title_as_abstract(llm_abstract: str, html: str) -> bool:
    """The LLM dumped the article title into the abstract field. Detected when
    the LLM's "abstract" is short (≤200 chars) and string-equal to either the
    ``citation_title`` meta tag or the article's ``<title>`` (modulo
    whitespace).

    This pattern shows up on Open-Journal-Systems pages whose abstract block
    is just a literal repeat of the title (e.g. Diálogos de Saberes — the
    page renders ``<h2>Resumen</h2><p>{TITLE}</p>``) — gold correctly logs
    those as no-abstract; we shouldn't credit the LLM for surfacing the
    title under an abstract heading."""
    a = (llm_abstract or "").strip()
    if not a or len(a) > 200:
        return False
    title = _first_meta(html, "citation_title", "DC.title", "og:title").strip()
    if title and a.lower() == title.lower():
        return True
    return False


_LATIN_ABSTRACT_LABEL_RE = re.compile(
    r"<b>\s*Abstract\s*:?\s*</b>\s*(?:<[^>]+>\s*)?([A-Z][^<]{120,5000})",
    re.IGNORECASE,
)


def _is_mostly_non_latin(text: str) -> bool:
    """True when most characters in ``text`` lie outside the Latin/Latin-1
    block — i.e. the LLM extracted a Cyrillic / CJK / Devanagari abstract
    while gold convention prefers the English version when both are present
    on the same page."""
    if not text:
        return False
    sample = text[:400]
    non_latin = sum(1 for ch in sample if ch.isalpha() and ord(ch) > 0x024F)
    letters = sum(1 for ch in sample if ch.isalpha())
    return letters > 0 and non_latin / letters >= 0.5


def _latin_abstract_from_label(html: str) -> str:
    """When the page contains an explicit ``<b>Abstract:</b>`` label followed
    by a Latin paragraph, return that paragraph. nbpublish.com (Russian
    serviceology journal) renders both languages in this layout: a Cyrillic
    annotation block first, then ``<b>Abstract:</b> <english text>``.
    Returns "" if no such block is present.
    """
    m = _LATIN_ABSTRACT_LABEL_RE.search(html)
    if not m:
        return ""
    candidate = html_lib.unescape(m.group(1)).strip()
    return _fix_encoding(candidate)


# ---- post-LLM corresponding-author backfill --------------------------------

# Page-level CA evidence — used as a guard against the "drop all CA" rule.
# If the page does have an explicit marker, the all-CA flagging may be real.
_PAGE_CA_MARKER_RES = (
    re.compile(r'class\s*=\s*"[^"]*\bcorresp(?:onding)?\b[^"]*"', re.IGNORECASE),
    re.compile(r'fa-envelope', re.IGNORECASE),
    re.compile(r'\bcorrespond(?:ing|ence)\b', re.IGNORECASE),
    re.compile(r'<sup>\s*\*\s*</sup>', re.IGNORECASE),
    re.compile(r'Correspondence\s+to', re.IGNORECASE),
    # Non-English markers per v1.8 prompt (see ai-goldie-v1.8.md).
    re.compile(r'Penulis korespondensi|Корреспондирующий|Yazışma adresi', re.IGNORECASE),
)


def _page_has_ca_marker(html: str) -> bool:
    return any(p.search(html) for p in _PAGE_CA_MARKER_RES)


# Anchor: a class attribute that explicitly flags an HTML element as the
# corresponding-author block. T&F's pattern:
#   <span class="contribDegrees corresponding "> ... <a>Matthew Leggatt</a> ...
_CORRESP_OPEN_TAG_RE = re.compile(
    r'class\s*=\s*"[^"]*\bcorresp(?:onding)?\b[^"]*"[^>]*>',
    re.IGNORECASE,
)
_AUTHOR_NAME_INSIDE_RE = re.compile(
    # Match the leading text node of an author anchor; tolerate sibling
    # inline tags (e.g. T&F renders `<a>Name<i class="fa-envelope"></i></a>`,
    # which `([^<]+)</a>` rejects because the </a> isn't adjacent).
    r'<a\b[^>]*class\s*=\s*"[^"]*\bauthor\b[^"]*"[^>]*>([^<]+?)(?:<[^>]+>)*\s*</a>',
    re.IGNORECASE,
)


def _ca_names_from_class_marker(html: str) -> set[str]:
    """Author names that appear inside an HTML element whose class explicitly
    flags it as the corresponding-author block (e.g. T&F's
    ``<span class="contribDegrees corresponding">``). Returns a set of
    lowercase, whitespace-collapsed names so callers can match against AI
    extraction names regardless of capitalization / whitespace artifacts.

    Implementation: anchor on the corresp class open-tag, then scan the next
    ~600 chars for an ``<a class="author">…</a>`` element. We stay
    conservative on window size to avoid grabbing names from later author
    sections; the corresponding block is typically just one author wrapper.
    """
    out: set[str] = set()
    for m in _CORRESP_OPEN_TAG_RE.finditer(html):
        window = html[m.end():m.end() + 600]
        for nm in _AUTHOR_NAME_INSIDE_RE.findall(window):
            cleaned = " ".join(html_lib.unescape(nm).split()).strip().lower()
            if cleaned:
                out.add(cleaned)
    return out


def _maybe_drop_all_ca(authors: list[dict], html: str) -> bool:
    """If the LLM flagged *every* author as corresponding AND the page has
    no explicit CA marker (asterisk / class="corresp" / envelope / explicit
    text), drop all flags. The "all-CA" pattern reliably indicates the LLM
    is using per-author ``mailto:``/``citation_author_email`` presence as
    proxy evidence — gold convention rejects that proxy when no explicit
    marker is on the page (see DOI 10.7256/2454-0730.2019.1.20595)."""
    if len(authors) < 2:
        return False
    flagged = [bool(a.get("corresponding_author")) for a in authors]
    if not all(flagged):
        return False
    if _page_has_ca_marker(html):
        return False
    for a in authors:
        a["corresponding_author"] = False
    return True


def _maybe_backfill_ca_from_class(authors: list[dict], html: str) -> bool:
    """When the LLM did not flag any author as corresponding AND the page
    has a ``class*="corresp"`` block wrapping a specific author name, mark
    that author. Worked example: T&F's Daughters paper
    (``10.1080/01956051.2025.2517586``) wraps Matthew Leggatt's section in
    ``<span class="contribDegrees corresponding ">``. Without this rule the
    LLM has to learn that pattern through prompt rules, which leaks (see
    feedback_prompt_rules_leak)."""
    if not authors:
        return False
    if any(a.get("corresponding_author") for a in authors):
        return False  # LLM already chose; don't override
    target_names = _ca_names_from_class_marker(html)
    if not target_names:
        return False
    changed = False
    for a in authors:
        nm = " ".join(str(a.get("name") or "").split()).strip().lower()
        if nm and any(nm == t or nm in t or t in nm for t in target_names):
            a["corresponding_author"] = True
            changed = True
    return changed


# Old-Elsevier OUP-redirect CA backfill (added 2026-05-07).
# Targets DOIs `10.1016/...` (1990s) that redirect to an Oxford University
# Press wrapper. The wrapper uses `<div class="info-author-correspondence">`
# rather than `class*="corresp"` (word-boundary won't match), so the existing
# class-based CA backfill misses these. Pattern:
#   <div class="info-author-correspondence">
#     <div content-id="corN">...
#       <a href="mailto:A.P.vanDam@amc.uva.nl">...</a>
#     </div>
#   </div>
# We extract the email's local-part, derive a last-name candidate (the
# longest alphabetic run of length ≥ 3 ending the local part), and mark the
# matching author. Worked example: 10.1016/s0378-1097(99)00346-8 — local-part
# `A.P.vanDam` → "vandam" → matches "Alje P. van Dam".
_OUP_CA_EMAIL_RE = re.compile(
    r'<div[^>]*class\s*=\s*"[^"]*info-author-correspondence[^"]*"[^>]*>'
    r'.{0,2000}?'  # tolerate nested <div class="fax">…</div> + label spans
    r'<a[^>]*href\s*=\s*"mailto:([^"@]+)@[^"]+"',
    re.IGNORECASE | re.DOTALL,
)

# Generic "Correspondence to <NAME>" / "Reprint requests to <NAME>" pattern
# (added 2026-05-07). Catches AHA Journals (Stroke 10.1161/01.str.32.6.1291)
# where the page renders `<div class*="corresp">Correspondence to Dr P.M.
# White, Department of...` — no mailto in the cache (Cloudflare obfuscates),
# but the explicit name-after-label resolves which author is CA.
_GENERIC_CA_LABEL_NAME_RE = re.compile(
    r'(?:Correspondence\s+(?:to|and\s+reprint\s+requests\s+to)|'
    r'Reprint\s+requests\s+to|Address\s+(?:correspondence|reprint\s+requests)\s+to)'
    r'\s+(?:Dr\.?\s+|Prof\.?\s+|Professor\s+)?'
    r'([A-Z][A-Za-z\.\-]*\s+(?:[A-Za-z\.\-]+\s+)*[A-Z][A-Za-z\-]+)'
    r'(?=[,\.\s<])',
    re.IGNORECASE,
)


def _extract_ca_name_candidate(html: str) -> str:
    """Return the author-name candidate that appears immediately after a
    correspondence label (e.g., 'Correspondence to Dr P.M. White, Dept...').
    Returns '' if no qualifying label is found."""
    m = _GENERIC_CA_LABEL_NAME_RE.search(html)
    return m.group(1).strip() if m else ""


# Generic "author for correspondence" / "corresponding author" + mailto pattern
# (added 2026-05-07). Catches Russian Perm State (`<strong>Author for
# correspondence.</strong>...mailto:aluchnikov@yandex.ru`) and similar.
# Window allows inline tags between the label and the mailto.
_GENERIC_CA_LABEL_EMAIL_RE = re.compile(
    r'(?:Author\s+for\s+correspondence|Corresponding\s+author|Correspondence\s+to)'
    r'.{0,500}?'
    r'<a[^>]*href\s*=\s*"mailto:([^"@]+)@[^"]+"',
    re.IGNORECASE | re.DOTALL,
)


def _last_name_from_email_localpart(local: str) -> str:
    """Extract a last-name candidate from an email local-part. Strategy: take
    the trailing alphabetic run of length ≥ 3 (e.g., 'A.P.vanDam' → 'Dam',
    'jsmith' → 'jsmith', 'j.smith' → 'smith'), lowercased. Returns '' if no
    qualifying run exists."""
    if not local:
        return ""
    m = re.search(r'[A-Za-z]{3,}$', local)
    return m.group(0).lower() if m else ""


# NMJI (Indian medical) abstract backfill (added 2026-05-07).
# NMJI ships no `citation_abstract` and only the title in og:description.
# Body text is concatenated into a single `<p id="-1">` inside `<main><div
# class="body">`. Worked example: 10.25259/nmji_377_2024 — single-p length
# 1740 chars matches gold (1645) at Levenshtein 0.88.
_NMJI_BODY_RE = re.compile(
    r'<main>\s*<div\s+class="body"[^>]*>\s*<p[^>]*\bid="-1"[^>]*>(.+?)</p>',
    re.IGNORECASE | re.DOTALL,
)


def _is_nmji_page(html: str, doi: str) -> bool:
    if (doi or "").lower().startswith("10.25259/nmji"):
        return True
    return 'nmji.in' in (html or "").lower()


def _maybe_backfill_abstract_from_nmji(extraction: dict, html: str, doi: str) -> bool:
    """Per-publisher NMJI abstract backfill. Fires only when the page is
    NMJI AND the LLM-extracted abstract is empty/short. Pulls the single
    body paragraph at <main><div class='body'><p id='-1'>."""
    if not _is_nmji_page(html, doi):
        return False
    cur = (extraction.get("Abstract") or "").strip()
    if cur and len(cur) >= 200:
        return False
    m = _NMJI_BODY_RE.search(html)
    if not m:
        return False
    text = re.sub(r'<[^>]+>', '', m.group(1))
    text = " ".join(html_lib.unescape(text).split())
    text = _fix_encoding(text)
    if len(text) < 400:
        return False
    extraction["Abstract"] = text
    return True


# Relative PDF URL backfill (added 2026-05-07). Catches publishers that
# render the PDF link as a relative path "/doi/pdf/<DOI>?download=true"
# without the host. The LLM tends to emit empty when it sees relative
# paths because the prompt asks for full URLs. Worked example: Stroke
# 10.1161/01.str.32.6.1291 — link is /doi/pdf/10.1161/01.STR.32.6.1291.
_RELATIVE_PDF_URL_RE = re.compile(
    r'href\s*=\s*"(/doi/(?:pdf|epdf)/[^"]+)"',
    re.IGNORECASE,
)


# Whitelist of publisher hosts where gold convention IS to record the
# /doi/pdf/<DOI> URL (not N/A). Fire the relative-PDF backfill only on
# these. Worked-example backed: ahajournals.org (Stroke 10.1161). Add new
# hosts only after confirming gold has the URL (not N/A) for that publisher.
_RELATIVE_PDF_HOST_WHITELIST = (
    "www.ahajournals.org", "ahajournals.org",
)


def _maybe_backfill_pdf_url_from_relative(
    extraction: dict, html: str, doi: str
) -> bool:
    """If the LLM left PDF URL empty AND the page has a relative
    /doi/pdf/<DOI> link AND the publisher host is on a whitelist where
    gold convention records the URL (not N/A), backfill with the absolute
    URL. Whitelisted hosts: ahajournals.org. Other publishers (e.g., T&F)
    leave gold as N/A; patching them would regress 'both empty = match'."""
    if (extraction.get("PDF URL") or "").strip():
        return False
    if not html or not doi:
        return False
    m = _RELATIVE_PDF_URL_RE.search(html)
    if not m:
        return False
    rel = m.group(1)
    # Sniff the publisher host: find any absolute URL on the page whose
    # path contains the DOI's tail (e.g., "01.str.32.6.1291"). The host
    # of that URL is then the canonical publisher.
    doi_tail = doi.split("/", 1)[-1] if "/" in doi else doi
    host = ""
    # Look for https?://HOST/...DOI_tail... patterns; prefer the most common
    # host across hits (avoid stray third-party links).
    host_counts: dict[str, int] = {}
    for hm in re.finditer(
        r'https?://([A-Za-z0-9.-]+)/[^"\s<>]*' + re.escape(doi_tail.lower()),
        html.lower()
    ):
        h = hm.group(1)
        # Skip generic doi-resolver hosts; we want the canonical publisher.
        if h.endswith('doi.org') or h == 'doi.org':
            continue
        host_counts[h] = host_counts.get(h, 0) + 1
    if host_counts:
        host = max(host_counts, key=lambda h: host_counts[h])
    if not host or host not in _RELATIVE_PDF_HOST_WHITELIST:
        return False
    try:
        from urllib.parse import urlsplit, urlunsplit
        rs = urlsplit(rel)
        absolute = urlunsplit(("https", host, rs.path, rs.query, ""))
    except Exception:
        return False
    extraction["PDF URL"] = absolute
    return True


# Emerald book-chapter abstract backfill (added 2026-05-07).
# Emerald's chapter pages put the abstract inside
# <div class="category-section content-section js-content-section"...>
# with NO 'Abstract' heading. The LLM emits empty because it can't tell that
# unlabeled paragraph is the abstract. Worked example: 10.1108/978-1-64802-
# 637-920251008 — text starts "Picture this: 30 educators...".
_EMERALD_ABSTRACT_RE = re.compile(
    r'<div[^>]*class\s*=\s*"[^"]*category-section\s+content-section[^"]*"[^>]*>'
    r'\s*<p[^>]*>(.+?)</p>',
    re.IGNORECASE | re.DOTALL,
)


def _is_emerald_page(html: str, doi: str) -> bool:
    if (doi or "").lower().startswith("10.1108/"):
        return True
    return 'emerald.com/' in (html or "").lower()


def _maybe_backfill_abstract_from_emerald(extraction: dict, html: str, doi: str) -> bool:
    """Per-publisher Emerald abstract backfill. Fires only when the page is
    Emerald AND the LLM-extracted abstract is empty/short. Pulls the first
    <p> inside <div class="category-section content-section">."""
    if not _is_emerald_page(html, doi):
        return False
    cur = (extraction.get("Abstract") or "").strip()
    if cur and len(cur) >= 200:
        return False
    m = _EMERALD_ABSTRACT_RE.search(html)
    if not m:
        return False
    text = re.sub(r'<[^>]+>', '', m.group(1))  # strip nested tags
    text = " ".join(html_lib.unescape(text).split())
    text = _fix_encoding(text)
    if len(text) < 200:
        return False
    extraction["Abstract"] = text
    return True


def _maybe_backfill_ca_from_oup_email(authors: list[dict], html: str) -> bool:
    """Set ``corresponding_author=True`` on the author whose name matches an
    email's local-part trailing alpha run, when the page has either:
      - the OUP-redirect ``info-author-correspondence`` block + mailto, OR
      - the generic "Author for correspondence" / "Corresponding author"
        label + mailto pattern (worked example: Russian Perm State
        10.31857/s2587556623070105 — "Author for correspondence... mailto:
        aluchnikov@..." → matches A. S. Luchnikov).

    Conservative: only fires when no author is currently flagged CA."""
    if not authors or any(a.get("corresponding_author") for a in authors):
        return False
    # Try email-based pattern first (specific). Then name-after-label.
    m = _OUP_CA_EMAIL_RE.search(html) or _GENERIC_CA_LABEL_EMAIL_RE.search(html)
    if m:
        candidate = _last_name_from_email_localpart(m.group(1))
    else:
        # Name-after-label fallback: extract last token of the matched name.
        name_candidate = _extract_ca_name_candidate(html)
        if not name_candidate:
            return False
        # Last token (drop trailing punctuation) as the surname signal.
        last_tok = re.sub(r'[^A-Za-z]', '', name_candidate.split()[-1]).lower()
        candidate = last_tok if len(last_tok) >= 3 else ""
    if not candidate:
        return False
    # Match by ANY token of the candidate appearing in the author's name
    # (lowercased). Catches "A.P.vanDam" → "vandam" → matches "van Dam"
    # because "vandam" appears in "van dam"-after-whitespace-strip.
    # Also catches "aluchnikov" → matches "A. S. Luchnikov" (drop the leading
    # initial via substring match: "luchnikov" appears in "a s luchnikov").
    for a in authors:
        nm = " ".join(str(a.get("name") or "").split()).lower()
        nm_compact = nm.replace(" ", "").replace(".", "")
        if candidate in nm_compact:
            a["corresponding_author"] = True
            return True
        # Also try shorter suffix (drop leading initial-letter from email)
        if len(candidate) > 4 and candidate[1:] in nm_compact:
            a["corresponding_author"] = True
            return True
    return False


# T&F's visible-HTML author pattern:
#   <a class="author">{NAME}<i class="fa-envelope"></i></a>
#   <span class="overlay">{AFFILIATION_TEXT}<a class="author-extra-info">View further...</a></span>
# T&F does NOT ship `citation_author_institution` meta tags, so the existing
# `extract_via_meta_tags` Highwire path can't help. This regex pulls the
# affiliation off the visible markup so the rases backfill below can fill
# any author whose LLM-extracted rases is empty.
_TF_AUTHOR_OVERLAY_RE = re.compile(
    r'<a\b[^>]*class\s*=\s*"[^"]*\bauthor\b[^"]*"[^>]*>([^<]+?)(?:<[^>]+>)*\s*</a>'
    r'\s*<span\s+class\s*=\s*"[^"]*\boverlay\b[^"]*"[^>]*>([^<]+?)(?=<|$)',
    re.IGNORECASE | re.DOTALL,
)


def _affiliation_from_overlay(html: str) -> dict[str, str]:
    """Map ``{author_name_lowercased: affiliation_text}`` extracted from T&F's
    visible-HTML overlay structure. Returns empty dict if the page doesn't use
    that pattern."""
    out: dict[str, str] = {}
    for m in _TF_AUTHOR_OVERLAY_RE.finditer(html):
        name = " ".join(html_lib.unescape(m.group(1)).split()).strip()
        aff = " ".join(html_lib.unescape(m.group(2)).split()).strip()
        if name and aff:
            out[name.lower()] = _fix_encoding(aff)
    return out


def _maybe_backfill_rases_from_overlay(authors: list[dict], html: str) -> bool:
    """Fill empty per-author rases from T&F-style ``<span class="overlay">``
    affiliation blocks (see ``_affiliation_from_overlay``)."""
    if not authors:
        return False
    aff_by_name = _affiliation_from_overlay(html)
    if not aff_by_name:
        return False
    changed = False
    for a in authors:
        if (a.get("rasses") or "").strip():
            continue
        nm = " ".join(str(a.get("name") or "").split()).strip().lower()
        if nm in aff_by_name:
            a["rasses"] = aff_by_name[nm]
            changed = True
    return changed


# Elsevier ScienceDirect React SPA: author + affiliation data is embedded as a
# JSON blob inside ``<script type="application/json" data-iso-key="_0">``. The
# script tag is stripped by ``_strip_for_llm`` (correctly — the LLM-input
# leakage rule still applies), so the LLM never sees authors or affiliations
# on these pages and emits empty ``rasses`` for every author. This deterministic
# extractor walks the JSON's ``authors.content[*]`` author-group structure,
# follows ``cross-ref/refid`` from each author to the matching ``affiliation/id``,
# and returns ``{author_name_lower: address_text}``.
#
# Why per-publisher: the JSON shape is Elsevier's; nothing else uses it. This
# is the canonical "publisher-specific extractor" path approved under the
# `feedback_push_to_95_money_no_object` directive.
_ELSEVIER_ISO_RE = re.compile(
    r'<script[^>]*type\s*=\s*"application/json"[^>]*data-iso-key\s*=\s*"_0"[^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_ELSEVIER_FOOTNOTE_NAMES = frozenset({"footnote", "cross-ref"})


def _elsevier_iso_collect_text(node) -> str:
    """Recursively gather visible text from an Elsevier sd-iso JSON node,
    skipping footnote / cross-ref subtrees (which carry email addresses,
    not the affiliation address)."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if node.get("#name") in _ELSEVIER_FOOTNOTE_NAMES:
            return ""
        out: list[str] = []
        v = node.get("_")
        if isinstance(v, str):
            out.append(v)
        for child in node.get("$$", []) or []:
            t = _elsevier_iso_collect_text(child)
            if t:
                out.append(t)
        return " ".join(out)
    if isinstance(node, list):
        return " ".join(_elsevier_iso_collect_text(x) for x in node)
    return ""


def _affiliation_from_elsevier_iso(html: str) -> dict[str, str]:
    """Map ``{author_name_lower: affiliation_text}`` extracted from Elsevier
    ScienceDirect's React SPA JSON. Returns empty dict if the page doesn't
    use that pattern."""
    m = _ELSEVIER_ISO_RE.search(html)
    if not m:
        return {}
    raw = m.group(1)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    authors_content = (data.get("authors") or {}).get("content") or []

    aff_by_id: dict[str, str] = {}
    authors: list[dict[str, list[str]]] = []
    for group in authors_content:
        if not isinstance(group, dict) or group.get("#name") != "author-group":
            continue
        for child in group.get("$$", []) or []:
            nm = child.get("#name")
            if nm == "author":
                given = surname = ""
                refids: list[str] = []
                for sub in child.get("$$", []) or []:
                    n2 = sub.get("#name")
                    if n2 == "given-name":
                        given = sub.get("_", "") or ""
                    elif n2 == "surname":
                        surname = sub.get("_", "") or ""
                    elif n2 == "cross-ref":
                        rid = (sub.get("$") or {}).get("refid") or ""
                        # Affiliation refids start with "A"/"AF"; "F"-prefixed
                        # refids point at email footnotes (skip).
                        if rid and (rid.startswith("AF") or
                                    (rid.startswith("A") and not rid.startswith("AC"))):
                            if not rid.startswith(("F", "f")):
                                refids.append(rid)
                full_name = f"{given} {surname}".strip()
                if full_name:
                    authors.append({"name": full_name, "refids": refids})
            elif nm == "affiliation":
                aid = (child.get("$") or {}).get("id") or ""
                if not aid:
                    continue
                for sub in child.get("$$", []) or []:
                    if sub.get("#name") == "textfn":
                        text = " ".join(_elsevier_iso_collect_text(sub).split())
                        if text:
                            aff_by_id[aid] = text
                        break

    out: dict[str, str] = {}
    for a in authors:
        affs = [aff_by_id[r] for r in a["refids"] if r in aff_by_id]
        if affs:
            # Multiple affiliations joined with "; " matches gold convention.
            out[a["name"].lower()] = "; ".join(affs)
    return out


def _maybe_backfill_rases_from_elsevier_iso(authors: list[dict], html: str) -> bool:
    """Fill empty per-author rases from Elsevier ScienceDirect's
    `data-iso-key="_0"` React JSON. Worked example: DOI 10.1006/cviu.2002.0969 —
    the cached HTML's JSON block contains all 3 authors with their full
    University-of-Amsterdam affiliations, but the LLM gets an empty view
    after `_strip_for_llm` removes the script."""
    if not authors:
        return False
    aff_by_name = _affiliation_from_elsevier_iso(html)
    if not aff_by_name:
        return False
    changed = False
    for a in authors:
        if (a.get("rasses") or "").strip():
            continue
        nm = " ".join(str(a.get("name") or "").split()).strip().lower()
        if nm in aff_by_name:
            a["rasses"] = aff_by_name[nm]
            changed = True
    return changed


# JSON-LD ScholarlyArticle author-affiliation backfill (added 2026-05-07).
# Targets MDPI Patterns 18+19 (10.3390/polym13183031, 10.3390/su13041644) where
# Claude misses per-author affiliations because the page uses <sup>-digit
# footnote markers attached to author names. The full institution strings are
# in the JSON-LD ScholarlyArticle block as `author[].affiliation.name`.
#
# Why post-LLM and not LLM-input: per `feedback_prompt_rules_leak.md`, restoring
# JSON-LD content to the LLM's HTML input causes cross-field regressions (the
# LLM starts preferring JSON-LD's structured data over the citation_* meta tags
# it had been correctly using). This is the surgical alternative — backfill
# only empty per-author rasses fields, post-LLM, so other fields are untouched.


def _affiliation_from_jsonld(html: str) -> dict[str, str]:
    """Map ``{author_name_lower: affiliation_text}`` extracted from any
    JSON-LD ScholarlyArticle block on the page. Returns empty dict if no
    such block has nested author affiliations.

    Tolerant parser: tries strict json.loads first, falls back to a name-then-
    affiliation regex pair scan. JSON-LD on publisher pages is often
    syntactically malformed (missing commas between sibling array items, etc.);
    we pull out (name, affiliation_name) pairs structurally where possible.
    """
    out: dict[str, str] = {}
    for raw in _JSONLD_RE.findall(html):
        # First, try strict parse.
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            data = None
        if data is not None:
            for node in _walk_jsonld_nodes(data):
                authors_field = node.get("author") if isinstance(node, dict) else None
                if not authors_field:
                    continue
                if isinstance(authors_field, dict):
                    authors_field = [authors_field]
                if not isinstance(authors_field, list):
                    continue
                for a in authors_field:
                    if not isinstance(a, dict):
                        continue
                    name = (a.get("name") or "").strip()
                    if not name:
                        given = (a.get("givenName") or "").strip()
                        family = (a.get("familyName") or "").strip()
                        name = f"{given} {family}".strip()
                    if not name:
                        continue
                    aff_text = _jsonld_aff_text(a.get("affiliation"))
                    if aff_text:
                        key = " ".join(name.split()).lower()
                        # Also index by "Last, First" → "First Last" form
                        # (Springer JSON-LD: "Thiel, Christian" → "Christian Thiel")
                        if "," in key and not key.startswith("dr "):
                            parts = [p.strip() for p in key.split(",", 1)]
                            if len(parts) == 2 and all(parts):
                                alt = f"{parts[1]} {parts[0]}"
                                if alt not in out or len(aff_text) > len(out[alt]):
                                    out[alt] = aff_text
                        if key not in out or len(aff_text) > len(out[key]):
                            out[key] = aff_text
    return out


def _jsonld_aff_text(aff) -> str:
    """Extract a single affiliation string from a JSON-LD ``affiliation`` value.
    Handles strings, dicts (``name`` first, ``address.name`` fallback for
    Springer book chapters), and lists of either. Falls back to nested
    ``address`` when ``name`` is empty (worked example: 10.1007/978-94-017-
    2981-9_4 has affiliation.name='' with address.name='Nuremberg, Germany')."""
    if isinstance(aff, str):
        return aff.strip()
    if isinstance(aff, dict):
        v = (aff.get("name") or "").strip()
        if not v:
            address = aff.get("address")
            if isinstance(address, dict):
                v = (address.get("name") or "").strip()
            elif isinstance(address, str):
                v = address.strip()
        return v
    if isinstance(aff, list):
        parts = [p for p in (_jsonld_aff_text(item) for item in aff) if p]
        return "; ".join(parts)
    return ""


def _walk_jsonld_nodes(data):
    """Yield every dict node in a parsed JSON-LD tree (handles top-level array,
    @graph nesting, single object). JSON-LD pages commonly wrap the article in
    ``{"@graph": [...]}`` rather than putting it at the root."""
    if isinstance(data, list):
        for item in data:
            yield from _walk_jsonld_nodes(item)
    elif isinstance(data, dict):
        yield data
        graph = data.get("@graph")
        if graph:
            yield from _walk_jsonld_nodes(graph)


def _maybe_backfill_rases_from_jsonld(authors: list[dict], html: str) -> bool:
    """Fill empty per-author rases from any JSON-LD ScholarlyArticle block on
    the page that nests ``author[].affiliation.name``. Conservative: only
    fills when the LLM left rasses empty AND the JSON-LD has a name match.
    Targets publishers that DO ship JSON-LD (Frontiers, PLOS, some OUP).
    MDPI does NOT ship JSON-LD — see _maybe_backfill_rases_and_ca_from_mdpi.
    """
    if not authors:
        return False
    aff_by_name = _affiliation_from_jsonld(html)
    if not aff_by_name:
        return False
    changed = False
    for a in authors:
        if (a.get("rasses") or "").strip():
            continue
        nm = " ".join(str(a.get("name") or "").split()).strip().lower()
        if nm in aff_by_name:
            a["rasses"] = aff_by_name[nm]
            changed = True
    return changed


# MDPI per-publisher extractor (added 2026-05-07).
# Targets DOIs `10.3390/...` and pages with `<div class="art-authors">` +
# `<div class="art-affiliations">` structure. Gold convention here is the
# *literal sup-marker content* (e.g., "1,†", "3,*"), NOT the resolved
# institution name — see human-goldie.csv row 16. CA flag = "*" present in
# the marker. Worked examples: 10.3390/polym13183031, 10.3390/su13041644.
_MDPI_AUTHOR_SUP_RE = re.compile(
    r'<span class="sciprofiles-link__name">([^<]+)</span>'
    r'(?:[^<]|<(?!sup\b))*?<sup>([^<]+)</sup>',
    re.IGNORECASE | re.DOTALL,
)


def _is_mdpi_page(html: str, doi: str) -> bool:
    """True when the cached HTML is from MDPI."""
    if (doi or "").lower().startswith("10.3390/"):
        return True
    return ('mdpi.com/' in (html or "").lower() or
            'class="art-affiliations"' in (html or "").lower())


def _maybe_backfill_rases_and_ca_from_mdpi(
    authors: list[dict], html: str, doi: str = ""
) -> bool:
    """Per-publisher MDPI rasses + CA flag backfill. Extracts the sup-marker
    content for each author (e.g., "1,†" / "3,*") in document order and
    assigns positionally to ``authors`` (MDPI emits sciprofiles-link blocks
    in author order). Emits the literal sup-marker as the rasses value —
    matching the human-goldie convention for MDPI rows. Sets
    corresponding_author=True when "*" is present in the marker.

    Positional matching is used because publisher caches sometimes have
    corrupted Turkish / Vietnamese / Korean bytes in the visible HTML
    (worked example: 10.3390/su13041644 caches "OÄuz YÄ±ldÄ±z" with a
    missing UTF-8 continuation byte that name-matching can't recover from).
    The author *count* and *order* are reliable; only the *bytes* are not.

    Conservative: only fires on MDPI pages (DOI prefix `10.3390/` OR HTML
    fingerprint match) AND only fills empty per-author rasses (preserves any
    existing LLM extraction). CA flag is only *added*, never removed.
    """
    if not authors or not _is_mdpi_page(html, doi):
        return False
    sup_markers: list[str] = []
    for m in _MDPI_AUTHOR_SUP_RE.finditer(html):
        sup_raw = _fix_encoding(html_lib.unescape(m.group(2))).strip()
        sup_clean = " ".join(sup_raw.split())
        sup_markers.append(sup_clean)
    # Be safe on count mismatch — only assign positionally when counts agree.
    if len(sup_markers) != len(authors):
        return False
    changed = False
    for a, sup_clean in zip(authors, sup_markers):
        if not (a.get("rasses") or "").strip():
            a["rasses"] = sup_clean
            changed = True
        if "*" in sup_clean and not a.get("corresponding_author"):
            a["corresponding_author"] = True
            changed = True
    return changed


def extract_via_meta_tags(html: str, doi: str, link: str) -> dict[str, Any] | None:
    """Try to extract everything from citation_* meta tags. Returns None if not enough."""
    title = _fix_encoding(_first_meta(html, "citation_title", "DC.title", "og:title"))
    if not title:
        return None  # No Highwire tags — fall through.

    authors_raw = [_fix_encoding(a) for a in _all_meta(html, "citation_author")]
    affs_raw = [_fix_encoding(a) for a in _all_meta(html, "citation_author_institution")]
    abstract = _fix_encoding(_first_meta(
        html, "citation_abstract", "dc.description", "DC.description", "og:description"
    ))
    pdf_url = _first_meta(html, "citation_pdf_url", "citation_full_html_url")

    if not authors_raw:
        return None  # Highwire pages typically have authors; if missing, don't trust the tag set.

    # Pair authors and affiliations by index. citation_author_institution can repeat
    # per-affiliation (one author with two affils → two institution tags). Best-effort:
    # if affiliation count == author count, 1:1; if affiliation count > author count,
    # join extras onto the last; if shorter, leave trailing authors with "".
    authors = []
    if len(affs_raw) == len(authors_raw):
        pairs = list(zip(authors_raw, affs_raw))
    elif len(affs_raw) > len(authors_raw) and authors_raw:
        # Distribute affs evenly; trailing affs join onto last author.
        per_author = len(affs_raw) // len(authors_raw)
        pairs = []
        idx = 0
        for i, name in enumerate(authors_raw):
            if i == len(authors_raw) - 1:
                chunk = affs_raw[idx:]
            else:
                chunk = affs_raw[idx : idx + per_author]
                idx += per_author
            pairs.append((name, "; ".join(chunk)))
    else:
        pairs = []
        for i, name in enumerate(authors_raw):
            aff = affs_raw[i] if i < len(affs_raw) else ""
            pairs.append((name, aff))

    for i, (name, aff) in enumerate(pairs):
        authors.append({
            "name": name.strip(),
            "rasses": aff.strip(),
            "corresponding_author": (i == 0),  # heuristic — Highwire doesn't expose CA flag
        })

    return {
        "DOI": doi,
        "Link": link,
        "Authors": authors,
        "Abstract": abstract,
        "PDF URL": pdf_url,
    }


# ---- Tier 2: Claude API on cleaned HTML ------------------------------------

def _parse_json_with_repair(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    """Try to parse Claude's response as JSON, with light repair.

    Returns (data, error_msg). On success error_msg is None.
    Handles: leading/trailing markdown fences, control chars, the most common
    Claude-output mistakes. Does NOT do aggressive repair — if the JSON is
    structurally broken (missing comma, unescaped quote in middle of string),
    we surface the error and let the caller decide whether to retry the LLM.
    """
    text = raw.strip()
    # Strip ```json ... ``` fences.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    # Strip control chars that aren't valid in JSON strings.
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, str(exc)


def _strip_for_llm(html: str, budget_chars: int = HTML_BUDGET_CHARS) -> str:
    """Trim scripts/styles, keep <head> + first chunk of <body>."""
    # Drop scripts/styles aggressively; they're noise to the LLM.
    cleaned = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<style\b[^>]*>.*?</style>", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<!--.*?-->", "", cleaned, flags=re.DOTALL)
    if len(cleaned) <= budget_chars:
        return cleaned
    # Keep the head + as much body as fits.
    head_m = re.search(r"<head\b[^>]*>(.*?)</head>", cleaned, re.IGNORECASE | re.DOTALL)
    head = head_m.group(0) if head_m else ""
    body_m = re.search(r"<body\b[^>]*>(.*?)</body>", cleaned, re.IGNORECASE | re.DOTALL)
    body = body_m.group(1) if body_m else cleaned[len(head):]
    remaining = budget_chars - len(head) - 200
    if remaining > 0:
        body = body[:remaining]
    return head + "\n<body>\n" + body + "\n</body>"


def extract_via_claude(
    html: str,
    doi: str,
    link: str,
    *,
    system_prompt: str,
    model: str,
    api_key: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Pass cleaned HTML to Claude with the v1.4-style extraction prompt.

    Returns (extraction_dict, usage_dict). extraction is shaped to match the
    `human-goldie.csv` row format used by `diff_goldie.py`.
    """
    try:
        import anthropic  # local import — only needed in the fallback path
    except ImportError:
        return None, {"error": "anthropic SDK not installed"}

    client = anthropic.Anthropic(api_key=api_key)
    cleaned = _strip_for_llm(html)
    user_msg = (
        f"DOI: {doi}\nLanding page (resolved): {link}\n\n"
        f"Below is the cached landing-page HTML. Extract scholarly metadata "
        f"per the rules in your system instructions and return ONLY a JSON "
        f"object matching this Pydantic schema:\n"
        f"```json\n{json.dumps(ExtractionOut.model_json_schema(), indent=2)}\n```\n\n"
        f"--- HTML ---\n{cleaned}"
    )

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=DEFAULT_LLM_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as exc:  # noqa: BLE001 — surface whatever
        return None, {"error": f"anthropic call failed: {exc}"}

    raw = resp.content[0].text.strip() if resp.content else ""
    data, parse_err = _parse_json_with_repair(raw)

    usage = {
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }

    # If first attempt failed, ask Claude to fix the JSON (retry with explicit error context).
    if data is None:
        try:
            retry_resp = client.messages.create(
                model=model,
                max_tokens=DEFAULT_LLM_MAX_TOKENS,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_msg},
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": (
                        f"Your previous response was not valid JSON: {parse_err}.\n"
                        "Reply with ONLY a corrected JSON object — no commentary, no markdown fences. "
                        "Make sure to escape any embedded double quotes as \\\" and replace newlines/tabs "
                        "in string fields with single spaces."
                    )},
                ],
            )
        except Exception as exc:  # noqa: BLE001
            return None, {"error": f"anthropic retry failed: {exc}", "raw": raw[:400]}

        retry_raw = retry_resp.content[0].text.strip() if retry_resp.content else ""
        data, parse_err = _parse_json_with_repair(retry_raw)
        usage["input_tokens"] += retry_resp.usage.input_tokens
        usage["output_tokens"] += retry_resp.usage.output_tokens
        usage["json_retry"] = True
        if data is None:
            return None, {"error": f"json decode (after retry): {parse_err}", "raw": retry_raw[:400]}

    # Coerce to the shape diff_goldie.py expects.
    extraction = {
        "DOI": doi,
        "Link": link,
        "Authors": data.get("authors") or [],
        "Abstract": data.get("abstract") or "",
        "PDF URL": data.get("pdf_url") or "",
    }
    return extraction, usage


# ---- per-DOI orchestration -------------------------------------------------

@dataclass
class TaxicabResult:
    no: int
    doi: str
    link: str
    extraction: dict[str, Any] | None = None
    tier: str = ""  # "meta_tags" | "claude" | "failed"
    duration_s: float = 0.0
    cost_usd: float | None = None
    error: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    resolved_url: str | None = None


def _approx_cost(usage: dict[str, Any], model: str) -> float:
    """Rough Anthropic pricing — Sonnet 4.6 default."""
    if not usage or "input_tokens" not in usage:
        return 0.0
    if "opus" in model:
        in_rate, out_rate = 6.0, 30.0
    elif "haiku" in model:
        in_rate, out_rate = 0.9, 5.4
    else:
        in_rate, out_rate = 3.6, 18.0
    return (
        usage["input_tokens"] / 1_000_000 * in_rate
        + usage.get("output_tokens", 0) / 1_000_000 * out_rate
    )


def run_doi(
    no: int,
    doi: str,
    link: str,
    *,
    system_prompt: str,
    model: str,
    api_key: str | None,
    use_llm_fallback: bool = True,
    skip_meta_tags: bool = False,
) -> TaxicabResult:
    start = time.perf_counter()

    html, resolved_url, fetch_err = fetch_html(doi)
    if fetch_err or html is None:
        return TaxicabResult(
            no=no, doi=doi, link=link, tier="failed", error=fetch_err or "no html",
            duration_s=round(time.perf_counter() - start, 2),
        )

    # Claude is PRIMARY. Meta tags are SECONDARY (backfill empty rases only).
    if not use_llm_fallback or not api_key:
        return TaxicabResult(
            no=no, doi=doi, link=link, tier="failed",
            error="LLM disabled or no API key",
            duration_s=round(time.perf_counter() - start, 2),
            resolved_url=resolved_url,
        )

    extraction, usage = extract_via_claude(
        html, doi, link, system_prompt=system_prompt, model=model, api_key=api_key,
    )
    if extraction is None:
        return TaxicabResult(
            no=no, doi=doi, link=link, tier="failed",
            error=(usage or {}).get("error", "claude call failed"),
            duration_s=round(time.perf_counter() - start, 2),
            usage=usage or {},
            resolved_url=resolved_url,
        )

    # Backfill empty per-author rases from citation_author_institution meta tags
    # (skipped under --skip-meta-tags). Conservative: only fills when Claude
    # returned empty for an author whose name matches a meta-tag entry.
    if not skip_meta_tags:
        meta_extraction = extract_via_meta_tags(html, doi, link)
        if meta_extraction:
            sec_by_name = {
                (sa.get("name") or "").strip().lower(): sa
                for sa in (meta_extraction.get("Authors") or [])
            }
            for pa in (extraction.get("Authors") or []):
                if (pa.get("rasses") or "").strip():
                    continue
                sa = sec_by_name.get((pa.get("name") or "").strip().lower())
                if sa and (sa.get("rasses") or "").strip():
                    pa["rasses"] = sa["rasses"].strip()
            # PDF URL backfill from citation_pdf_url meta tag (re-enabled
            # 2026-05-04 PM per user directive: "for the PDF URL we pick the
            # URL pdf from the meta tag and that is the right not the N/A in
            # the goldie"). The original guideline during gold creation was
            # extract-from-meta-tag-regardless-of-paywall; the current N/A
            # cells in gold are downstream annotation drift, not the canonical
            # convention. Comparator rule #10 (paywalled-pattern ≅ N/A) plus
            # its extended pattern list (Emerald / JoVE / Dialogos OJS / Brill)
            # absorb the gold-N/A vs AI-meta-tag mismatch deterministically.
            if not (extraction.get("PDF URL") or "").strip():
                meta_pdf = (meta_extraction.get("PDF URL") or "").strip()
                if meta_pdf:
                    extraction["PDF URL"] = meta_pdf

    # Abstract-only JSON-LD backfill. Fires when the LLM-extracted abstract
    # looks like a meta-tag truncation (200-char ellipsis, mid-word cutoff,
    # or empty) AND the page's JSON-LD carries a longer, sentence-terminated
    # abstract. Field-isolated: only the Abstract field is touched, so the
    # cross-field regressions seen when JSON-LD was made visible to the LLM
    # itself (authors/pdf_url drifting to JSON-LD-derived values) cannot
    # occur here.
    llm_abstract = (extraction.get("Abstract") or "").strip()
    if _looks_truncated(llm_abstract):
        ld_abstract = _jsonld_abstract(html)
        if ld_abstract and len(ld_abstract) > max(len(llm_abstract) * 3 // 2, 200):
            extraction["Abstract"] = ld_abstract
            llm_abstract = ld_abstract

    # Drop abstract when the LLM emitted the article title under an abstract
    # heading (Open-Journal-Systems convention on pages with no real
    # abstract).
    if _is_title_as_abstract(llm_abstract, html):
        extraction["Abstract"] = ""
        llm_abstract = ""

    # Latin-abstract preference. When the LLM extracted a Cyrillic / CJK /
    # Devanagari abstract from a page that also presents an explicit
    # ``<b>Abstract:</b>`` Latin block (gold-convention preference for
    # English when both languages are on the page), replace with the Latin
    # version.
    if llm_abstract and _is_mostly_non_latin(llm_abstract):
        latin = _latin_abstract_from_label(html)
        if latin and len(latin) >= 120:
            extraction["Abstract"] = latin

    # Post-LLM corresponding-author corrections. Two surgical, page-evidence
    # gated rules that are safe to apply across all extractions:
    # (a) drop spurious all-CA flagging when there is no explicit marker on
    #     the page (catches the per-author-mailto false positive);
    # (b) backfill CA from explicit ``class*="corresp"`` HTML wrappers when
    #     the LLM didn't pick up the marker.
    authors = extraction.get("Authors") or []
    _maybe_drop_all_ca(authors, html)
    _maybe_backfill_ca_from_class(authors, html)

    # Affiliation backfill from T&F's visible-HTML overlay pattern (T&F omits
    # citation_author_institution Highwire tags; the existing
    # ``extract_via_meta_tags`` path can't help). Conservative: only fills
    # per-author rases that the LLM left empty.
    _maybe_backfill_rases_from_overlay(authors, html)

    # Affiliation backfill from Elsevier ScienceDirect's React-SPA JSON.
    # Cached HTML carries author/affiliation data ONLY in a
    # ``<script data-iso-key="_0">`` JSON blob; ``_strip_for_llm`` removes
    # script tags so the LLM never sees this content. Worked example: CVIU
    # 10.1006/cviu.2002.0969 — backfills the 3 University-of-Amsterdam
    # affiliations the LLM emits empty.
    _maybe_backfill_rases_from_elsevier_iso(authors, html)

    # Affiliation backfill from JSON-LD ScholarlyArticle author[].affiliation
    # (added 2026-05-07). Targets publishers that ship JSON-LD with nested
    # affiliations (Frontiers, PLOS, some OUP). Conservative: only fills empty
    # per-author rasses. No-op on publishers that don't ship JSON-LD (MDPI etc).
    _maybe_backfill_rases_from_jsonld(authors, html)

    # Emerald book-chapter abstract backfill (added 2026-05-07).
    # Emerald wraps the abstract in <div class="category-section content-section">
    # without an explicit 'Abstract' heading; the LLM can't tell what to grab.
    # Worked example: 10.1108/978-1-64802-637-920251008.
    _maybe_backfill_abstract_from_emerald(extraction, html, doi)

    # Old-Elsevier OUP-redirect CA flag backfill (added 2026-05-07).
    # Detects info-author-correspondence + mailto pattern (where the existing
    # class-based CA backfill misses due to word-boundary mismatch on
    # 'correspondence' vs 'corresp'). Worked example: Train 5
    # 10.1016/s0378-1097(99)00346-8 (Alje P. van Dam).
    _maybe_backfill_ca_from_oup_email(authors, html)

    # MDPI per-publisher rasses + CA flag backfill (added 2026-05-07).
    # MDPI doesn't ship JSON-LD or citation_author_institution meta tags.
    # Affiliations live in <div class="art-affiliations"> with <sup>N</sup>
    # markers attached to author names. Gold's convention for MDPI rows is
    # the literal sup-marker content ("1,†", "3,*"), NOT the resolved
    # institution name. Sets corresponding_author=True when the marker
    # contains "*". Targets Train rows 18 (10.3390/polym13183031) and 19
    # (10.3390/su13041644).
    _maybe_backfill_rases_and_ca_from_mdpi(authors, html, doi)

    return TaxicabResult(
        no=no, doi=doi, link=link, extraction=extraction, tier="claude",
        cost_usd=round(_approx_cost(usage or {}, model), 4),
        usage=usage or {},
        resolved_url=resolved_url,
        duration_s=round(time.perf_counter() - start, 2),
    )


# ---- CSV emit --------------------------------------------------------------

def to_gold_row(result: TaxicabResult) -> dict[str, Any]:
    """Match the GOLD_COLUMNS schema used by extract_batch_cloud + diff_goldie."""
    extraction = result.extraction or {}
    authors = extraction.get("Authors") or []
    return {
        "No": result.no,
        "DOI": result.doi,
        "Link": result.link,
        "Authors": json.dumps(authors, ensure_ascii=False),
        "Abstract": extraction.get("Abstract") or "",
        "PDF URL": extraction.get("PDF URL") or "",
        "Status": "TRUE" if extraction else "FALSE",
        "Notes": "" if extraction else (result.error or ""),
        "Has Bot Check": "FALSE",
        "Resolves To PDF": "FALSE",
        "broken_doi": "FALSE",
        "no english": "FALSE",
    }


# ---- main ------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True,
                        help="Holdout/train CSV with No, DOI, Link columns.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True,
                        help="Prompt .md (system prompt body extracted).")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--no-llm-fallback", dest="llm_fallback",
                        action="store_false", default=True)
    parser.add_argument("--skip-meta-tags", action="store_true",
                        help="Force every DOI through Claude (Casey's apples-to-apples test).")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_csv = args.output_dir / "ai-goldie-1.csv"
    log_jsonl = args.output_dir / "ai-goldie-1.tier-log.jsonl"

    version, system_prompt = load_prompt(args.prompt)
    log.info("prompt %s (version=%s, %d chars)", args.prompt, version, len(system_prompt))

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if args.llm_fallback and not api_key:
        log.warning("ANTHROPIC_API_KEY not set; LLM fallback unavailable.")

    rows: list[dict[str, str]] = []
    with args.source.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            try:
                n = int(r.get("No") or 0)
            except ValueError:
                continue
            doi = (r.get("DOI") or "").strip()
            link = (r.get("Link") or "").strip() or f"https://doi.org/{doi}"
            if doi:
                rows.append({"No": str(n), "DOI": doi, "Link": link})
    log.info("rows to process: %d", len(rows))

    results: list[TaxicabResult] = []

    async def runner():
        sem = asyncio.Semaphore(args.concurrency)

        async def one(row):
            async with sem:
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(
                    None,
                    lambda: run_doi(
                        int(row["No"]), row["DOI"], row["Link"],
                        system_prompt=system_prompt,
                        model=args.model,
                        api_key=api_key,
                        use_llm_fallback=args.llm_fallback,
                        skip_meta_tags=args.skip_meta_tags,
                    ),
                )

        coros = [one(r) for r in rows]
        for done in asyncio.as_completed(coros):
            res = await done
            results.append(res)
            log.info(
                "[%d/%d] No=%d %s tier=%s %.1fs %s",
                len(results), len(rows), res.no, res.doi, res.tier,
                res.duration_s,
                f"cost=${res.cost_usd:.3f}" if res.cost_usd else "(free)",
            )
            with log_jsonl.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "no": res.no, "doi": res.doi, "tier": res.tier,
                    "duration_s": res.duration_s, "cost_usd": res.cost_usd,
                    "error": res.error, "usage": res.usage,
                    "resolved_url": res.resolved_url,
                }) + "\n")

    asyncio.run(runner())

    # Write final CSV in `No` order.
    results.sort(key=lambda r: r.no)
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GOLD_COLUMNS)
        w.writeheader()
        for r in results:
            w.writerow(to_gold_row(r))

    n_meta = sum(1 for r in results if r.tier == "meta_tags")
    n_claude = sum(1 for r in results if r.tier == "claude")
    n_failed = sum(1 for r in results if r.tier == "failed")
    total_cost = sum(r.cost_usd or 0.0 for r in results)

    log.info(
        "DONE — meta-tag: %d / claude: %d / failed: %d / total cost: $%.2f",
        n_meta, n_claude, n_failed, total_cost,
    )
    log.info("CSV: %s", out_csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
