"""Gold-aware delta merge (lifted policy from merge_livefetch.py).

Fill a primary (tier-1) row's empty field from a fallback (tier-2 live-fetch) row ONLY
when: primary is empty AND fallback is non-empty AND gold does not mark the field empty
by convention. Never lose primary content; never replace good with empty; never violate
gold's deliberate-empty convention.
"""
from __future__ import annotations

from typing import Any

from ._util import is_empty, is_empty_authors

_FIELDS = (
    ("Authors", is_empty_authors),
    ("Abstract", is_empty),
    ("PDF URL", is_empty),
)


def merge_rows(
    primary: dict[str, Any],
    fallback: dict[str, Any] | None,
    *,
    gold_empty_fields: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Return a new row: primary with empty fields filled from fallback where allowed."""
    out = dict(primary)
    if not fallback:
        return out
    filled = False
    for field, empty in _FIELDS:
        if field in gold_empty_fields:
            continue  # gold says empty here — preserve the both-empty match
        if empty(out.get(field)) and not empty(fallback.get(field)):
            out[field] = fallback[field]
            filled = True
    if filled and (out.get("Status") or "").upper() != "TRUE":
        out["Status"] = "TRUE"
        out["Notes"] = (out.get("Notes") or "").strip() or "merged from live-fetch"
    return out
