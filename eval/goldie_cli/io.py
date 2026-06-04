"""CSV / row-shaping I/O for goldie.

Atomic writes (``.tmp`` + ``os.replace``) and the author/row normalisation lifted
from ``eval/scripts/extract_batch_cloud.py`` so the produced CSV is byte-compatible
with the existing ``ai-goldie-{N}.csv`` consumers.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .schema import GOLD_COLUMNS


def normalize_author(a: dict[str, Any]) -> dict[str, Any]:
    """Collapse an extractor author dict into the gold author shape.

    Handles three rasses encodings: explicit ``rasses`` string, a ``rasses`` list,
    or a fallback ``affiliations`` list. ``corresponding_author`` accepts the
    ``is_corresponding`` alias. Lifted from extract_batch_cloud.py:223-239.
    """
    name = str(a.get("name") or "").strip()
    rasses = a.get("rasses")
    if isinstance(rasses, list):
        rasses = " | ".join(str(s or "").strip() for s in rasses if str(s or "").strip())
    elif rasses is None:
        affs = a.get("affiliations") or []
        if isinstance(affs, list):
            rasses = " | ".join(str(s or "").strip() for s in affs if str(s or "").strip())
        else:
            rasses = str(affs or "").strip()
    else:
        rasses = str(rasses).strip()
    corr = a.get("corresponding_author")
    if corr is None:
        corr = a.get("is_corresponding")
    return {"name": name, "rasses": rasses or "", "corresponding_author": bool(corr)}


def to_gold_row(
    *,
    no: int,
    doi: str,
    link: str,
    extraction: dict[str, Any] | None,
    error: str | None = None,
) -> dict[str, Any]:
    """Build one 12-column gold row from an extraction dict (or a failure).

    Mirrors extract_batch_cloud.py:242-259. ``Status`` is TRUE only when there is a
    non-empty extraction, no bot-check, and no error.
    """
    e = extraction or {}
    raw_authors = e.get("authors") or []
    authors = [normalize_author(a) for a in raw_authors if isinstance(a, dict)]
    return {
        "No": no,
        "DOI": doi,
        "Link": link,
        "Authors": json.dumps(authors, ensure_ascii=False) if authors else "",
        "Abstract": e.get("abstract") or "",
        "PDF URL": e.get("pdf_url") or "",
        "Status": "TRUE" if (e and not e.get("has_bot_check") and not error) else "FALSE",
        "Notes": e.get("notes") or (error or ""),
        "Has Bot Check": str(bool(e.get("has_bot_check"))).upper() if e else "",
        "Resolves To PDF": str(bool(e.get("resolves_to_pdf"))).upper() if e else "",
        "broken_doi": str(bool(e.get("broken_doi"))).upper() if e else "",
        "no english": str(bool(e.get("no_english"))).upper() if e else "",
    }


def to_transform_dict(extraction: dict[str, Any] | None) -> dict[str, Any]:
    """Convert a backend's lowercase ``ExtractionOut`` dict to the capitalized shape
    the post-LLM transforms operate on: ``{"Authors":[...], "Abstract", "PDF URL"}``.
    Authors are normalised to ``{name, rasses, corresponding_author}``.
    """
    e = extraction or {}
    authors = [normalize_author(a) for a in (e.get("authors") or []) if isinstance(a, dict)]
    return {
        "Authors": authors,
        "Abstract": e.get("abstract") or "",
        "PDF URL": e.get("pdf_url") or "",
    }


def apply_transform_dict(extraction: dict[str, Any], cap: dict[str, Any]) -> None:
    """Write the (possibly transformed) capitalized fields back onto the lowercase
    extraction dict, so ``to_gold_row`` produces the final row."""
    extraction["authors"] = cap.get("Authors") or []
    extraction["abstract"] = cap.get("Abstract") or ""
    extraction["pdf_url"] = cap.get("PDF URL") or ""


def write_csv_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write rows to ``path`` atomically via a sibling ``.tmp`` + rename.

    Lifted from extract_batch_cloud.py:262-270.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GOLD_COLUMNS)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    tmp.replace(path)


def read_source_rows(path: Path) -> list[dict[str, str]]:
    """Read a corpus CSV (``No,DOI,Link,...``) into a list of dict rows."""
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def chunk_batches(rows: list[dict[str, str]], batch_size: int) -> list[tuple[int, list[dict[str, str]]]]:
    """Split rows into ``(batch_no, rows)`` chunks, 1-indexed, preserving order."""
    batches: list[tuple[int, list[dict[str, str]]]] = []
    for i in range(0, len(rows), batch_size):
        batches.append((i // batch_size + 1, rows[i:i + batch_size]))
    return batches
