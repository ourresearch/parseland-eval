from __future__ import annotations

import json

from goldie_cli.report import (
    FIELD_ORDER,
    compute_report,
    operator_report_markdown,
    summary_report,
    write_operator_report,
    write_report,
)


def _row(doi, authors, abstract, pdf, status="TRUE"):
    return {"No": "1", "DOI": doi, "Link": "L",
            "Authors": json.dumps(authors) if authors else "[]",
            "Abstract": abstract, "PDF URL": pdf, "Status": status, "Notes": "",
            "Has Bot Check": "", "Resolves To PDF": "", "broken_doi": "", "no english": ""}


GA = [{"name": "Jane Roe", "rasses": "MIT", "corresponding_author": True}]
GNA = [{"name": "Jane Roe", "rasses": "MIT", "corresponding_author": False}]


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


def test_fetch_ok_split_excludes_empty_status_true_rows():
    gold = [_row("10.1/a", GA, "abs", "p"), _row("10.1/b", GA, "abs2", "p2")]
    produced = [_row("10.1/a", GA, "abs", "p"),
                _row("10.1/b", [], "", "", status="TRUE")]  # empty backend object
    rep = compute_report(gold, produced)
    assert rep["fetch_ok_rows"] == 1
    assert rep["fields"]["authors"]["accuracy_fetch_ok"] == 1.0


def test_write_report_roundtrip(tmp_path):
    rep = compute_report([_row("10.1/a", GA, "x", "p")], [_row("10.1/a", GA, "x", "p")])
    p = tmp_path / "report.json"
    write_report(p, rep)
    assert json.loads(p.read_text())["field_order"] == FIELD_ORDER


def test_operator_report_markdown_contains_status_metrics_and_commands(tmp_path):
    rep = summary_report([_row("10.1/a", GA, "abs", "p")], {
        "status": "complete",
        "cost_usd": 1.0,
        "total_cost_usd": 2.0,
        "fallback": {"tier": "cloud", "fallback_attempted": 1, "fallback_used": 1, "cost_usd": 1.0},
    })
    manifest = {
        "status": "complete",
        "corpus": "goldie-random-1",
        "source_csv": "runs/goldie-random-1/source.csv",
        "tier": "cached",
        "fallback_tier": "cloud",
        "failed": 0,
        "fallback": {"tier": "cloud", "status": "complete", "fallback_attempted": 1, "fallback_used": 1},
    }
    md = operator_report_markdown(tmp_path / "runs" / "goldie-random-1-20260605T000000Z", rep, manifest)
    assert "**Status:** `ready`" in md
    assert "| Total cost | $2.00 |" in md
    assert "goldie resume --run" in md
    assert "Do not claim 98% absolute accuracy" in md

    out = tmp_path / "OPERATOR_REPORT.md"
    write_operator_report(out, md)
    assert out.read_text(encoding="utf-8").startswith("# Goldie Operator Report")


def test_summary_report_quality_telemetry():
    rows = [
        _row("10.1/a", GA, "abs", "p"),
        {**_row("10.1/b", [], "", "", status="FALSE"),
         "Notes": "taxicab-reharvest:refreshed | iter-R:extraction-miss"},
    ]
    rep = summary_report(rows, {
        "status": "complete",
        "cost_usd": 1.0,
        "total_cost_usd": 2.0,
        "taxicab_reharvest": {"refreshed": 1},
        "fallback": {"fallback_attempted": 1, "fallback_used": 0},
        "targeted_rerun": {"changed_rows": 1},
        "quality_probes": [{"name": "ca-audit", "changed_rows": 0}],
        "events": "logs/live-agent-events.ndjson",
        "live_html": "live.html",
    })
    assert rep["field_presence"]["authors"] == {"present": 1, "rows": 2}
    assert rep["field_presence"]["rases"] == {"present": 1, "rows": 2}
    assert rep["field_presence"]["pdf_url"] == {"present": 1, "rows": 2}
    assert rep["field_missing"]["authors"] == {"missing": 1, "rows": 2}
    assert rep["field_missing"]["abstract"] == {"missing": 1, "rows": 2}
    assert rep["field_missing"]["pdf_url"] == {"missing": 1, "rows": 2}
    assert rep["missing_field_combinations"][0] == ("rases,pdf_url,ca,abstract,authors", 1)
    assert rep["extraction_miss"] == 1
    assert rep["taxicab_reharvest"] == {"refreshed": 1}
    assert rep["top_labels"][0] == ("taxicab-reharvest:refreshed", 1)
    assert rep["final_status"] == {"true": 1, "false": 1, "other": 0}
    assert rep["events"].endswith("live-agent-events.ndjson")
    assert "legacy_fallback_used_semantics" in rep["fallback"]
    assert rep["targeted_rerun"] == {"changed_rows": 1}
    assert rep["quality_probes"] == [{"name": "ca-audit", "changed_rows": 0}]
    assert rep["quality_focus"]["counts"]["all_core_empty"] == 1
    assert rep["quality_focus"]["counts"]["unresolved_all_core_empty"] == 1
    assert rep["quality_focus"]["counts"]["extraction_miss"] == 1
    assert rep["quality_focus"]["all_core_empty"][0]["doi"] == "10.1/b"
    assert rep["quality_focus"]["all_core_empty"][0]["fetch_ok"] is False


