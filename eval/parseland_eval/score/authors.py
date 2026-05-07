"""Order-insensitive author matching via bipartite assignment.

Match key: (last_name_normalized, first_initial). Tie-breaking uses rapidfuzz
token_set_ratio over the full normalized name.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from nameparser import HumanName  # type: ignore[import-untyped]
from rapidfuzz import fuzz  # type: ignore[import-untyped]

from parseland_eval.score.normalize import normalize_alpha


@dataclass(frozen=True)
class AuthorMatch:
    gold_index: int
    parsed_index: int
    key_match: bool         # strict match on (last, first-initial)
    name_ratio: float       # 0-100 token_set_ratio on normalized full name


@dataclass(frozen=True)
class AuthorResult:
    matched: tuple[AuthorMatch, ...]
    gold_unmatched: tuple[int, ...]
    parsed_unmatched: tuple[int, ...]
    precision: float
    recall: float
    f1: float
    precision_soft: float
    recall_soft: float
    f1_soft: float


@dataclass(frozen=True)
class CorrespondingResult:
    """Per-row precision / recall / F1 over the corresponding-author flag.

    Counted over matched author pairs (output of `score_authors`):
      - tp: gold marks CA, parsed marks CA on the same matched author pair
      - fp: parsed marks CA where gold does not (on a matched pair, or any
            parsed author marked CA whose gold counterpart is missing —
            those become fp because we can't verify the truth)
      - fn: gold marks CA where parsed does not (on a matched pair, or any
            gold CA whose parsed counterpart is missing)

    Rule #15 (2026-05-06). Activated default-on; baseline-shift call-out
    in RESULTS.md per the audit-cycle plan.
    """
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    gold_total_ca: int
    parsed_total_ca: int


def _name_key(name: str) -> tuple[str, str]:
    if not name:
        return ("", "")
    parsed = HumanName(name)
    last = normalize_alpha(parsed.last) or normalize_alpha(name.split()[-1] if name.split() else "")
    first = normalize_alpha(parsed.first) or (normalize_alpha(name.split()[0]) if name.split() else "")
    initial = first[:1]
    return (last, initial)


def _name_full(name: str) -> str:
    return normalize_alpha(name)


def _extract_names(authors: Iterable[Any]) -> list[str]:
    names: list[str] = []
    for a in authors:
        if isinstance(a, dict):
            names.append(str(a.get("name") or "").strip())
        else:
            names.append(str(getattr(a, "name", "") or "").strip())
    return names


def _f1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def score_authors(
    gold_authors: Iterable[Any],
    parsed_authors: Iterable[Any],
    *,
    soft_threshold: float = 85.0,
) -> AuthorResult:
    gold_names = _extract_names(gold_authors)
    parsed_names = _extract_names(parsed_authors)

    gold_keys = [_name_key(n) for n in gold_names]
    parsed_keys = [_name_key(n) for n in parsed_names]

    # Build all candidate pairs with a score; greedy-assign best first.
    candidates: list[tuple[float, int, int, bool]] = []
    for gi, (gkey, gname) in enumerate(zip(gold_keys, gold_names)):
        if not gname:
            continue
        for pi, (pkey, pname) in enumerate(zip(parsed_keys, parsed_names)):
            if not pname:
                continue
            key_match = bool(gkey[0]) and gkey == pkey
            ratio = fuzz.token_set_ratio(_name_full(gname), _name_full(pname))
            # Boost strict-key hits above ratio ties.
            score = ratio + (1000 if key_match else 0)
            candidates.append((score, gi, pi, key_match))

    candidates.sort(reverse=True)
    used_g: set[int] = set()
    used_p: set[int] = set()
    matched: list[AuthorMatch] = []
    for score, gi, pi, key_match in candidates:
        if gi in used_g or pi in used_p:
            continue
        ratio = score - (1000 if key_match else 0)
        # Reject weak non-key matches.
        if not key_match and ratio < soft_threshold:
            continue
        matched.append(AuthorMatch(gi, pi, key_match, float(ratio)))
        used_g.add(gi)
        used_p.add(pi)

    total_g = sum(1 for n in gold_names if n)
    total_p = sum(1 for n in parsed_names if n)

    strict_tp = sum(1 for m in matched if m.key_match)
    soft_tp = len(matched)

    p_strict, r_strict, f_strict = _f1(strict_tp, max(0, total_p - strict_tp), max(0, total_g - strict_tp))
    p_soft, r_soft, f_soft = _f1(soft_tp, max(0, total_p - soft_tp), max(0, total_g - soft_tp))

    return AuthorResult(
        matched=tuple(matched),
        gold_unmatched=tuple(i for i in range(len(gold_names)) if i not in used_g and gold_names[i]),
        parsed_unmatched=tuple(i for i in range(len(parsed_names)) if i not in used_p and parsed_names[i]),
        precision=p_strict,
        recall=r_strict,
        f1=f_strict,
        precision_soft=p_soft,
        recall_soft=r_soft,
        f1_soft=f_soft,
    )


def _is_corresponding(author: Any) -> bool:
    """Read the corresponding-author flag from gold or parsed author objects.
    Tolerates both ``is_corresponding`` and ``corresponding_author`` keys
    so the same helper works for GoldAuthor instances (attribute access)
    and parsed dict authors."""
    if author is None:
        return False
    if isinstance(author, dict):
        v = author.get("corresponding_author")
        if v is None:
            v = author.get("is_corresponding")
    else:
        v = getattr(author, "is_corresponding", None)
        if v is None:
            v = getattr(author, "corresponding_author", None)
    return bool(v)


def score_corresponding(
    gold_authors: list[Any],
    parsed_authors: list[Any],
    matched: tuple[AuthorMatch, ...],
) -> CorrespondingResult:
    """Score the corresponding-author flag using the matched author pairs.

    Rule #15 (2026-05-06): activated default-on. Per the 2026-05-06 audit,
    Stroke / NMJI / Russian-Perm / masharif / old-Elsevier-OUP-redirect
    disagreements all hinge on the CA flag; without scoring them they were
    invisible. Headline overall % may appear to drop one cycle as
    previously-silent CA mismatches start counting; this is a measurement
    visibility change, not a regression. Documented in RESULTS.md.
    """
    matched_g = {m.gold_index for m in matched}
    matched_p = {m.parsed_index for m in matched}

    gold_ca_total = sum(1 for a in gold_authors if _is_corresponding(a))
    parsed_ca_total = sum(1 for a in parsed_authors if _is_corresponding(a))

    tp = fp = fn = 0
    for m in matched:
        g_ca = _is_corresponding(gold_authors[m.gold_index]) if m.gold_index < len(gold_authors) else False
        p_ca = _is_corresponding(parsed_authors[m.parsed_index]) if m.parsed_index < len(parsed_authors) else False
        if g_ca and p_ca:
            tp += 1
        elif p_ca and not g_ca:
            fp += 1
        elif g_ca and not p_ca:
            fn += 1
    # Unmatched-author CA flags also count: a gold CA whose author wasn't
    # matched in parsed is a recall miss (fn); a parsed CA whose author
    # wasn't matched in gold is a precision miss (fp).
    for gi, a in enumerate(gold_authors):
        if gi not in matched_g and _is_corresponding(a):
            fn += 1
    for pi, a in enumerate(parsed_authors):
        if pi not in matched_p and _is_corresponding(a):
            fp += 1

    p, r, f = _f1(tp, fp, fn)
    return CorrespondingResult(
        tp=tp, fp=fp, fn=fn,
        precision=p, recall=r, f1=f,
        gold_total_ca=gold_ca_total,
        parsed_total_ca=parsed_ca_total,
    )
