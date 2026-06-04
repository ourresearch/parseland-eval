"""Mojibake cleanup pass (lifted from fix_mojibake.py): ftfy over text fields."""
from __future__ import annotations

import json
from typing import Any

TEXT_FIELDS = ("Abstract", "Notes")


def _fix(text: str) -> str:
    import ftfy
    return ftfy.fix_text(text)


def clean_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return a new row with ftfy applied to Abstract/Notes and to author name/rasses."""
    out = dict(row)
    for f in TEXT_FIELDS:
        v = out.get(f)
        if isinstance(v, str) and v:
            out[f] = _fix(v)
    authors = out.get("Authors")
    if isinstance(authors, str) and authors.strip():
        try:
            parsed = json.loads(authors)
        except json.JSONDecodeError:
            out["Authors"] = _fix(authors)
        else:
            if isinstance(parsed, list):
                for a in parsed:
                    if isinstance(a, dict):
                        for k in ("name", "rasses"):
                            if isinstance(a.get(k), str) and a[k]:
                                a[k] = _fix(a[k])
                out["Authors"] = json.dumps(parsed, ensure_ascii=False)
    return out
