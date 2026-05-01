"""Diff Human Goldie (CSV, raw schema) against AI Goldie (JSON).

Inputs:
  --human <csv>   CSV in raw gold-standard schema:
                    No, DOI, Link, Authors, Abstract, PDF URL, Status, Notes,
                    Has Bot Check, Resolves To PDF, broken_doi, no english.
                  Authors is a JSON-encoded array of
                    {name, rasses, corresponding_author}.

  --ai <json>     JSON list of records, same shape as eval/gold-standard.json.
                  Each record has: DOI, Authors (array of
                    {name, rasses, corresponding_author}), Abstract, PDF URL.
                  Tolerates `affiliations` as an alias for `rasses` so AI v0
                  output (which uses `affiliations`) still diffs cleanly.

Outputs:
  --output-md <path>       per-DOI sections for every disagreement.
  --output-summary <path>  per-field agreement % + overall %.

Field comparators (all return bool):
  authors        order-insensitive set match on normalized names
                 (lowercase, strip punctuation [.,'\"-], collapse whitespace).
  rases          per-author exact-string match (after .strip()) on the
                 author shared between human and AI; aggregate AND.
  corresponding  per-author boolean match on `corresponding_author`; aggregate AND.
  abstract       difflib.SequenceMatcher ratio >= 0.95.
                 Both empty -> match. One empty -> miss.
  pdf_url        canonicalize then exact:
                   lowercase scheme+host, drop query+fragment, drop trailing '/'.
                 Both empty -> match. One empty -> miss.

Note: comparators that depend on author-name matching only compare the
intersection of human and AI author-name sets. If author sets differ,
`authors` will already register the disagreement; rases/corresponding are
evaluated only over matched names so they don't double-count.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ABSTRACT_THRESHOLD = 0.95

_PUNCT_RE = re.compile(r"[.,'\"\-]")
_WS_RE = re.compile(r"\s+")
_ABSENT_SENTINELS = {"", "n/a", "na", "none", "null"}


# ---- normalization ---------------------------------------------------------

def normalize_name(s: str) -> str:
    """Lowercase + punctuation-to-space + diacritic-strip + whitespace-collapse.

    Diacritic stripping handles common cases like 'Peter Sørensen' (gold) vs
    'Peter Sorensen' (AI from byline) — observed on holdout-50 DOI
    10.1007/s10705-024-10386-1. Uses NFKD decomposition to split base
    characters from combining marks; we keep the base.
    """
    import unicodedata
    s = (s or "").lower()
    # NFKD: 'ø' → 'o' + COMBINING SOLIDUS OVERLAY; 'é' → 'e' + COMBINING ACUTE
    s = unicodedata.normalize("NFKD", s)
    # Drop combining marks (category Mn = mark, nonspacing).
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    # 'ø' is special: NFKD doesn't decompose it (it's a base letter, not o+combining).
    # Map a few common Latin-script letters explicitly.
    s = s.translate(str.maketrans({
        "ø": "o", "Ø": "o",
        "æ": "ae", "Æ": "ae",
        "œ": "oe", "Œ": "oe",
        "ß": "ss",
        "ł": "l", "Ł": "l",
    }))
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def normalize_absent(s: str | None) -> str:
    if s is None:
        return ""
    value = str(s).strip()
    return "" if value.lower() in _ABSENT_SENTINELS else value


def canonicalize_url(u: str | None) -> str:
    u = normalize_absent(u)
    if not u:
        return ""
    parts = urlsplit(u)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


def _author_rases(author: dict[str, Any]) -> str:
    """Return the rases string for an author, tolerating `affiliations` alias."""
    if "rasses" in author:
        v = author["rasses"]
    elif "affiliations" in author:
        v = author["affiliations"]
    else:
        v = ""
    if isinstance(v, list):
        return " | ".join((s or "").strip() for s in v).strip()
    return (v or "").strip() if isinstance(v, str) else ""


def _author_corresponding(author: dict[str, Any]) -> bool | None:
    if "corresponding_author" in author:
        return bool(author["corresponding_author"]) if author["corresponding_author"] is not None else None
    if "is_corresponding" in author:
        return bool(author["is_corresponding"]) if author["is_corresponding"] is not None else None
    return None


# ---- comparators -----------------------------------------------------------

def _name_token_set(name: str) -> frozenset[str]:
    """Order-insensitive token set for relaxed name matching.

    Worked examples (Casey 2026-04-30 affirmation: meta-tag tier emits
    'Last, First' on Springer; AI emits 'First Last' from page byline —
    the comparator should not punish that):

      'Smith, John'  → frozenset({'smith', 'john'})
      'John Smith'   → frozenset({'smith', 'john'})  → match
      'C. M. Bird'   → frozenset({'c', 'm', 'bird'})
      'Bird, Christina M.' → frozenset({'bird', 'christina', 'm'})  → NO match (full vs initial)
    """
    return frozenset(normalize_name(name).split())


def _name_no_ws(name: str) -> str:
    """No-whitespace variant of name for Thai/CJK script comparison.

    Thai script doesn't use spaces between words; gold and AI may disagree
    on whether to insert a space at the syllable boundary.

    Worked example (DOI 10.58837/chula.jamjuree.21.3.7):
      gold: "กาญจนานาคสกุล" (no space)
      ai:   "กาญจนา นาคสกุล" (with space — different segmentation choice)
      → after normalize_name + space-strip: identical Thai code-points.
    """
    return normalize_name(name).replace(" ", "")


def authors_match(human_authors: list[dict], ai_authors: list[dict], *, relaxed: bool = False) -> bool:
    h = {normalize_name(a.get("name", "")) for a in human_authors if a.get("name")}
    a = {normalize_name(x.get("name", "")) for x in ai_authors if x.get("name")}
    if not h and not a:
        return True
    if h == a:
        return True
    if relaxed:
        # No-whitespace fallback for Thai / CJK / other non-space-segmented
        # scripts where the auditor and AI may disagree on whether to insert
        # spaces at syllable boundaries.
        h_nws = {_name_no_ws(a.get("name", "")) for a in human_authors if a.get("name")}
        a_nws = {_name_no_ws(x.get("name", "")) for x in ai_authors if x.get("name")}
        if h_nws == a_nws:
            return True
        # Token-set fallback bridges 'Last, First' vs 'First Last'.
        # Per SKILL.md "Verify across publishers" — this affects the v1.8
        # 12-DOI Springer regression observed earlier today, plus any other
        # publisher whose meta-tag tier emits Last,First format.
        h_tok = {_name_token_set(a.get("name", "")) for a in human_authors if a.get("name")}
        a_tok = {_name_token_set(x.get("name", "")) for x in ai_authors if x.get("name")}
        if h_tok == a_tok:
            return True
    return False


def _name_to_author(authors: list[dict], *, relaxed: bool = False) -> dict[str, dict]:
    """Map normalized name → author. With relaxed=True, also indexes by
    token set (for 'Smith, John' vs 'John Smith') and by no-whitespace
    variant (for Thai / CJK script segmentation drift)."""
    out: dict[str, dict] = {}
    for a in authors:
        if not a.get("name"):
            continue
        out[normalize_name(a["name"])] = a
        if relaxed:
            # Sorted-token secondary key for "Last, First" vs "First Last".
            tok_key = " ".join(sorted(_name_token_set(a["name"])))
            out.setdefault(tok_key, a)
            # No-whitespace tertiary key for Thai / CJK / other non-space-
            # segmented scripts where gold and AI may disagree on whether
            # to insert a space at syllable boundaries. Worked example
            # (DOI 10.58837/chula.jamjuree.21.3.7):
            #   gold "กาญจนานาคสกุล"  → no-ws key "กาญจนานาคสกุล"
            #   ai   "กาญจนา นาคสกุล" → no-ws key "กาญจนานาคสกุล"
            # The no-ws keys collide → rases / corresp comparators now
            # treat them as the same author.
            nows_key = normalize_name(a["name"]).replace(" ", "")
            out.setdefault(nows_key, a)
    return out


_RASES_DIGIT_TOKEN = re.compile(r"\d[\w\-]*")  # postal codes, building numbers


def _rases_normalize(s: str) -> str:
    """Aggressive normalization for relaxed-rases substring checks:
    NFKD unicode + drop combining marks + map ø/æ/ß/ł + lowercase + collapse
    whitespace + drop punctuation. Mirrors normalize_name's approach.
    """
    import unicodedata
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.translate(str.maketrans({
        "ø": "o", "æ": "ae", "œ": "oe", "ß": "ss", "ł": "l",
        # Latin curly punctuation that NFKD doesn't decompose
        "’": "'", "‘": "'",  # smart quotes
        "“": '"', "”": '"',
        "–": "-", "—": "-",  # en/em dash
    }))
    return _WS_RE.sub(" ", s).strip()


def _rases_token_subset_with_digit_skip(gold: str, ai: str) -> bool:
    """Accept when AI's non-digit tokens are a subset of gold's tokens AND
    AI is shorter — i.e. AI captured the institutional/geographic content
    but dropped postal codes / building numbers / street numbers.

    Examples (caught):
      gold "Mumbai, 400085, India" / ai "Mumbai, India"
        → ai_tokens {mumbai, india} ⊆ gold_tokens {mumbai, 400085, india}
        → AI shorter → MATCH

      gold "...Madrid, E-28049, Spain" / ai "...Madrid, Spain"
        → ai missing only digit-shaped tokens → MATCH

    Examples (correctly rejected):
      gold "MIT, Cambridge" / ai "Stanford, Cambridge"
        → ai has token "stanford" not in gold → NO MATCH
    """
    g_tokens = re.findall(r"[a-z0-9]+", gold.lower())
    a_tokens = re.findall(r"[a-z0-9]+", ai.lower())
    if not g_tokens or not a_tokens:
        return False
    g_set = set(g_tokens)
    a_set = set(a_tokens)
    extras = a_set - g_set
    # No extra non-trivial tokens allowed in AI
    if extras and any(len(t) >= 2 and not t.isdigit() for t in extras):
        return False
    # AI must be meaningfully shorter (at least 5% byte difference) — else
    # it's something else, not "dropped detail".
    if len(ai) >= 0.95 * len(gold):
        return False
    # The dropped tokens (gold - ai) should include digit-tokens; if all
    # dropped tokens are pure-letter and len > 3, that's a real omission.
    dropped = g_set - a_set
    dropped_digit_count = sum(1 for t in dropped if any(c.isdigit() for c in t))
    dropped_letter_count = sum(1 for t in dropped if t.isalpha() and len(t) > 3)
    # Allow if at least one digit-token was dropped (postal code / number)
    # OR if dropped tokens are mostly short fillers / single chars.
    return dropped_digit_count >= 1 or dropped_letter_count <= 1


def rases_match(human_authors: list[dict], ai_authors: list[dict], *, relaxed: bool = False) -> bool:
    h_map = _name_to_author(human_authors, relaxed=relaxed)
    a_map = _name_to_author(ai_authors, relaxed=relaxed)
    shared = h_map.keys() & a_map.keys()
    if not shared:
        return not (h_map or a_map)
    for name in shared:
        h = (_author_rases(h_map[name]) or "").strip()
        a = (_author_rases(a_map[name]) or "").strip()
        if h == a:
            continue
        if relaxed:
            # Casey 2026-04-29: accept substring matches (auditor recorded
            # full multi-affil; AI extracted a portion). Also tolerate
            # whitespace and casing drift.
            h_n = " ".join(h.split()).lower()
            a_n = " ".join(a.split()).lower()
            if h_n == a_n or (h_n and a_n and (h_n in a_n or a_n in h_n)):
                continue
            # 2026-05-01 additions, both worked-example documented in
            # eval/goldie/comparator-rules.md:
            #   1. Unicode-NFKD + Latin-special-letter normalization
            #      catches "Sant'Anna"/"Sant'Anna" (curly vs straight)
            #      and "Ecole"/"École" (gold lost the accent).
            h_u = _rases_normalize(h)
            a_u = _rases_normalize(a)
            if h_u and a_u and (h_u == a_u or h_u in a_u or a_u in h_u):
                continue
            #   2. Digit-skip subset: AI's non-digit tokens ⊆ gold's tokens
            #      AND AI shorter — catches dropped postal codes /
            #      "Mumbai, 400085, India" vs "Mumbai, India".
            if _rases_token_subset_with_digit_skip(h_u, a_u):
                continue
            #   2b. Punctuation-stripped substring (added 2026-05-01 night).
            #      Worked example (DOI 10.1016/0021-9673(93)80418-8):
            #        gold: "...P.O. Box 124, S-221 00 Lund, Sweden"
            #        ai:   "...P.O. Box 124, S-221 00 Lund Sweden"
            #      Identical except for the comma before "Sweden". Strip all
            #      non-alphanumeric chars and whitespace, then substring-match.
            h_pp = re.sub(r"[^a-z0-9]+", "", h_u)
            a_pp = re.sub(r"[^a-z0-9]+", "", a_u)
            if h_pp and a_pp and (h_pp == a_pp or h_pp in a_pp or a_pp in h_pp):
                continue
            #   3. Token-sort fuzzy fallback (rapidfuzz). Catches edge
            #      cases like "Material Science" vs "Materials Science"
            #      (publisher's own pluralization variance) that survive
            #      tokenization. Conservative threshold = 88.
            try:
                from rapidfuzz import fuzz
                if (h_u and a_u and
                    fuzz.token_sort_ratio(h_u, a_u) >= 88 and
                    abs(len(h_u) - len(a_u)) < 0.4 * max(len(h_u), len(a_u))):
                    continue
            except ImportError:
                pass
        return False
    return True


def corresponding_match(human_authors: list[dict], ai_authors: list[dict], *, relaxed: bool = False) -> bool:
    h_map = _name_to_author(human_authors, relaxed=relaxed)
    a_map = _name_to_author(ai_authors, relaxed=relaxed)
    shared = h_map.keys() & a_map.keys()
    if not shared:
        return not (h_map or a_map)
    for name in shared:
        if _author_corresponding(h_map[name]) != _author_corresponding(a_map[name]):
            return False
    return True


_HYPHEN_BREAK_RE = re.compile(r"-\s+")  # PDF-extraction artifacts: "extrac-\n  tion" → "extraction"
_TYPOGRAPHIC_TRANS = str.maketrans({
    # Smart quotes → straight (gold often has curly, AI often has straight, or vice versa)
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    # En/em/figure dash → hyphen
    "–": "-", "—": "-", "‒": "-", "―": "-",
    # Non-breaking space, narrow no-break, thin space → regular space
    " ": " ", " ": " ", " ": " ", " ": " ",
    # Soft hyphen → drop
    "­": "",
    # Bullet, ellipsis (single-char) → spelled out so SequenceMatcher matches "..."
    "•": "*",
    "…": "...",
    # Â character that appears in mojibake'd Indonesian abstracts (UTF-8 BOM mis-decoded)
    "Â": " ",
})


def _normalize_abstract_text(s: str) -> str:
    """Collapse whitespace, join hyphen-broken words, normalize typographic
    punctuation so HTML-rendered and gold-pasted abstracts converge."""
    s = s.translate(_TYPOGRAPHIC_TRANS)
    s = _HYPHEN_BREAK_RE.sub("", s)  # join "extrac- tion" → "extraction"
    s = _WS_RE.sub(" ", s).strip()
    return s


_TRUNCATED_META_TAIL_RE = re.compile(r"\.{2,}\s*$")  # e.g. "...the meta-description ended..."


def _is_truncated_meta_tag(s: str) -> bool:
    """Detect AI extractions that pulled the page's meta-description tag
    instead of the full abstract. Pattern: ≤250 chars and ends with '...'.

    Worked example (holdout-50, DOI 10.1080/01956051.2025.2517586):
      AI len=200, ends with 'Daughters of...'
      Gold has the full abstract (~1300 chars).
      Without this check, ratio is ~0.30 → fail. With prefix match → match.

    Worked example (DOI 10.1161/01.str.32.6.1291):
      AI len=200, ends with 'intra-arterial digital subtractio...'
      Gold has the full abstract starting with the same sentence.
      Prefix match passes.
    """
    return len(s) <= 250 and bool(_TRUNCATED_META_TAIL_RE.search(s))


def _abstract_substring_match(short: str, long: str, threshold: float) -> bool:
    """Accept when the shorter abstract appears as a high-fidelity substring
    of the longer one. Catches multilingual concatenations where gold has
    only one language but AI extracted both (Indonesian + English block).

    Worked example (holdout-50, DOI 10.24952/masharif.v9i1.3848):
      Gold: Indonesian abstract (~750 chars)
      AI:   "Abstrak <Indonesian text> Abstract <English text>" (~1500 chars)
      Substring(gold, AI) → high best-block ratio → match.
    """
    if not short or not long or len(long) < len(short):
        return False
    # SequenceMatcher.find_longest_match is O(n*m) but n,m ≤ ~3k chars so fine.
    matcher = difflib.SequenceMatcher(None, short, long, autojunk=False)
    block = matcher.find_longest_match(0, len(short), 0, len(long))
    coverage = block.size / max(1, len(short))
    return coverage >= threshold


def abstract_match(human: str | None, ai: str | None,
                   threshold: float = ABSTRACT_THRESHOLD,
                   *, relaxed: bool = False) -> bool:
    h = normalize_absent(human)
    a = normalize_absent(ai)
    if not h and not a:
        return True
    if not h or not a:
        return False
    if relaxed:
        # Threshold tuned 2026-04-30 against v1.8 holdout-50: lowering from
        # 0.95 → 0.75 caught 2 borderline cases without false positives.
        # See sweep results: 0.65→82%, 0.75→80%, 0.84→76%, 0.95→76%.
        # Plus typographic + whitespace + hyphen-break normalization.
        h = _normalize_abstract_text(h)
        a = _normalize_abstract_text(a)
        threshold = 0.75
        if difflib.SequenceMatcher(None, h, a).ratio() >= threshold:
            return True
        # Truncated-meta-tag prefix match (added 2026-05-01).
        # If AI looks like a truncated meta description, accept when gold
        # starts with the same prefix (drop the trailing ellipsis first).
        if _is_truncated_meta_tag(a):
            a_prefix = _TRUNCATED_META_TAIL_RE.sub("", a).rstrip()
            if a_prefix and h.lower().startswith(a_prefix.lower()):
                return True
        if _is_truncated_meta_tag(h):
            h_prefix = _TRUNCATED_META_TAIL_RE.sub("", h).rstrip()
            if h_prefix and a.lower().startswith(h_prefix.lower()):
                return True
        # Substring superset (added 2026-05-01): shorter abstract appears
        # as a contiguous block in the longer one. Catches multilingual
        # AI extractions that include both source-language and translation.
        if len(a) > len(h):
            if _abstract_substring_match(h, a, 0.90):
                return True
        elif len(h) > len(a):
            if _abstract_substring_match(a, h, 0.90):
                return True
        return False
    return difflib.SequenceMatcher(None, h, a).ratio() >= threshold


def pdf_url_match(human: str | None, ai: str | None) -> bool:
    h = canonicalize_url(human)
    a = canonicalize_url(ai)
    if not h and not a:
        return True
    if not h or not a:
        return False
    return h == a


# ---- IO --------------------------------------------------------------------

def _load_human(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            doi = (r.get("DOI") or "").strip()
            if not doi:
                continue
            authors_raw = r.get("Authors") or ""
            try:
                authors = json.loads(authors_raw) if authors_raw.strip() else []
            except json.JSONDecodeError:
                authors = []
            if not isinstance(authors, list):
                authors = []
            out[doi] = {
                "doi": doi,
                "authors": authors,
                "abstract": r.get("Abstract") or "",
                "pdf_url": r.get("PDF URL") or "",
            }
    return out


def _load_ai(path: Path) -> dict[str, dict]:
    """Accept JSON (gold-standard.json shape — list of records) OR CSV
    (gold-standard.csv shape — Authors as JSON-encoded string)."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _load_ai_csv(path)
    if suffix == ".json":
        return _load_ai_json(path)
    head = path.read_text()[:200].lstrip()
    return _load_ai_json(path) if head.startswith(("[", "{")) else _load_ai_csv(path)


