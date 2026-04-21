"""Abstract comparison: Levenshtein ratio + length-ratio sanity check.

We deliberately avoid BLEU (tokenizer-biased per SacreBLEU) and keep the scorer
deterministic and character-based. The length ratio is kept as a side-signal so
truncation bugs are visible even when the ratio is OK (e.g., parsed only the
first paragraph but phrasing matches).
"""
from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz  # type: ignore[import-untyped]

from parseland_eval.score.normalize import normalize_text

# Threshold at which a fuzzy Levenshtein ratio is treated as a binary "match".
# Tuned 2026-04-21 via ``eval/scripts/tune_abstract_threshold.py`` against
# the 100-row live-API baseline. Method: largest-gap midpoint above a
# domain floor of 0.5 (below that, fewer than half the characters align —
# definitionally not the same abstract). On the baseline the gap is
# [0.681, 0.804], midpoint 0.742; bootstrap lower bound also 0.742, so
# the lower edge of the natural "good match" cluster is stable. Re-run
# the tuner after each meaningful gold expansion; the headline to watch
# is whether the midpoint shifts beyond the bootstrap CI.
ABSTRACT_MATCH_THRESHOLD = 0.74


@dataclass(frozen=True)
class AbstractResult:
    strict_match: bool
    soft_ratio: float     # 0-1 on NFKC+casefold-normalized text
    fuzzy_ratio: float    # 0-1 on raw text, rapidfuzz ratio
    length_ratio: float   # parsed length / gold length (1.0 = equal; <1 = truncated)
    present: bool         # did parser return any non-empty abstract?
    match_at_threshold: bool  # fuzzy_ratio >= ABSTRACT_MATCH_THRESHOLD


def _apply_threshold(fuzzy_ratio: float, present: bool, expected_present: bool) -> bool:
    """Binary match decision.

    Both empty → match; asymmetric empty → miss; otherwise ratio-gated.
    """
    if not expected_present and not present:
        return True
    if not expected_present or not present:
        return False
    return fuzzy_ratio >= ABSTRACT_MATCH_THRESHOLD


def score_abstract(gold: str | None, parsed: str | None) -> AbstractResult:
    gold_s = (gold or "").strip()
    parsed_s = (parsed or "").strip()
    present = bool(parsed_s)
    expected_present = bool(gold_s)

    if not gold_s and not parsed_s:
        return AbstractResult(True, 1.0, 1.0, 1.0, present, True)
    if not gold_s or not parsed_s:
        return AbstractResult(False, 0.0, 0.0, 0.0, present, False)

    gold_n = normalize_text(gold_s)
    parsed_n = normalize_text(parsed_s)

    strict = gold_s == parsed_s
    soft = fuzz.ratio(gold_n, parsed_n) / 100.0
    fuzzy = fuzz.ratio(gold_s, parsed_s) / 100.0
    length_ratio = len(parsed_s) / len(gold_s) if gold_s else 0.0

    return AbstractResult(
        strict_match=strict,
        soft_ratio=soft,
        fuzzy_ratio=fuzzy,
        length_ratio=length_ratio,
        present=present,
        match_at_threshold=_apply_threshold(fuzzy, present, expected_present),
    )
