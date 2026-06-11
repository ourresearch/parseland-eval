"""String and URL canonicalization for comparison keys.

Follows UAX#15 guidance: use NFKC+casefold for match keys only; preserve originals
for display. Diacritics are stripped so "Cédric" matches "Cedric".
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from unidecode import unidecode  # type: ignore[import-untyped]

_WHITESPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]+", flags=re.UNICODE)
_EMPTY_STRINGS = {"", "na", "n/a", "null", "[]", "{}"}
_TRACKING_PARAMS = frozenset(
    {
        "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
        "gclid", "fbclid", "mc_cid", "mc_eid",
        # ScienceDirect anti-hotlink session tokens — change per request, do
        # not identify the underlying PDF resource.
        "md5", "pid", "_user", "_origin", "rdoc", "ts",
    }
)


def is_empty_value(value: object) -> bool:
    """Return True for values that should score as an empty field."""
    if value is None:
        return True
    if isinstance(value, str):
        return normalize_text(value) in _EMPTY_STRINGS
    if isinstance(value, Mapping):
        return len(value) == 0
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return len(value) == 0
    return False


def strip_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def normalize_text(text: str | None) -> str:
    """NFKC + unidecode (handles ß, ligatures) + casefold + diacritic fold + whitespace collapse."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = unidecode(t)
    t = t.casefold()
    t = strip_diacritics(t)
    t = _WHITESPACE.sub(" ", t).strip()
    return t


