"""Tier composition over already-extracted tier-1 gold rows.

``run_with_fallback`` takes tier-1 rows and (optionally) an async fallback extractor
(tier-2 live-fetch via local_cdp), re-extracts only the rows with empty fields, merges
the fallback in (gold-aware), then classifies + cleans every row. Backend-agnostic: the
fallback is any ``async (doi, link) -> gold_row | None`` callable, so it tests offline.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from ._util import is_empty, is_empty_authors
from .classify import classify_row
from .cleanup import clean_row
from .merge import merge_rows

FallbackExtract = Callable[[str, str], Awaitable[dict[str, Any] | None]]


def _needs_fallback(row: dict[str, Any]) -> bool:
    return (
        is_empty_authors(row.get("Authors"))
        or is_empty(row.get("Abstract"))
        or is_empty(row.get("PDF URL"))
    )


def _append_label(row: dict[str, Any], label: str | None) -> dict[str, Any]:
    if not label:
        return row
    note = (row.get("Notes") or "").strip()
    row["Notes"] = f"{note} | {label}" if note else label
    return row


async def run_with_fallback(
    rows: list[dict[str, Any]],
    *,
    fallback_extract: FallbackExtract | None = None,
    resolved_urls: dict[str, str] | None = None,
    gold_empty: dict[str, frozenset[str]] | None = None,
    do_cleanup: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return ``(final_rows, stats)``. ``stats`` reports fallback usage + iter-R labels."""
    resolved_urls = resolved_urls or {}
    gold_empty = gold_empty or {}
    final: list[dict[str, Any]] = []
    fallback_used = 0
    labels: dict[str, int] = {}

    for row in rows:
        doi = (row.get("DOI") or "").strip()
        merged = dict(row)
        if fallback_extract is not None and _needs_fallback(merged):
            fb = await fallback_extract(doi, (row.get("Link") or "").strip())
            if fb:
                merged = merge_rows(merged, fb, gold_empty_fields=gold_empty.get(doi, frozenset()))
                fallback_used += 1
        if do_cleanup:
            merged = clean_row(merged)
        label = classify_row(merged, resolved_urls.get(doi))
        if label:
            labels[label] = labels.get(label, 0) + 1
            merged = _append_label(merged, label)
        final.append(merged)

    return final, {"fallback_used": fallback_used, "labels": labels}