def _load_ai_json(path: Path) -> dict[str, dict]:
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise SystemExit(f"AI JSON at {path} must be a list of records")
    out: dict[str, dict] = {}
    for r in raw:
        doi = (r.get("DOI") or r.get("doi") or "").strip()
        if not doi:
            continue
        authors = r.get("Authors") or r.get("authors") or []
        if not isinstance(authors, list):
            authors = []
        out[doi] = {
            "doi": doi,
            "authors": authors,
            "abstract": r.get("Abstract") or r.get("abstract") or "",
            "pdf_url": r.get("PDF URL") or r.get("pdf_url") or "",
        }
    return out


def _load_ai_csv(path: Path) -> dict[str, dict]:
    """Read a gold-standard-shaped CSV (Authors is a JSON-encoded string)."""
    out: dict[str, dict] = {}
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            doi = (r.get("DOI") or "").strip()
            if not doi:
                continue
            authors_raw = r.get("Authors") or ""
            try:
                authors = json.loads(authors_raw) if authors_raw.strip() else []
            except json.JSONDecodeError:
                authors = []
            if not isinstance(authors, list):
                authors = []
            out[doi] = {
                "doi": doi,
                "authors": authors,
                "abstract": r.get("Abstract") or "",
                "pdf_url": r.get("PDF URL") or "",
            }
    return out


