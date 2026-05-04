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
            # Note: a citation_pdf_url backfill was tried here on 2026-05-04
            # and reverted (-6pp on holdout-50 pdf_url). The publisher's own
            # citation_pdf_url meta tag IS a valid PDF link, but gold marks
            # most book-chapter / non-typical-article DOIs as "N/A" by
            # convention even when the publisher exposes the link. So
            # backfilling from this meta tag breaks 4 "both empty = match"
            # rows for every 1 real-miss it recovers (Brill 10.1163/...).
            # The fix space is gold-convention, not extraction. See
            # GOLD-UPDATE-PROPOSAL pdf_url section + REPORT.md.

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
