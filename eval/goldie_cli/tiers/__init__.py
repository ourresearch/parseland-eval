"""Tier composition: tier1 (primary) → tier2 fallback on empties → merge → classify → cleanup.

Plus a write-isolated parser cross-check (targeting only). All operate on the 12-column
gold-row dict; the empty/absent predicates are shared in ``_util``.
"""
from __future__ import annotations

from .classify import classify_row
from .cleanup import clean_row
from .crosscheck import CrossCheck, crosscheck
from .merge import merge_rows
from .tiered import run_with_fallback

__all__ = ["classify_row", "clean_row", "CrossCheck", "crosscheck", "merge_rows", "run_with_fallback"]
