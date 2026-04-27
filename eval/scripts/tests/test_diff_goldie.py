"""Unit tests for diff_goldie.py field comparators."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diff_goldie import (  # noqa: E402
    abstract_match,
    authors_match,
    canonicalize_url,
    corresponding_match,
    normalize_absent,
    normalize_name,
    pdf_url_match,
    rases_match,
)


# ---- normalize_name --------------------------------------------------------

def test_normalize_name_collapses_punct_and_case():
    assert normalize_name("Smith, J.") == "smith j"
    assert normalize_name("O'Brien-Jones") == "o brien jones"


def test_normalize_absent_handles_goldie_sentinels():
    assert normalize_absent("N/A") == ""
    assert normalize_absent(" na ") == ""
    assert normalize_absent(None) == ""
    assert normalize_absent("real value") == "real value"


# ---- authors_match (3 cases) ----------------------------------------------

def test_authors_match_order_insensitive():
    h = [{"name": "Alice"}, {"name": "Bob"}]
    a = [{"name": "Bob"}, {"name": "Alice"}]
    assert authors_match(h, a) is True


def test_authors_disagree_when_set_differs():
    h = [{"name": "Alice"}, {"name": "Bob"}]
    a = [{"name": "Alice"}]
    assert authors_match(h, a) is False


def test_authors_match_punctuation_normalization():
    h = [{"name": "Smith, J."}]
    a = [{"name": "smith j"}]
    assert authors_match(h, a) is True


def test_authors_match_both_empty():
    assert authors_match([], []) is True


# ---- rases_match (3 cases) ------------------------------------------------

def test_rases_match_when_strings_equal_after_strip():
    h = [{"name": "Alice", "rasses": "MIT"}]
    a = [{"name": "Alice", "rasses": "  MIT  "}]
    assert rases_match(h, a) is True


def test_rases_disagree_on_different_strings():
    h = [{"name": "Alice", "rasses": "MIT"}]
    a = [{"name": "Alice", "rasses": "Stanford"}]
    assert rases_match(h, a) is False


def test_rases_tolerates_affiliations_alias_as_list():
    # AI v0 emits `affiliations` (list); human emits `rasses` (string).
    h = [{"name": "Alice", "rasses": "MIT | Harvard"}]
    a = [{"name": "Alice", "affiliations": ["MIT", "Harvard"]}]
    assert rases_match(h, a) is True


def test_rases_disjoint_author_sets_is_miss():
    # If author sets are wholly disjoint, there are zero per-author rases
    # comparisons available. Calling that a match would be misleading —
    # the rases ARE different (Alice@MIT vs Bob@Stanford).
    h = [{"name": "Alice", "rasses": "MIT"}]
    a = [{"name": "Bob", "rasses": "Stanford"}]
    assert rases_match(h, a) is False


def test_rases_both_empty_is_match():
    assert rases_match([], []) is True


# ---- corresponding_match (3 cases) ----------------------------------------

def test_corresponding_match_when_flags_agree():
    h = [{"name": "Alice", "corresponding_author": True}]
    a = [{"name": "Alice", "corresponding_author": True}]
    assert corresponding_match(h, a) is True


def test_corresponding_disagree_when_flags_differ():
    h = [{"name": "Alice", "corresponding_author": True}]
    a = [{"name": "Alice", "corresponding_author": False}]
    assert corresponding_match(h, a) is False


def test_corresponding_handles_missing_flag_consistently():
    # AI side has no flag at all; human is True. Treat None != True as miss.
    h = [{"name": "Alice", "corresponding_author": True}]
    a = [{"name": "Alice"}]
    assert corresponding_match(h, a) is False


# ---- abstract_match (3 cases) ---------------------------------------------

def test_abstract_match_above_threshold():
    h = "The cat sat on the mat and looked content."
    a = "The cat sat on the mat and looked content!"
    assert abstract_match(h, a) is True


def test_abstract_disagree_below_threshold():
    h = "The cat sat on the mat."
    a = "Quantum chromodynamics governs the strong nuclear force."
    assert abstract_match(h, a) is False


def test_abstract_both_empty_is_match():
    assert abstract_match("", "") is True
    assert abstract_match(None, None) is True
    assert abstract_match("N/A", "") is True


def test_abstract_one_empty_is_miss():
    assert abstract_match("non-empty", "") is False
    assert abstract_match("", "non-empty") is False


def test_abstract_threshold_boundary():
    # Construct a custom threshold check to confirm >= semantics.
    h = "abcdefghij"
    a = "abcdefghij"
    assert abstract_match(h, a, threshold=1.0) is True
    assert abstract_match("abcdefghij", "abcdefghik", threshold=1.0) is False


# ---- pdf_url_match (3 cases) ----------------------------------------------

def test_pdf_url_canonicalization_strips_query_and_trailing_slash():
    h = "https://example.com/paper.pdf"
    a = "https://EXAMPLE.com/paper.pdf?utm_source=foo&x=1"
    assert pdf_url_match(h, a) is True


def test_pdf_url_disagree_on_different_paths():
    h = "https://example.com/paper-A.pdf"
    a = "https://example.com/paper-B.pdf"
    assert pdf_url_match(h, a) is False


def test_pdf_url_both_empty_is_match():
    assert pdf_url_match("", "") is True
    assert pdf_url_match(None, None) is True
    assert pdf_url_match("N/A", "") is True


def test_pdf_url_one_empty_is_miss():
    assert pdf_url_match("https://example.com/paper.pdf", "") is False
    assert pdf_url_match("https://example.com/paper.pdf", "N/A") is False


def test_canonicalize_url_drops_fragment_and_lowers_host():
    assert canonicalize_url("HTTPS://Example.COM/Foo/#section") == "https://example.com/Foo"


def test_canonicalize_url_treats_na_as_absent():
    assert canonicalize_url("N/A") == ""