def test_summary_report_splits_terminal_empty_rows():
    rows = [{
        **_row("10.1/broken", [], "", "", status="TRUE"),
        "broken_doi": "TRUE",
        "Notes": "taxicab-reharvest:refreshed | iter-R:extraction-miss",
    }]

    rep = summary_report(rows, {})
    qf = rep["quality_focus"]

    assert rep["fetch_ok_rows"] == 1
    assert rep["extraction_miss"] == 0
    assert qf["counts"]["all_core_empty"] == 1
    assert qf["counts"]["terminal_flagged_empty"] == 1
    assert qf["counts"]["bot_check_empty"] == 0
    assert qf["counts"]["unresolved_all_core_empty"] == 0
    assert qf["terminal_flagged_empty"][0]["terminal_flags"] == {"broken_doi": True}
    assert "iter-R:terminal:broken_doi" in qf["terminal_flagged_empty"][0]["labels"]
    assert "iter-R:extraction-miss" not in qf["terminal_flagged_empty"][0]["labels"]
    assert qf["terminal_flagged_empty"][0]["notes"] == (
        "taxicab-reharvest:refreshed | iter-R:terminal:broken_doi"
    )
    assert qf["terminal_flagged_empty"][0]["raw_notes"] == (
        "taxicab-reharvest:refreshed | iter-R:extraction-miss"
    )


def test_summary_report_treats_no_metadata_note_as_terminal_evidence():
    rows = [{
        **_row("10.1/book-review", [], "", "", status="FALSE"),
        "Notes": "Book review; no authors listed on page | iter-R:terminal:no_article_metadata",
    }]

    rep = summary_report(rows, {})
    qf = rep["quality_focus"]

    assert rep["fetch_ok_rows"] == 1
    assert qf["counts"]["all_core_empty"] == 1
    assert qf["counts"]["terminal_flagged_empty"] == 1
    assert qf["counts"]["unresolved_all_core_empty"] == 0
    assert "iter-R:terminal:no_article_metadata" in qf["terminal_flagged_empty"][0]["labels"]


def test_summary_report_no_metadata_terminal_takes_precedence_over_bot_queue():
    rows = [{
        **_row("10.1/lww", [], "", "", status="TRUE"),
        "Has Bot Check": "TRUE",
        "Notes": "iter-R:bot-check:flagged | iter-R:terminal:no_article_metadata",
    }]

    rep = summary_report(rows, {})
    qf = rep["quality_focus"]

    assert rep["fetch_ok_rows"] == 1
    assert qf["counts"]["all_core_empty"] == 1
    assert qf["counts"]["terminal_flagged_empty"] == 1
    assert qf["counts"]["bot_check_empty"] == 0
    assert qf["counts"]["unresolved_all_core_empty"] == 0


def test_summary_report_suppresses_extraction_miss_for_structural_note():
    authors = [{"name": "Jane Roe", "rasses": "", "corresponding_author": False}]
    rows = [{
        **_row("10.1/no-abstract", authors, "", "https://pub.example/a.pdf", status="TRUE"),
        "Notes": "taxicab-reharvest:refreshed | iter-R:extraction-miss | iter-R:no-abstract-unavailable",
    }]

    rep = summary_report(rows, {})

    assert rep["extraction_miss"] == 0
    assert rep["top_labels"] == [
        ("taxicab-reharvest:refreshed", 1),
        ("iter-R:no-abstract-unavailable", 1),
    ]
    assert rep["quality_focus"]["counts"]["extraction_miss"] == 0
    assert rep["quality_focus"]["multi_priority_missing"][0]["labels"] == [
        "taxicab-reharvest:refreshed",
        "iter-R:no-abstract-unavailable",
    ]


