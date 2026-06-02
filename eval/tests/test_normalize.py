import pytest

from parseland_eval.score.normalize import (
    canonicalize_url,
    normalize_alpha,
    normalize_doi,
    normalize_text,
    strip_diacritics,
)


class TestNormalizeText:
    def test_empty(self) -> None:
        assert normalize_text("") == ""
        assert normalize_text(None) == ""

    def test_diacritics_folded(self) -> None:
        # unidecode maps ö→o, ß→ss (not oe/ss), é→e
        assert normalize_text("Cédric") == normalize_text("Cedric") == "cedric"
        assert normalize_text("Mößbauer") == "mossbauer"
        assert normalize_text("Straße") == normalize_text("strasse") == "strasse"

    def test_case_folded(self) -> None:
        assert normalize_text("ABC") == normalize_text("abc") == "abc"

    def test_whitespace_collapsed(self) -> None:
        assert normalize_text("  hello   world  ") == "hello world"


class TestNormalizeAlpha:
    def test_strips_punctuation(self) -> None:
        assert normalize_alpha("Smith, John-Paul!") == "smith john paul"


class TestCanonicalizeUrl:
    def test_empty(self) -> None:
        assert canonicalize_url("") == ""
        assert canonicalize_url(None) == ""

    def test_lowercases_host(self) -> None:
        assert canonicalize_url("HTTPS://Example.COM/path") == "https://example.com/path"

    def test_strips_www(self) -> None:
        assert canonicalize_url("https://www.example.com/") == "https://example.com/"

    def test_strips_tracking_params(self) -> None:
        u = canonicalize_url("https://x.com/a?utm_source=foo&keep=1")
        assert u == "https://x.com/a?keep=1"

    def test_strips_lww_trckng_src_pg(self) -> None:
        # journals.lww.com downloadpdf.aspx: trckng_src_pg varies (Other /
        # ArticleViewer / absent) for the same article; the an= param is the
        # stable resource identifier. The two variants must canonicalize equal.
        base = (
            "https://journals.lww.com/j/_layouts/15/oaks.journals/"
            "downloadpdf.aspx?trckng_src_pg={src}&an=00005131-199004030-00010"
        )
        other = canonicalize_url(base.format(src="Other"))
        article_viewer = canonicalize_url(base.format(src="ArticleViewer"))
        assert other == article_viewer
        assert "trckng_src_pg" not in other
        assert "an=00005131-199004030-00010" in other

    def test_lww_downloadpdf_keeps_only_an(self) -> None:
        # journals.lww.com downloadpdf.aspx: only an= identifies the PDF. The
        # page/parser emits the correct "trckng_src_pg"; gold rows carry varied
        # and even corrupted tracking-param names for the same article. All
        # must canonicalize to the same an=-only URL.
        page = canonicalize_url(
            "https://journals.lww.com/j/_layouts/15/oaks.journals/"
            "downloadpdf.aspx?trckng_src_pg=Other&an=00005373-199506000-00005"
        )
        variants = [
            # missing 'n'
            "downloadpdf.aspx?trcking_src_pg=ArticleViewer&an=00005373-199506000-00005",
            # missing underscore
            "downloadpdf.aspx?trckngsrc_pg=ArticleViewer&an=00005373-199506000-00005",
            # stray non-ASCII char injected mid-name (real gold corruption)
            "downloadpdf.aspx?trcknג_src_pg=ArticleViewer&an=00005373-199506000-00005",
            # no tracking param at all
            "downloadpdf.aspx?an=00005373-199506000-00005",
        ]
        for v in variants:
            got = canonicalize_url(
                "https://journals.lww.com/j/_layouts/15/oaks.journals/" + v
            )
            assert got == page, v
        assert "an=00005373-199506000-00005" in page
        assert "src_pg" not in page

    def test_lww_an_only_does_not_affect_other_hosts(self) -> None:
        # The an=-only rule is scoped to journals.lww.com downloadpdf.aspx.
        u = canonicalize_url("https://example.com/x?an=1&keep=2")
        assert "keep=2" in u and "an=1" in u

    def test_acs_epdf_pdf_and_ref_equivalence(self) -> None:
        # pubs.acs.org: /doi/epdf/ == /doi/pdf/ (same resource) and
        # ?ref=article_openPDF is a tracking param. All forms collapse equal.
        canon = "https://pubs.acs.org/doi/pdf/10.1021/ja076898a"
        forms = [
            "https://pubs.acs.org/doi/pdf/10.1021/ja076898a",
            "https://pubs.acs.org/doi/epdf/10.1021/ja076898a",
            "https://pubs.acs.org/doi/pdf/10.1021/ja076898a?ref=article_openPDF",
            "https://pubs.acs.org/doi/epdf/10.1021/ja076898a?ref=article_openPDF",
        ]
        for f in forms:
            assert canonicalize_url(f) == canon, f

    def test_acs_rule_scoped_to_pubs_acs_org(self) -> None:
        # /doi/epdf/ on a non-ACS host must NOT be rewritten, and a non-ACS
        # ref param is preserved.
        u = canonicalize_url("https://example.com/doi/epdf/10.1/x?ref=keep")
        assert "/doi/epdf/" in u and "ref=keep" in u


class TestNormalizeDoi:
    def test_strips_scheme(self) -> None:
        assert normalize_doi("https://doi.org/10.1/Abc") == "10.1/abc"

    def test_strips_doi_prefix(self) -> None:
        assert normalize_doi("DOI:10.5/XYZ") == "10.5/xyz"

    def test_lowercases(self) -> None:
        assert normalize_doi("10.1/ABC") == "10.1/abc"
