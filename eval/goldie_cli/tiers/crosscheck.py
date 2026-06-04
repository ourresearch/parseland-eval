"""Write-isolated parser cross-check (targeting ONLY).

Per the hard constraint, parseland-lib parser *output* must never set a gold value. This
helper exists solely to TARGET effort: it compares the AI gold row against an optional
parser output and emits a ``CrossCheck(confidence, route)`` — nothing more. It is a frozen
dataclass with no row reference and no setter, so by construction it cannot write gold.
The parser output is *injected* (parseland-lib is not invoked here), keeping this pure,
offline, and decoupled from the to-be-deprecated parser scripts.

Routes: ``skip_livefetch`` (AI complete / agrees) · ``livefetch`` (AI empty) ·
``human_audit`` (AI and parser disagree on a populated field).
"""
from __future__ import annotations

from dataclasses import dataclass

from ._util import is_empty, is_empty_authors


@dataclass(frozen=True)
class CrossCheck:
    confidence: float
    route: str


def _norm(s) -> str:
    return " ".join(str(s or "").split()).strip().lower()


def crosscheck(ai_row: dict, parser_output: dict | None = None) -> CrossCheck:
    """Emit a (confidence, route) signal. Reads only; never mutates ``ai_row``."""
    ai_empty = (
        is_empty_authors(ai_row.get("Authors"))
        or is_empty(ai_row.get("Abstract"))
        or is_empty(ai_row.get("PDF URL"))
    )
    if ai_empty:
        return CrossCheck(confidence=0.3, route="livefetch")
    if not parser_output:
        return CrossCheck(confidence=0.6, route="skip_livefetch")
    # Both populated: compare abstract + pdf as cheap agreement proxies.
    disagree = False
    for field in ("Abstract", "PDF URL"):
        a, p = _norm(ai_row.get(field)), _norm(parser_output.get(field))
        if a and p and a != p and a not in p and p not in a:
            disagree = True
            break
    if disagree:
        return CrossCheck(confidence=0.4, route="human_audit")
    return CrossCheck(confidence=0.9, route="skip_livefetch")