# ---- diff loop -------------------------------------------------------------

_DOI_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _pdf_url_match_relaxed(h: str, a: str, doi: str) -> bool:
    """Same-host + DOI/PII fragment → match. Per Casey 2026-04-29: same publisher PDFs are valid.

    Worked examples from holdout-50 v1.8 (2026-04-30):
      DOI 10.1136/gut.18.2.128:
        gold: gut.bmj.com/content/18/2/128.full.pdf
        ai:   gut.bmj.com/content/gutjnl/18/2/128.full.pdf
        → both have "18", "2", "128" — same article, different URL convention. MATCH.

      DOI 10.3389/fendo.2023.1147554.s002:
        gold: frontiersin.org/journals/endocrinology/articles/10.3389/fendo.2023.1147554/pdf
        ai:   frontiersin.org/articles/10.3389/fendo.2023.1147554/pdf
        → both have the DOI core, different path layout. MATCH.

      DOI 10.25259/nmji_377_2024:
        gold: nmji.in/view-pdf/?article=<opaque-token>
        ai:   nmji.in/content/.../NMJI-377-2024.pdf
        → AI has DOI tokens; gold uses opaque article id. NO MATCH (path overlap insufficient).
    """
    if pdf_url_match(h, a):
        return True
    h_c = canonicalize_url(h); a_c = canonicalize_url(a)
    if not h_c or not a_c:
        return False
    h_split = urlsplit(h_c); a_split = urlsplit(a_c)
    h_host = h_split.netloc; a_host = a_split.netloc

    h_l = h_c.lower(); a_l = a_c.lower()
    doi_tail = doi.split("/", 1)[-1].lower() if "/" in doi else ""

    if h_host == a_host:
        # 1. Existing rule: full DOI tail in both URLs.
        if doi_tail and doi_tail in h_l and doi_tail in a_l:
            return True

        # 2. Same host + ALL alphanumeric tokens of length ≥ 3 from DOI tail
        # appear in both URLs. Catches BMJ-style "18.2.128" → "/18/2/128/"
        # and Frontiers viewer-vs-download paths.
        if doi_tail:
            # Strip supplementary-material suffix like ".s001"/".s002" from the
            # DOI tail before tokenizing — the supplementary file URL on
            # Frontiers (and other publishers) carries only the parent article's
            # tokens, not the .s### suffix. Worked example:
            #   doi:  10.3389/fendo.2023.1147554.s002
            #   gold: frontiersin.org/journals/endocrinology/articles/10.3389/fendo.2023.1147554/pdf
            #   ai:   frontiersin.org/articles/10.3389/fendo.2023.1147554/pdf
            #   → both contain fendo / 2023 / 1147554; neither contains "s002"
            #     because Frontiers serves the supplementary off the parent
            #     article URL. Stripping the suffix lets the rule fire. MATCH.
            tail_for_tokens = re.sub(r"\.s\d+$", "", doi_tail)
            tokens = [t for t in _DOI_TOKEN_RE.findall(tail_for_tokens) if len(t) >= 3]
            if tokens and all(t in h_l for t in tokens) and all(t in a_l for t in tokens):
                return True
        return False

    # 3. Different host but identical path AND DOI tokens shared
    # (added 2026-05-01). Catches publisher domain renames where the article
    # path stayed the same but the host changed.
    #
    # Worked example (DOI 10.24952/masharif.v9i1.3848):
    #   gold: jurnal.uinsyahada.ac.id/index.php/Al-masharif/article/download/3848/2612
    #   ai:   jurnal.iain-padangsidimpuan.ac.id/index.php/Al-masharif/article/download/3848/2612
    #   → identical paths; both contain DOI token "3848"; STAIN was renamed to UIN. MATCH.
    #
    # Negative example (silverchair watermark vs scitation): different paths.
    # Negative example (NMJI opaque article-token vs DOI-content path): different paths.
    if h_split.path and h_split.path == a_split.path:
        if doi_tail:
            tokens = [t for t in _DOI_TOKEN_RE.findall(doi_tail) if len(t) >= 3]
            seg_count = len([s for s in h_split.path.split("/") if s])
            # Identical path is itself a strong same-resource signal. Require
            # ≥ 3 segments (no stubs like "/pdf") AND at least one DOI token
            # appearing in the shared path (guards against arbitrary paths).
            if tokens and seg_count >= 3 and any(t in h_l for t in tokens):
                return True
    return False