def normalize_alpha(text: str | None) -> str:
    """As normalize_text but also drops punctuation — for fuzzy name/org keys."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = unidecode(t)
    t = t.casefold()
    t = strip_diacritics(t)
    t = _PUNCT.sub(" ", t)
    t = _WHITESPACE.sub(" ", t).strip()
    return t


def canonicalize_url(url: str | None) -> str:
    """Lowercase scheme+host, strip tracking + session params, drop trailing
    slash, and apply publisher-specific path equivalences.

    ScienceDirect serves the same article PDF resource at both
    ``/science/article/pii/<PII>/pdf`` (the canonical landing URL) and
    ``/science/article/pii/<PII>/pdfft?md5=...&pid=...`` (the time-bound,
    anti-hotlink signed variant). Treating these as different URLs in strict
    matching punishes parseland for emitting the clean canonical form when
    gold happened to have the signed form (or vice versa). Equivalent on the
    open web: ``/pdfft`` is just ``/pdf`` with a signing wrapper.
    """
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    scheme = parts.scheme.lower() or "https"
    host = parts.netloc.lower().removeprefix("www.")
    query_pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in _TRACKING_PARAMS]
    path = parts.path.rstrip("/") or "/"
    # Lippincott / Wolters Kluwer (journals.lww.com) downloadpdf.aspx: the an=
    # (article number) is the sole resource identifier. Gold rows carry varied
    # and sometimes corrupted tracking-param names for the same PDF —
    # "trckng_src_pg", the misspelling "trcking_src_pg", a missing-underscore
    # "trckngsrc_pg", even a stray non-ASCII char injected mid-name. None of
    # them identify the PDF, so keep only an= and drop the rest.
    if host.endswith("journals.lww.com") and "downloadpdf.aspx" in path.lower():
        query_pairs = [(k, v) for k, v in query_pairs if k == "an"]
    # ACS (pubs.acs.org): /doi/epdf/ is the enhanced-PDF viewer wrapper for the
    # same resource as /doi/pdf/ (parseland's clean_pdf_url already canonicalizes
    # to /doi/pdf/; gold sometimes keeps /doi/epdf/). "ref=article_openPDF" is a
    # tracking param the viewer appends. Host-scoped so other sites are untouched.
    if host == "pubs.acs.org":
        path = path.replace("/doi/epdf/", "/doi/pdf/")
        query_pairs = [(k, v) for k, v in query_pairs if k != "ref"]
    # Wiley (onlinelibrary.wiley.com): /doi/pdfdirect/, /doi/epdf/, and /doi/pdf/
    # all serve the same article PDF. parseland's transform_pdf_url enforces
    # /pdfdirect/ via deliberate rewrite; tests/fixtures/wiley-gold.ndjson
    # follows that convention. The merged-FINAL.csv 10K corpus uses /doi/pdf/
    # — the more common user-facing form. Without this equivalence, 216 of
    # 221 Wiley pdf-present rows in the corpus mismatch on path alone.
    # Host-scoped so non-Wiley sites are untouched.
    if host == "onlinelibrary.wiley.com" or host.endswith(".onlinelibrary.wiley.com"):
        host = "onlinelibrary.wiley.com"
        path = unquote(path)
        path = path.replace("/doi/pdfdirect/", "/doi/pdf/")
        path = path.replace("/doi/epdf/", "/doi/pdf/")
        query_pairs = [(k, v) for k, v in query_pairs if k != "download"]
    # Journal of Investigative Dermatology legacy links may include or omit
    # the hyphen in the PII-like article id.
    if host == "jidonline.org":
        path = re.sub(r"(/article/S\d{4})-(\d{3}X)", r"\1\2", path, flags=re.I)
    # ESS Open Archive pdfjs links redirect between http and https.
    if host == "essoar.org" and path.startswith("/pdfjs/"):
        scheme = "https"
    # SAGE (journals.sagepub.com): the page emits a /doi/pdf/X anchor with a
    # ?download=true tracking param. Gold drops it. They identify the same PDF.
    if host == "journals.sagepub.com":
        query_pairs = [(k, v) for k, v in query_pairs if k != "download"]
    # De Gruyter rebranded article/chapter document URLs from degruyter.com to
    # degruyterbrill.com. Both hosts serve the same /document/doi/<doi>/pdf
    # resource; licenseType and stream are viewer/access-state parameters.
    if host in {"degruyter.com", "degruyterbrill.com"} and re.search(
        r"^/document/doi/10\..*/pdf(?:/firstPage)?/?$", path,
        flags=re.I,
    ):
        host = "degruyterbrill.com"
        path = re.sub(r"/pdf(?:/firstPage)?/?$", "/pdf", path, flags=re.I)
        query_pairs = [
            (k, v)
            for k, v in query_pairs
            if k.lower() not in {"licensetype", "stream"}
        ]
    # Brill and its imprints expose the same PDF behind several stable path
    # aliases. Goldie often records the viewer-form route
    # /downloadpdf/display/... or /downloadpdf/view/..., while page anchors
    # point directly at /downloadpdf/... . Fink and Schoeningh are Brill/De
    # Gruyter imprint hosts with the same downloadpdf path family.
    if host in {"brill.com", "fink.de", "schoeningh.de"} and path.startswith("/downloadpdf/"):
        host = "brill.com"
        path = path.replace("/downloadpdf/display/", "/downloadpdf/", 1)
        path = path.replace("/downloadpdf/view/", "/downloadpdf/", 1)
        path = path.replace("/downloadpdf/edcollchap/book/", "/downloadpdf/book/", 1)
    # Vandenhoeck & Ruprecht / Brill pages use /doi/reader/<doi> as a PDF
    # reader wrapper and /doi/pdf/<doi>?download=true as the direct PDF route.
    if host == "vr-elibrary.de":
        path = path.replace("/doi/reader/", "/doi/pdf/", 1)
        query_pairs = [(k, v) for k, v in query_pairs if k != "download"]
    # BMJ main-site URLs may include or omit the journal segment in article PDF
    # paths. Both /content/<volume>/<issue>/<page>.full.pdf and
    # /content/bmj/<volume>/<issue>/<page>.full.pdf resolve to the same PDF.
    # Keep this scoped to the main BMJ host so specialty journals such as
    # emj.bmj.com keep their own /content/emermed/... path identity.
    if host == "bmj.com":
        path = path.replace("/content/bmj/", "/content/", 1)
    # SSRN exposes PDF downloads behind a stable Delivery.cfm abstractid route.
    # Goldie often stores the generic form:
    #   /sol3/Delivery.cfm?abstractid=<id>
    # while Parseland/Taxicab sees the concrete delivery object:
    #   /sol3/Delivery.cfm/<file>.pdf?abstractid=<id>&mirid=1
    # The abstractid identifies the paper PDF; the concrete file path and
    # mirid are delivery-state details.
    if host == "papers.ssrn.com" and path.lower().startswith("/sol3/delivery.cfm"):
        abstractid = next((v for k, v in query_pairs if k.lower() == "abstractid" and v), None)
        if abstractid:
            path = "/sol3/Delivery.cfm"
            query_pairs = [("abstractid", abstractid)]
    # AHA journals (ahajournals.org): /doi/pdf/X?download=true is the download
    # viewer form for the same DOI PDF that gold records without the param.
    if host.endswith("ahajournals.org"):
        query_pairs = [(k, v) for k, v in query_pairs if k != "download"]
    # Taylor & Francis (tandfonline.com): /doi/epdf/ and /doi/pdf/ both serve
    # the same article PDF (Taylor's clean_pdf_url already collapses /epdf/ →
    # /pdf/ for the parser path; gold sometimes keeps /epdf/). needAccess and
    # role are state tracking params the page appends, not part of the URL
    # identity.
    if host.endswith("tandfonline.com"):
        path = path.replace("/doi/epdf/", "/doi/pdf/")
        query_pairs = [(k, v) for k, v in query_pairs if k not in ("needAccess", "role")]
    query = urlencode(query_pairs)
    # ScienceDirect /pdf ↔ /pdfft equivalence — same resource, different
    # signing wrapper. Apply only on the ScienceDirect host so we don't
    # accidentally collapse paths on unrelated sites.
    if host == "sciencedirect.com" and path.endswith("/pdfft"):
        path = path[: -len("/pdfft")] + "/pdf"
    return urlunsplit((scheme, host, path, query, ""))


def normalize_doi(doi: str | None) -> str:
    if not doi:
        return ""
    d = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d
