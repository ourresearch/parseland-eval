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
    s = (s or "").lower()
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

def authors_match(human_authors: list[dict], ai_authors: list[dict]) -> bool:
    h = {normalize_name(a.get("name", "")) for a in human_authors if a.get("name")}
    a = {normalize_name(x.get("name", "")) for x in ai_authors if x.get("name")}
    if not h and not a:
        return True
    return h == a


def _name_to_author(authors: list[dict]) -> dict[str, dict]:
    return {normalize_name(a.get("name", "")): a for a in authors if a.get("name")}


def rases_match(human_authors: list[dict], ai_authors: list[dict]) -> bool:
    h_map = _name_to_author(human_authors)
    a_map = _name_to_author(ai_authors)
    shared = h_map.keys() & a_map.keys()
    if not shared:
        # No shared authors. Treat as match only if both sides have no authors;
        # otherwise the authors-comparator already flags the disagreement and
        # we don't double-count here.
        return not (h_map or a_map)
    for name in shared:
        if _author_rases(h_map[name]) != _author_rases(a_map[name]):
            return False
    return True


def corresponding_match(human_authors: list[dict], ai_authors: list[dict]) -> bool:
    h_map = _name_to_author(human_authors)
    a_map = _name_to_author(ai_authors)
    shared = h_map.keys() & a_map.keys()
    if not shared:
        return not (h_map or a_map)
    for name in shared:
        if _author_corresponding(h_map[name]) != _author_corresponding(a_map[name]):
            return False
    return True


def abstract_match(human: str | None, ai: str | None, threshold: float = ABSTRACT_THRESHOLD) -> bool:
    h = normalize_absent(human)
    a = normalize_absent(ai)
    if not h and not a:
        return True
    if not h or not a:
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


# ---- diff loop -------------------------------------------------------------

def diff(human: dict[str, dict], ai: dict[str, dict]) -> tuple[dict, list[dict]]:
    fields = ["authors", "rases", "corresponding", "abstract", "pdf_url"]
    counts = {f: 0 for f in fields}
    overall_match = 0
    disagreements: list[dict] = []

    shared_dois = sorted(human.keys() & ai.keys())
    for doi in shared_dois:
        h = human[doi]
        a = ai[doi]
        per_field = {
            "authors": authors_match(h["authors"], a["authors"]),
            "rases": rases_match(h["authors"], a["authors"]),
            "corresponding": corresponding_match(h["authors"], a["authors"]),
            "abstract": abstract_match(h["abstract"], a["abstract"]),
            "pdf_url": pdf_url_match(h["pdf_url"], a["pdf_url"]),
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
    args = parser.parse_args(argv)

    human = _load_human(args.human)
    ai = _load_ai(args.ai)
    summary, disagreements = diff(human, ai)

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