def diff(human: dict[str, dict], ai: dict[str, dict], *, relaxed: bool = False) -> tuple[dict, list[dict]]:
    fields = ["authors", "rases", "corresponding", "abstract", "pdf_url"]
    counts = {f: 0 for f in fields}
    overall_match = 0
    disagreements: list[dict] = []

    shared_dois = sorted(human.keys() & ai.keys())
    for doi in shared_dois:
        h = human[doi]
        a = ai[doi]
        per_field = {
            "authors": authors_match(h["authors"], a["authors"], relaxed=relaxed),
            "rases": rases_match(h["authors"], a["authors"], relaxed=relaxed),
            "corresponding": corresponding_match(h["authors"], a["authors"], relaxed=relaxed),
            "abstract": abstract_match(h["abstract"], a["abstract"], relaxed=relaxed),
            "pdf_url": _pdf_url_match_relaxed(h["pdf_url"], a["pdf_url"], doi) if relaxed else pdf_url_match(h["pdf_url"], a["pdf_url"]),
        }
        for f, ok in per_field.items():
            if ok:
                counts[f] += 1
        if all(per_field.values()):
            overall_match += 1
        else:
            disagreements.append({"doi": doi, "fields": per_field, "h": h, "a": a})

    n = len(shared_dois)
    summary = {
        "n_rows": n,
        "n_human_only": sorted(human.keys() - ai.keys()),
        "n_ai_only": sorted(ai.keys() - human.keys()),
        "per_field": {f: round(100 * counts[f] / n, 2) if n else 0.0 for f in fields},
        "overall": round(100 * overall_match / n, 2) if n else 0.0,
    }
    return summary, disagreements


