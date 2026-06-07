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

    def test_wiley_pdfdirect_pdf_equivalence(self) -> None:
        # onlinelibrary.wiley.com: /doi/pdfdirect/ and /doi/pdf/ both serve
        # the same article PDF. parseland's transform_pdf_url deliberately
        # rewrites /pdf/ -> /pdfdirect/, but merged-FINAL.csv gold uses
        # /pdf/ — they must canonicalize equal.
        canon = "https://onlinelibrary.wiley.com/doi/pdf/10.1002/chin.198035056"
        forms = [
            "https://onlinelibrary.wiley.com/doi/pdf/10.1002/chin.198035056",
            "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/chin.198035056",
            "https://onlinelibrary.wiley.com/doi/epdf/10.1002/chin.198035056",
        ]
        for f in forms:
            assert canonicalize_url(f) == canon, f

    def test_wiley_percent_encoded_doi_path_equivalence(self) -> None:
        # Wiley DOI paths may appear percent-encoded in parser output and
        # decoded in gold. The DOI identifies the same PDF resource.
        decoded = (
            "https://onlinelibrary.wiley.com/doi/pdf/"
            "10.1002/(SICI)1096-8628(19970414)69:4<400::AID-AJMG12>3.0.CO;2-R"
        )
        encoded = (
            "https://onlinelibrary.wiley.com/doi/pdfdirect/"
            "10.1002/%28SICI%291096-8628%2819970414%2969%3A4%3C400%3A%3AAID-AJMG12%3E3.0.CO%3B2-R"
        )
        assert canonicalize_url(decoded) == canonicalize_url(encoded)

    def test_wiley_subdomain_pdf_equivalence(self) -> None:
        # Wiley hosts branded subdomains such as agupubs and acsess under the
        # same Online Library PDF path family. Subdomain drift should not make
        # the same DOI PDF fail strict comparison.
        base = "https://onlinelibrary.wiley.com/doi/pdf/10.1029/TE042i004p00408-02"
        branded = "https://agupubs.onlinelibrary.wiley.com/doi/pdfdirect/10.1029/TE042i004p00408-02"
        assert canonicalize_url(base) == canonicalize_url(branded)

    def test_wiley_download_param_dropped(self) -> None:
        # Wiley appends download=true on some PDF anchors; it is viewer state,
        # not part of the PDF identity.
        base = "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1111/nan.12654"
        download = base + "?download=true"
        assert canonicalize_url(base) == canonicalize_url(download)

    def test_wiley_rule_scoped_to_onlinelibrary_wiley_com(self) -> None:
        # /doi/pdfdirect/ on a non-Wiley host must NOT be rewritten.
        u = canonicalize_url("https://example.com/doi/pdfdirect/10.1/foo")
        assert "/doi/pdfdirect/" in u
        v = canonicalize_url("https://example.com/doi/epdf/10.1/foo")
        assert "/doi/epdf/" in v
        w = canonicalize_url("https://example.com/x?download=true")
        assert "download=true" in w

    def test_wiley_preserves_different_dois(self) -> None:
        # Distinct DOIs must remain distinct after canonicalization, even
        # under the same Wiley equivalence rule.
        a = canonicalize_url("https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/foo")
        b = canonicalize_url("https://onlinelibrary.wiley.com/doi/pdf/10.1002/bar")
        assert a != b

    def test_jid_legacy_pii_hyphen_equivalence(self) -> None:
        # JID legacy URLs appear with and without the hyphen inside the
        # PII-like article id; both resolve to the same article PDF path.
        gold = "http://www.jidonline.org/article/S0022-202X15321138/pdf"
        parsed = "http://www.jidonline.org/article/S0022202X15321138/pdf"
        assert canonicalize_url(gold) == canonicalize_url(parsed)

    def test_jid_hyphen_rule_scoped(self) -> None:
        # Do not rewrite the same path shape on unrelated hosts.
        gold = canonicalize_url("https://example.com/article/S0022-202X15321138/pdf")
        parsed = canonicalize_url("https://example.com/article/S0022202X15321138/pdf")
        assert gold != parsed

    def test_essoar_pdfjs_http_https_equivalence(self) -> None:
        # ESS Open Archive pdfjs URLs redirect between http and https while
        # identifying the same DOI PDF.
        https_url = "https://www.essoar.org/pdfjs/10.1002/essoar.10504659.1"
        http_url = "http://www.essoar.org/pdfjs/10.1002/essoar.10504659.1"
        assert canonicalize_url(https_url) == canonicalize_url(http_url)

    def test_essoar_scheme_rule_scoped_to_pdfjs(self) -> None:
        a = canonicalize_url("http://www.essoar.org/other/10.1002/essoar.10504659.1")
        b = canonicalize_url("https://www.essoar.org/other/10.1002/essoar.10504659.1")
        assert a != b

    def test_sage_download_param_dropped(self) -> None:
        # SAGE page anchor: /doi/pdf/X?download=true. Gold drops the param.
        canon = "https://journals.sagepub.com/doi/pdf/10.1177/0021934716658862"
        forms = [
            "https://journals.sagepub.com/doi/pdf/10.1177/0021934716658862",
            "https://journals.sagepub.com/doi/pdf/10.1177/0021934716658862?download=true",
        ]
        for f in forms:
            assert canonicalize_url(f) == canon, f

    def test_sage_rule_scoped(self) -> None:
        # download= on a non-SAGE host is preserved.
        u = canonicalize_url("https://example.com/x?download=true")
        assert "download=true" in u

    def test_de_gruyter_host_and_license_type_equivalence(self) -> None:
        # De Gruyter document PDFs moved from degruyter.com to
        # degruyterbrill.com. licenseType/stream are viewer-state params, not
        # PDF identity.
        canon = "https://degruyterbrill.com/document/doi/10.1515/9783110779707-fm/pdf"
        forms = [
            "https://www.degruyterbrill.com/document/doi/10.1515/9783110779707-fm/pdf",
            "https://www.degruyter.com/document/doi/10.1515/9783110779707-fm/pdf",
            "https://www.degruyterbrill.com/document/doi/10.1515/9783110779707-fm/pdf?licenseType=free",
            "https://www.degruyter.com/document/doi/10.1515/9783110779707-fm/pdf/firstPage?stream=true",
        ]
        for f in forms:
            assert canonicalize_url(f) == canon, f

    def test_de_gruyter_rule_scoped_to_document_pdf(self) -> None:
        # Do not rewrite De Gruyter non-PDF document routes or unrelated hosts.
        html = canonicalize_url(
            "https://www.degruyter.com/document/doi/10.1515/9783110779707-fm/html"
        )
        assert html == (
            "https://degruyter.com/document/doi/10.1515/9783110779707-fm/html"
        )
        other = canonicalize_url(
            "https://example.com/document/doi/10.1515/9783110779707-fm/pdf?licenseType=free"
        )
        assert "licenseType=free" in other

    def test_brill_downloadpdf_aliases_and_imprints(self) -> None:
        # Brill downloadpdf viewer routes and direct routes identify the same
        # PDF. Fink/Schoeningh imprint hosts share the same Brill path family.
        canon = "https://brill.com/downloadpdf/book/edcoll/9783657789085/BP000012.pdf"
        forms = [
            "https://brill.com/downloadpdf/book/edcoll/9783657789085/BP000012.pdf",
            "https://brill.com/downloadpdf/display/book/edcoll/9783657789085/BP000012.pdf",
            "https://www.schoeningh.de/downloadpdf/book/edcoll/9783657789085/BP000012.pdf",
        ]
        for f in forms:
            assert canonicalize_url(f) == canon, f

        journal = "https://brill.com/downloadpdf/journals/agpt/37/3/article-p550_12.pdf"
        assert canonicalize_url(
            "https://brill.com/downloadpdf/view/journals/agpt/37/3/article-p550_12.pdf"
        ) == journal

        chapter = "https://brill.com/downloadpdf/book/9789004509429/BP000038.pdf"
        assert canonicalize_url(
            "https://brill.com/downloadpdf/edcollchap/book/9789004509429/BP000038.pdf"
        ) == chapter

    def test_brill_rule_scoped_to_downloadpdf_hosts(self) -> None:
        # Do not rewrite similar routes on unrelated hosts or non-downloadpdf
        # pages on Brill imprint hosts.
        other = canonicalize_url(
            "https://example.com/downloadpdf/display/book/9783657789085/BP000012.pdf"
        )
        assert "/downloadpdf/display/" in other
        html = canonicalize_url("https://www.fink.de/document/doi/10.30965/x/html")
        assert html == "https://fink.de/document/doi/10.30965/x/html"

    def test_bmj_main_site_content_bmj_pdf_equivalence(self) -> None:
        # BMJ's main site accepts PDF paths with and without the repeated
        # journal segment after /content/. Gold and parser output differ on
        # this stable alias only.
        canon = "https://bmj.com/content/331/7530/1427.full.pdf"
        forms = [
            "https://www.bmj.com/content/331/7530/1427.full.pdf",
            "https://www.bmj.com/content/bmj/331/7530/1427.full.pdf",
        ]
        for f in forms:
            assert canonicalize_url(f) == canon, f

    def test_bmj_content_bmj_rule_scoped(self) -> None:
        # Specialty BMJ journal subdomains keep their journal path segment, and
        # unrelated hosts must not be rewritten.
        specialty = canonicalize_url(
            "https://emj.bmj.com/content/emermed/32/1/85.1.full.pdf"
        )
        assert "/content/emermed/" in specialty
        other = canonicalize_url(
            "https://example.com/content/bmj/331/7530/1427.full.pdf"
        )
        assert "/content/bmj/" in other

    def test_vr_elibrary_reader_pdf_equivalence(self) -> None:
        # VR eLibrary's reader route is the viewer wrapper for the same DOI PDF
        # emitted at /doi/pdf/<doi>?download=true.
        canon = "https://vr-elibrary.de/doi/pdf/10.14220/9783737010900.119"
        forms = [
            "https://www.vr-elibrary.de/doi/reader/10.14220/9783737010900.119",
            "https://www.vr-elibrary.de/doi/pdf/10.14220/9783737010900.119?download=true",
        ]
        for f in forms:
            assert canonicalize_url(f) == canon, f

    def test_vr_elibrary_rule_scoped(self) -> None:
        # /doi/reader/ and download= remain untouched on unrelated hosts.
        u = canonicalize_url("https://example.com/doi/reader/10.14220/x?download=true")
        assert "/doi/reader/" in u and "download=true" in u

    def test_aha_download_param_dropped(self) -> None:
        # AHA page anchors append download=true to the same DOI PDF URL that
        # gold records without the viewer-state param.
        canon = "https://ahajournals.org/doi/pdf/10.1161/CIRCOUTCOMES.118.005016"
        forms = [
            "https://www.ahajournals.org/doi/pdf/10.1161/CIRCOUTCOMES.118.005016",
            "https://www.ahajournals.org/doi/pdf/10.1161/CIRCOUTCOMES.118.005016?download=true",
        ]
        for f in forms:
            assert canonicalize_url(f) == canon, f

    def test_aha_rule_scoped_to_ahajournals(self) -> None:
        # download= remains significant on unrelated hosts.
        u = canonicalize_url("https://example.com/doi/pdf/10.1161/x?download=true")
        assert "download=true" in u

    def test_taylor_epdf_pdf_and_needaccess_equivalence(self) -> None:
        # tandfonline.com: /doi/epdf/ == /doi/pdf/; needAccess and role are
        # page-state tracking params, not URL identity.
        canon = "https://tandfonline.com/doi/pdf/10.1080/00046973.1970.9676590"
        forms = [
            "https://www.tandfonline.com/doi/pdf/10.1080/00046973.1970.9676590",
            "https://www.tandfonline.com/doi/epdf/10.1080/00046973.1970.9676590",
            "https://www.tandfonline.com/doi/epdf/10.1080/00046973.1970.9676590?needAccess=true",
            "https://www.tandfonline.com/doi/pdf/10.1080/00046973.1970.9676590?needAccess=true&role=button",
        ]
        for f in forms:
            assert canonicalize_url(f) == canon, f

    def test_taylor_rule_scoped(self) -> None:
        # needAccess on a non-Taylor host is preserved; /doi/epdf/ on a
        # non-Taylor host is preserved.
        u = canonicalize_url("https://example.com/x?needAccess=true")
        assert "needAccess=true" in u
        v = canonicalize_url("https://example.com/doi/epdf/10.1/foo")
        assert "/doi/epdf/" in v


class TestNormalizeDoi:
    def test_strips_scheme(self) -> None:
        assert normalize_doi("https://doi.org/10.1/Abc") == "10.1/abc"

    def test_strips_doi_prefix(self) -> None:
        assert normalize_doi("DOI:10.5/XYZ") == "10.5/xyz"

    def test_lowercases(self) -> None:
        assert normalize_doi("10.1/ABC") == "10.1/abc"
