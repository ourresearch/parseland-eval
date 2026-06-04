from __future__ import annotations

import json

from goldie_cli.report import FIELD_ORDER, compute_report, write_report


def _row(doi, authors, abstract, pdf, status="TRUE"):
    return {"No": "1", "DOI": doi, "Link": "L",
            "Authors": json.dumps(authors) if authors else "[]",
            "Abstract": abstract, "PDF URL": pdf, "Status": status, "Notes": "",
            "Has Bot Check": "", "Resolves To PDF": "", "broken_doi": "", "no english": ""}


GA = [{"name": "Jane Roe", "rasses": "MIT", "corresponding_author": True}]


def test_field_order_is_skill_order():
    assert FIELD_ORDER == ["rases", "pdf_url", "ca", "abstract", "authors"]


def test_perfect_match_all_fields():
    gold = [_row("10.1/a", GA, "the abstract text here", "https://pub/a.pdf")]
    produced = [_row("10.1/a", GA, "the abstract text here", "https://pub/a.pdf")]
    rep = compute_report(gold, produced)
    assert rep["matched_rows"] == 1
    for f in FIELD_ORDER:
        assert rep["fields"][f]["accuracy_all"] == 1.0


def test_empty_abstract_bucketed_as_empty():
    gold = [_row("10.1/a", GA, "a real abstract present in gold", "https://pub/a.pdf")]
    produced = [_row("10.1/a", GA, "", "https://pub/a.pdf")]
    rep = compute_report(gold, produced)
    assert rep["fields"]["abstract"]["accuracy_all"] == 0.0
    assert rep["fields"]["abstract"]["failure_buckets"].get("empty") == 1


def test_fetch_ok_split_excludes_failed_rows():
    gold = [_row("10.1/a", GA, "abs", "p"), _row("10.1/b", GA, "abs2", "p2")]
    produced = [_row("10.1/a", GA, "abs", "p"),
                _row("10.1/b", [], "", "", status="FALSE")]  # fetch failure
    rep = compute_report(gold, produced)
    assert rep["matched_rows"] == 2
    assert rep["fetch_ok_rows"] == 1
    # authors: 1/2 over all, 1/1 over fetch-OK
    assert rep["fields"]["authors"]["accuracy_all"] == 0.5
    assert rep["fields"]["authors"]["accuracy_fetch_ok"] == 1.0


def test_write_report_roundtrip(tmp_path):
    rep = compute_report([_row("10.1/a", GA, "x", "p")], [_row("10.1/a", GA, "x", "p")])
    p = tmp_path / "report.json"
    write_report(p, rep)
    assert json.loads(p.read_text())["field_order"] == FIELD_ORDER
