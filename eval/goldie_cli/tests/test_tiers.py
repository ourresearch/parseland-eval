from __future__ import annotations

import asyncio
import json

from goldie_cli.tiers import classify_row, clean_row, crosscheck, merge_rows, run_with_fallback
from goldie_cli.tiers.crosscheck import CrossCheck


# ---- merge ----------------------------------------------------------------

def test_merge_fills_empty_from_fallback():
    primary = {"Authors": "[]", "Abstract": "", "PDF URL": "", "Status": "FALSE", "Notes": ""}
    fb = {"Authors": json.dumps([{"name": "A", "rasses": "MIT"}]), "Abstract": "abs",
          "PDF URL": "http://x/y.pdf"}
    out = merge_rows(primary, fb)
    assert "MIT" in out["Authors"] and out["Abstract"] == "abs" and out["PDF URL"] == "http://x/y.pdf"
    assert out["Status"] == "TRUE"


def test_merge_never_overwrites_good_or_gold_empty():
    primary = {"Authors": json.dumps([{"name": "Keep"}]), "Abstract": "good", "PDF URL": ""}
    fb = {"Authors": json.dumps([{"name": "Other"}]), "Abstract": "other", "PDF URL": "http://x.pdf"}
    out = merge_rows(primary, fb, gold_empty_fields=frozenset({"PDF URL"}))
    assert "Keep" in out["Authors"]          # don't replace good authors
    assert out["Abstract"] == "good"         # don't replace good abstract
    assert out["PDF URL"] == ""              # gold says empty → preserved


def test_merge_no_fallback_is_noop():
    primary = {"Authors": "[]", "Abstract": "", "PDF URL": ""}
    assert merge_rows(primary, None) == primary


# ---- classify -------------------------------------------------------------

def test_classify_full_row_returns_none():
    row = {"Authors": "[{}]", "Abstract": "x", "PDF URL": "y"}
    assert classify_row(row, "https://publisher/x") is None


def test_classify_extraction_miss_without_url():
    assert classify_row({"Authors": "[]", "Abstract": "", "PDF URL": ""}) == "iter-R:extraction-miss"


def test_classify_botcheck_and_paywall_and_pdf():
    empty = {"Authors": "[]", "Abstract": "", "PDF URL": ""}
    assert classify_row(empty, "https://validate.perfdrive.com/x") == "iter-R:bot-check:perimeterx"
    assert classify_row(empty, "https://www.sciencedirect.com/science/article/pii/X") == "iter-R:paywalled=elsevier"
    assert classify_row(empty, "https://host/file.pdf") == "iter-R:pdf-redirect"


# ---- cleanup --------------------------------------------------------------

def test_clean_row_fixes_mojibake_in_abstract_and_authors():
    bad = "CafÃ©"  # classic UTF-8-as-Latin1 mojibake → "Café"
    row = {"Abstract": bad, "Notes": "", "Authors": json.dumps([{"name": bad, "rasses": ""}])}
    out = clean_row(row)
    assert out["Abstract"] == "Café"
    assert json.loads(out["Authors"])[0]["name"] == "Café"


# ---- crosscheck (write-isolated) ------------------------------------------

def test_crosscheck_returns_only_confidence_and_route():
    cc = crosscheck({"Authors": "[]", "Abstract": "", "PDF URL": ""})
    assert isinstance(cc, CrossCheck)
    assert set(vars(cc)) == {"confidence", "route"}     # no row, no write path
    assert cc.route == "livefetch"


def test_crosscheck_cannot_mutate_input_row():
    row = {"Authors": json.dumps([{"name": "A"}]), "Abstract": "a", "PDF URL": "p"}
    snapshot = json.dumps(row, sort_keys=True)
    crosscheck(row, parser_output={"Abstract": "totally different", "PDF URL": "q"})
    assert json.dumps(row, sort_keys=True) == snapshot   # input untouched


def test_crosscheck_routes():
    full = {"Authors": json.dumps([{"name": "A"}]), "Abstract": "a", "PDF URL": "p"}
    assert crosscheck(full).route == "skip_livefetch"
    assert crosscheck(full, parser_output={"Abstract": "a", "PDF URL": "p"}).route == "skip_livefetch"
    assert crosscheck(full, parser_output={"Abstract": "xyz", "PDF URL": "q"}).route == "human_audit"


# ---- tiered composition ---------------------------------------------------

def test_run_with_fallback_fills_and_labels():
    rows = [
        {"No": "1", "DOI": "10.1/a", "Link": "L", "Authors": json.dumps([{"name": "Has"}]),
         "Abstract": "ok", "PDF URL": "p", "Status": "TRUE", "Notes": ""},
        {"No": "2", "DOI": "10.1/b", "Link": "L", "Authors": "[]", "Abstract": "", "PDF URL": "",
         "Status": "FALSE", "Notes": ""},
    ]

    async def fb(doi, link):
        if doi == "10.1/b":
            return {"Authors": json.dumps([{"name": "Live"}]), "Abstract": "live abs", "PDF URL": "lp"}
        return None

    final, stats = asyncio.run(run_with_fallback(rows, fallback_extract=fb))
    by = {r["DOI"]: r for r in final}
    assert "Live" in by["10.1/b"]["Authors"]
    assert stats["fallback_used"] == 1


def test_run_with_fallback_labels_empty_when_no_fallback():
    rows = [{"No": "1", "DOI": "10.1/b", "Link": "L", "Authors": "[]", "Abstract": "",
             "PDF URL": "", "Status": "FALSE", "Notes": ""}]
    final, stats = asyncio.run(run_with_fallback(rows, fallback_extract=None))
    assert "iter-R:extraction-miss" in final[0]["Notes"]
    assert stats["labels"].get("iter-R:extraction-miss") == 1