def test_summary_report_tracks_page_checked_residual_missing_fields():
    authors = [{"name": "Jane Roe", "rasses": "", "corresponding_author": False}]
    rows = [{
        **_row("10.1/page-checked", authors, "abs", "", status="TRUE"),
        "Notes": (
            "taxicab-reharvest:refreshed | page-probe:residual-missing=rases,ca,pdf_url | "
            "iter-R:page-checked-residual-missing-fields"
        ),
    }]

    rep = summary_report(rows, {})
    qf = rep["quality_focus"]

    assert rep["extraction_miss"] == 0
    assert qf["counts"]["extraction_miss"] == 0
    assert qf["counts"]["page_checked_residual_missing"] == 1
    assert qf["page_checked_residual_missing"][0]["missing_fields"] == ["rases", "pdf_url", "ca"]
    assert "iter-R:page-checked-residual-missing-fields" in (
        qf["page_checked_residual_missing"][0]["labels"]
    )


def test_summary_report_excludes_structural_empty_rows_from_unresolved_queue():
    rows = [{
        **_row("10.1/paywall", [], "", "", status="FALSE"),
        "Notes": "iter-R:paywalled=taylor-francis",
    }]

    rep = summary_report(rows, {})
    qf = rep["quality_focus"]

    assert rep["fetch_ok_rows"] == 0
    assert qf["counts"]["all_core_empty"] == 1
    assert qf["counts"]["terminal_flagged_empty"] == 0
    assert qf["counts"]["unresolved_all_core_empty"] == 0


def test_summary_report_splits_bot_check_empty_rows():
    rows = [{
        **_row("10.1/bot", [], "", "", status="FALSE"),
        "Has Bot Check": "TRUE",
        "Notes": "iter-R:extraction-miss",
    }]

    rep = summary_report(rows, {})
    qf = rep["quality_focus"]

    assert rep["fetch_ok_rows"] == 0
    assert rep["extraction_miss"] == 0
    assert qf["counts"]["all_core_empty"] == 1
    assert qf["counts"]["terminal_flagged_empty"] == 0
    assert qf["counts"]["bot_check_empty"] == 1
    assert qf["counts"]["unresolved_all_core_empty"] == 0
    assert qf["bot_check_empty"][0]["terminal_flags"] == {"has_bot_check": True}
    assert "iter-R:bot-check:flagged" in qf["bot_check_empty"][0]["labels"]
    assert "iter-R:extraction-miss" not in qf["bot_check_empty"][0]["labels"]
    assert qf["bot_check_empty"][0]["notes"] == "iter-R:bot-check:flagged"
    assert qf["bot_check_empty"][0]["raw_notes"] == "iter-R:extraction-miss"


def test_summary_report_triages_ca_only_convention_candidates():
    rows = [_row("10.1007/978-3-642-57617-1_11", GNA, "abs", "https://pub/x.pdf")]

    rep = summary_report(rows, {})
    qf = rep["quality_focus"]

    assert qf["counts"]["ca_only_missing"] == 1
    assert qf["counts"]["ca_convention_candidates"] == 1
    assert qf["counts"]["ca_needs_evidence_audit"] == 0
    assert qf["ca_triage_counts"] == {"book_chapter_or_ebook": 1}
    assert qf["ca_only_missing"][0]["ca_triage"]["reason"] == "book_chapter_or_ebook"
    assert rep["field_missing"]["ca"] == {"missing": 1, "rows": 1}


def test_summary_report_triages_ca_only_rows_for_audit():
    rows = [_row("10.5333/kgfs.2008.28.2.081", GNA, "abs", "https://pub/x.pdf")]

    rep = summary_report(rows, {})
    qf = rep["quality_focus"]

    assert qf["counts"]["ca_only_missing"] == 1
    assert qf["counts"]["ca_convention_candidates"] == 0
    assert qf["counts"]["ca_needs_evidence_audit"] == 1
    assert qf["ca_triage_counts"] == {"needs_explicit_ca_evidence_audit": 1}
    assert qf["ca_needs_evidence_audit"][0]["doi"] == "10.5333/kgfs.2008.28.2.081"