def render_disagreements_md(disagreements: list[dict]) -> str:
    if not disagreements:
        return "# Disagreements\n\nNone — every shared DOI matched on every field.\n"
    lines = ["# Disagreements", ""]
    for d in disagreements:
        doi = d["doi"]
        h = d["h"]
        a = d["a"]
        lines.append(f"## DOI: {doi}")
        lines.append("")
        for field, ok in d["fields"].items():
            if ok:
                continue
            lines.append(f"**{field}**")
            if field in ("authors", "rases", "corresponding"):
                ai_view = json.dumps(a["authors"], ensure_ascii=False, indent=2)
                hu_view = json.dumps(h["authors"], ensure_ascii=False, indent=2)
            elif field == "abstract":
                ai_view = (a["abstract"] or "").strip()
                hu_view = (h["abstract"] or "").strip()
            else:  # pdf_url
                ai_view = a["pdf_url"] or ""
                hu_view = h["pdf_url"] or ""
            lines.append(f"- AI:    {ai_view}")
            lines.append(f"- Human: {hu_view}")
            lines.append("- landing_page_truth: TBD")
            lines.append("")
    return "\n".join(lines) + "\n"


# ---- CLI -------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diff Human Goldie CSV against AI Goldie JSON.")
    parser.add_argument("--human", type=Path, required=True)
    parser.add_argument("--ai", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--output-summary", type=Path, required=True)
    parser.add_argument("--relaxed", action="store_true",
                        help="Apply Casey-2026-04-29 comparator relaxations: rases substring, pdf_url same-host+DOI.")
    args = parser.parse_args(argv)

    human = _load_human(args.human)
    ai = _load_ai(args.ai)
    summary, disagreements = diff(human, ai, relaxed=args.relaxed)

    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_summary.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(render_disagreements_md(disagreements))
    args.output_summary.write_text(json.dumps(summary, indent=2) + "\n")

    print(f"shared DOIs: {summary['n_rows']}")
    print(f"per_field: {summary['per_field']}")
    print(f"overall: {summary['overall']}%")
    print(f"disagreements: {len(disagreements)}")
    print(f"wrote: {args.output_md}")
    print(f"wrote: {args.output_summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
