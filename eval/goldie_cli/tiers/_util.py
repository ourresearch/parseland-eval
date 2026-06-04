"""Shared empty/absent predicates (lifted from merge_livefetch.py / iter_r_classify.py)."""
from __future__ import annotations

import json

_ABSENT = {"n/a", "na", "none", "null", "[]", ""}


def is_empty(s: str | None) -> bool:
    """A scalar field is empty if blank or an absent-sentinel."""
    if not s:
        return True
    return s.strip().lower() in _ABSENT


def is_empty_authors(s: str | None) -> bool:
    """Authors is empty if blank/sentinel or a JSON list that's empty."""
    if is_empty(s):
        return True
    try:
        a = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return False
    return not a