def test_summary_report_triages_live_ca_probe_outcomes():
    rows = [
        {**_row("10.1/no-marker", GNA, "abs", "https://pub/x.pdf"),
         "Notes": "live-probe:ca-no-explicit-marker"},
        {**_row("10.1/candidate", GNA, "abs", "https://pub/y.pdf"),
         "Notes": "live-probe:ca-marker-candidate"},
        {**_row("10.1/bot", GNA, "abs", "https://pub/z.pdf"),
         "Notes": "live-probe:ca-bot-check=cloudflare"},
        {**_row("10.1/router", GNA, "abs", "https://pub/r.pdf"),
         "Notes": "live-probe:ca-router-only=elsevier-linkinghub"},
    ]

    rep = summary_report(rows, {})
    qf = rep["quality_focus"]

    assert qf["counts"]["ca_only_missing"] == 4
    assert qf["counts"]["ca_live_no_marker"] == 1
    assert qf["counts"]["ca_live_marker_candidates"] == 1
    assert qf["counts"]["ca_live_blocked"] == 2
    assert qf["counts"]["ca_needs_evidence_audit"] == 3
    assert qf["ca_triage_counts"] == {
        "live_probe_no_explicit_ca_marker": 1,
        "live_probe_ca_marker_candidate": 1,
        "live_probe_blocked_by_bot_check": 1,
        "live_probe_router_only": 1,
    }


def test_summary_report_triages_page_ca_probe_outcomes():
    rows = [
        {**_row("10.1/page-no-marker", GNA, "abs", "https://pub/x.pdf"),
         "Notes": "live-probe:ca-bot-check=cloudflare | page-probe:ca-no-explicit-marker"},
        {**_row("10.1/page-marker", GNA, "abs", "https://pub/y.pdf"),
         "Notes": "page-probe:ca-marker-candidate"},
    ]

    rep = summary_report(rows, {})
    qf = rep["quality_focus"]

    assert qf["counts"]["ca_only_missing"] == 2
    assert qf["counts"]["ca_page_no_marker"] == 1
    assert qf["counts"]["ca_page_marker_candidates"] == 1
    assert qf["counts"]["ca_live_blocked"] == 0
    assert qf["counts"]["ca_needs_evidence_audit"] == 1
    assert qf["ca_triage_counts"] == {
        "page_probe_no_explicit_ca_marker": 1,
        "page_probe_ca_marker_candidate": 1,
    }
    assert qf["ca_only_missing"][0]["ca_triage"]["reason"] == "page_probe_no_explicit_ca_marker"


def test_summary_report_quality_focus_reads_event_metadata(tmp_path):
    events = tmp_path / "live-agent-events.ndjson"
    events.write_text(
        "\n".join([
            json.dumps({
                "type": "backend_result",
                "tier": "cached",
                "doi": "10.1/b",
                "reharvest": {
                    "status": "refreshed",
                    "reason": "thin_extraction:authors,abstract,pdf_url",
                    "duration_s": 1.2,
                    "http_status_post": 201,
                    "http_status_get": 200,
                },
            }),
            json.dumps({
                "type": "backend_result",
                "tier": "cloud",
                "doi": "10.1/b",
                "live_url": "https://live.browser-use.com/session/abc",
                "screenshot_url": "https://signed.example/screenshot.png",
                "is_task_successful": False,
                "step_count": 7,
                "last_step_summary": "Running Python code",
            }),
            json.dumps({
                "type": "fallback_result",
                "tier": "cloud",
                "doi": "10.1/b",
                "returned": True,
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    rows = [
        {**_row("10.1/b", [], "", "", status="FALSE"),
         "Notes": "taxicab-reharvest:refreshed | iter-R:extraction-miss"},
    ]
    rep = summary_report(rows, {"events": str(events)})
    event = rep["quality_focus"]["all_core_empty"][0]["event"]

    assert event["taxicab_reharvest"]["reason"] == "thin_extraction:authors,abstract,pdf_url"
    assert event["browser_live_url"] == "https://live.browser-use.com/session/abc"
    assert event["browser_screenshot_present"] is True
    assert event["browser_is_task_successful"] is False
    assert event["browser_step_count"] == 7
    assert event["fallback_returned"] is True
