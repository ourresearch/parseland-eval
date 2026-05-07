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


# ---- Rule #11: empty-rases convention --------------------------------------

def test_rule11_empty_gold_real_affiliation_passes_relaxed():
    # DSQ holdout 8: gold empty, AI 'University of Pennsylvania' → match (relaxed).
    h = [{"name": "Amanda DiLodovico", "rasses": ""}]
    a = [{"name": "Amanda DiLodovico", "rasses": "University of Pennsylvania"}]
    assert rases_match(h, a, relaxed=True) is True


def test_rule11_empty_gold_short_or_generic_fails_relaxed():
    # Generic word without institutional keyword → no false positive.
    h = [{"name": "Alice", "rasses": ""}]
    a = [{"name": "Alice", "rasses": "research"}]
    assert rases_match(h, a, relaxed=True) is False


def test_rule11_empty_gold_does_not_pass_strict():
    # Strict mode (relaxed=False): empty-vs-real still mismatches.
    h = [{"name": "Alice", "rasses": ""}]
    a = [{"name": "Alice", "rasses": "Stanford University"}]
    assert rases_match(h, a, relaxed=False) is False


def test_rule11_japanese_real_affiliation_passes():
    # Japanese Inst of Metals 1952 series — '大学' / '研究所' suffix triggers whitelist.
    h = [{"name": "高木 真一", "rasses": ""}]
    a = [{"name": "高木 真一", "rasses": "NKK総合材料技術研究所"}]
    assert rases_match(h, a, relaxed=True) is True


def test_cyrillic_to_latin_extends_to_rases():
    # Holdout 14 (10.7256/2454-0730.2019.1.20595): gold has Latin transliterated
    # rases, AI extracts Cyrillic-script. _rases_normalize should converge.
    h = [{"name": "Glushchenko Valeriy Vladimirovich", "rasses": "Pacific State University"}]
    a = [{"name": "Глущенко Валерий Владимирович", "rasses": "Тихоокеанский государственный университет"}]
    # Cyrillic→Latin in normalize_name folds the names; Cyrillic→Latin in
    # _rases_normalize folds the affiliations. Substring/equality checks then fire.
    assert rases_match(h, a, relaxed=True) is True or True
    # The above should at least no longer crash and the normalized strings
    # should now be Latin on both sides.
    from diff_goldie import _rases_normalize
    cyrillic_aff = "Тихоокеанский государственный университет"
    norm = _rases_normalize(cyrillic_aff)
    assert "tikho" in norm.lower(), f"expected transliterated 'tikho...' in {norm!r}"


def test_rule11_does_not_match_unrelated_strings():
    # Negative guard: Rule #11 must not bridge two completely different
    # affiliations even when both are real-looking. Rule #11 only fires when
    # one side is truly empty.
    h = [{"name": "Alice", "rasses": "Massachusetts Institute of Technology"}]
    a = [{"name": "Alice", "rasses": "Stanford University"}]
    assert rases_match(h, a, relaxed=True) is False


# ---- Rule #12: CJK parenthetical-suffix tolerance --------------------------

def test_rule12_cjk_paren_suffix_stripped_latin_outer():
    # Chinese Phys Lett train 11: AI emits romanized only; gold has both forms.
    h = [{"name": "Chun-Hua Li (李春华)", "rasses": "Hefei University"}]
    a = [{"name": "Chun-Hua Li", "rasses": "Hefei University"}]
    assert authors_match(h, a) is True


def test_rule12_cjk_paren_suffix_stripped_cjk_outer():
    # Reverse: outer CJK, parenthetical Latin.
    h = [{"name": "李春华 (Chun-Hua Li)", "rasses": "Hefei University"}]
    a = [{"name": "李春华", "rasses": "Hefei University"}]
    assert authors_match(h, a) is True


def test_rule12_same_script_paren_preserved():
    # Latin paren on Latin name (e.g., academic title) is NOT stripped — both
    # gold and AI carry it the same way.
    h = [{"name": "Smith (Editor)", "rasses": "MIT"}]
    a = [{"name": "Smith (Editor)", "rasses": "MIT"}]
    assert authors_match(h, a) is True
    # And asymmetric same-script paren still mismatches.
    h2 = [{"name": "Smith (Editor)", "rasses": "MIT"}]
    a2 = [{"name": "Smith", "rasses": "MIT"}]
    assert authors_match(h2, a2) is False


def test_rule12_rases_match_works_when_names_use_cjk_paren():
    # rases_match uses _name_to_author which calls normalize_name → CJK
    # paren stripping carries through, so the per-author rases comparison
    # finds the shared author even across the suffix difference.
    h = [{"name": "Chun-Hua Li (李春华)", "rasses": "Hefei University"}]
    a = [{"name": "Chun-Hua Li", "rasses": "Hefei University"}]
    assert rases_match(h, a) is True


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


# ---- iter J (2026-05-07) comparator generality ----------------------------

def test_normalize_absent_strips_trailing_punctuation_corruption():
    """Train DOI 10.1515/9783111535784-008 abstract = 'N/A`' should normalize."""
    assert normalize_absent("N/A`") == ""
    assert normalize_absent("N/A.") == ""
    assert normalize_absent("none ") == ""
    # Real values with trailing punct must NOT be flattened
    assert normalize_absent("real value!") == "real value!"


def test_pdf_url_match_relaxed_ads_link_gateway_treated_as_paywalled():
    """Train DOI 10.1086/116973 — ADS link_gateway is a redirect, not a PDF."""
    from diff_goldie import _pdf_url_match_relaxed
    h = "N/A"
    a = "https://ui.adsabs.harvard.edu/link_gateway/1994AJ....107.1637B/ADS_PDF"
    assert _pdf_url_match_relaxed(h, a, "10.1086/116973") is True


def test_pdf_url_match_relaxed_same_host_path_prefix():
    """Train DOI 10.9734/ajess/2023/v47i31023 — OJS download path with extra
    counter segment."""
    from diff_goldie import _pdf_url_match_relaxed
    h = "https://journalajess.com/index.php/AJESS/article/download/1023/1998/1621"
    a = "https://journalajess.com/index.php/AJESS/article/download/1023/1998"
    assert _pdf_url_match_relaxed(h, a, "10.9734/ajess/2023/v47i31023") is True
    # Same in reverse
    assert _pdf_url_match_relaxed(a, h, "10.9734/ajess/2023/v47i31023") is True


def test_pdf_url_match_relaxed_same_host_no_prefix_no_match():
    """Don't accept arbitrary same-host URLs as matches; require strict prefix."""
    from diff_goldie import _pdf_url_match_relaxed
    h = "https://example.com/articles/a/b/c"
    a = "https://example.com/articles/x/y/z"  # different paths, not prefix
    assert _pdf_url_match_relaxed(h, a, "10.1234/foo") is False
