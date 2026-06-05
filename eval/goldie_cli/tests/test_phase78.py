from __future__ import annotations

import csv
import json

from goldie_cli.events import RunEventWriter, load_resolved_urls
from goldie_cli.maintenance import RunSnapshot, clean, find_clutter, migrate_check
from goldie_cli.rundir import RunDir
from goldie_cli.sample import append_partial, keep_item, load_partial, sample_dois, write_corpus_csv
from goldie_cli.schema import GOLD_COLUMNS
from goldie_cli.spike.browserbase_fetch import (
    assess_extraction,
    assess_html,
    run_spike,
    summarize,
)


# ---- sample (offline, injected fetcher) -----------------------------------

def test_keep_item_filters_non_articles():
    assert keep_item({"DOI": "10.1/a", "type": "journal-article"})
    assert not keep_item({"DOI": "10.1/b", "type": "dataset"})
    assert not keep_item({"type": "journal-article"})  # no DOI


def test_sample_dois_dedups_and_excludes():
    batches = iter([
        [{"DOI": "10.1/A", "type": "journal-article"}, {"DOI": "10.1/B", "type": "dataset"}],
        [{"DOI": "10.1/A", "type": "journal-article"}, {"DOI": "10.1/C", "type": "journal-article"}],
        [{"DOI": "10.1/D", "type": "journal-article"}],
    ])
    got = sample_dois(2, fetch_sample=lambda: next(batches), exclude=frozenset({"10.1/a"}))
    assert got == ["10.1/c", "10.1/d"]  # lowercased, A excluded, B dropped (dataset), deduped


def test_write_corpus_csv_uses_doi_org_resolver(tmp_path):
    p = tmp_path / "corpus.csv"
    write_corpus_csv(p, ["10.1/x", "10.1/y"])
    rows = list(csv.DictReader(p.open()))
    assert rows[0]["No"] == "1"
    assert rows[0]["DOI"] == "10.1/x"
    assert rows[0]["Link"] == "https://doi.org/10.1/x"
    assert list(rows[0]) == GOLD_COLUMNS
    assert rows[0]["Authors"] == ""
    assert rows[0]["PDF URL"] == ""


def test_sample_partial_roundtrip_skips_bad_lines(tmp_path):
    p = tmp_path / "corpus.csv.partial.jsonl"
    append_partial(p, "10.1/A")
    p.write_text(p.read_text(encoding="utf-8") + "not json\n", encoding="utf-8")
    append_partial(p, "10.1/B")

    assert load_partial(p) == ["10.1/a", "10.1/b"]


# ---- clean / migrate ------------------------------------------------------

def test_clean_dry_run_targets_clutter_and_guards_protected(tmp_path):
    (tmp_path / "ai-goldie-10k").mkdir()
    (tmp_path / "ai-goldie-10k.zip").write_text("z")
    (tmp_path / "merged-FINAL.csv").write_text("protected")  # must never be touched
    targets = find_clutter(tmp_path)
    names = {p.name for p in targets}
    assert "ai-goldie-10k" in names and "ai-goldie-10k.zip" in names
    assert "merged-FINAL.csv" not in names
    res = clean(mode="dry-run", data_dir=tmp_path)
    assert (tmp_path / "ai-goldie-10k").exists()  # dry-run removes nothing
    assert len(res["targets"]) == len(targets)


def test_clean_remove_deletes_only_clutter(tmp_path):
    (tmp_path / "ai-goldie-10k.zip").write_text("z")
    (tmp_path / "merged-FINAL.csv").write_text("keep")
    clean(mode="remove", data_dir=tmp_path)
    assert not (tmp_path / "ai-goldie-10k.zip").exists()
    assert (tmp_path / "merged-FINAL.csv").exists()


def test_migrate_check_finds_consumers(tmp_path):
    (tmp_path / "consumer.py").write_text("open('ai-goldie-1.csv')\n")
    (tmp_path / "unrelated.py").write_text("x = 1\n")
    res = migrate_check(tmp_path)
    assert "consumer.py" in res["consumers"] and res["count"] == 1


def test_run_snapshot(tmp_path):
    rd = RunDir.create("snap", runs_dir=tmp_path, stamp="t")
    rd.write_manifest({"corpus": "snap", "status": "complete", "rows": 5, "landed": 5,
                       "failed": 0, "cost_usd": 1.23,
                       "fallback": {"fallback_used": 2},
                       "taxicab_reharvest": {"refreshed": 1}})
    rd.events_path.write_text('{"type":"run_complete"}\n', encoding="utf-8")
    rd.live_html_path.write_text("<html></html>", encoding="utf-8")
    s = RunSnapshot.read(rd)
    assert s.status == "complete" and s.landed == 5 and s.cost_usd == 1.23
    assert s.fallback_used == 2 and s.taxicab_reharvest == {"refreshed": 1}
    assert s.events == 1 and s.live_html.endswith("live.html")


def test_event_writer_preserves_live_and_screenshot_urls(tmp_path):
    rd = RunDir.create("evt", runs_dir=tmp_path, stamp="t")
    ew = RunEventWriter(rd)
    long_url = "https://example.com/screenshot?" + ("x" * 800)

    ew.write(
        "backend_result",
        doi="10.1/a",
        live_url=long_url,
        screenshot_url=long_url,
        last_step_summary="y" * 800,
    )

    data = json.loads(rd.events_path.read_text().splitlines()[-1])
    assert data["live_url"] == long_url
    assert data["screenshot_url"] == long_url
    assert data["last_step_summary"].endswith("...")


def test_event_writer_doi_landed_reports_priority_field_presence(tmp_path):
    rd = RunDir.create("evt", runs_dir=tmp_path, stamp="t")
    ew = RunEventWriter(rd)
    ew.doi_landed(
        batch=1,
        doi="10.1/a",
        tier="cached",
        row={
            "Authors": json.dumps([
                {"name": "A", "rasses": "University", "corresponding_author": True}
            ]),
            "Abstract": "abstract",
            "PDF URL": "https://example.org/a.pdf",
            "Has Bot Check": "FALSE",
            "Resolves To PDF": "FALSE",
            "broken_doi": "FALSE",
            "no english": "FALSE",
            "Status": "TRUE",
        },
        cost_usd=0.1,
    )

    data = json.loads(rd.events_path.read_text().splitlines()[-1])

    assert data["type"] == "doi_landed"
    assert data["fields"] == {
        "authors": True,
        "rases": True,
        "ca": True,
        "abstract": True,
        "pdf_url": True,
        "has_bot_check": False,
        "resolves_to_pdf": False,
        "broken_doi": False,
        "no_english": False,
    }


def test_load_resolved_urls_from_backend_events(tmp_path):
    events = tmp_path / "events.ndjson"
    events.write_text(
        "\n".join([
            json.dumps({
                "type": "backend_result",
                "doi": "10.1/a",
                "resolved_url": "https://doi.org/10.1/a",
            }),
            json.dumps({"type": "doi_landed", "doi": "10.1/a"}),
            json.dumps({
                "type": "backend_result",
                "doi": "10.1/a",
                "resolved_url": "https://publisher.example/article",
            }),
        ]) + "\n",
        encoding="utf-8",
    )

    assert load_resolved_urls(events) == {"10.1/a": "https://publisher.example/article"}


# ---- browserbase spike (offline, injected fetchers) -----------------------

def test_assess_html_block_and_useful():
    assert assess_html("just a moment... cloudflare").blocked
    good = "<html>" + "x" * 3000 + '<meta name="citation_author" content="A">'
    a = assess_html(good)
    assert a.ok and a.useful


def test_run_spike_recommendation_no_improvement():
    good = "<html>" + "x" * 3000 + '<meta name="citation_title" content="T">'
    rep = run_spike(["10.1/a", "10.1/b"],
                    taxicab_fetch=lambda d: good, browserbase_fetch=lambda d: good)
    assert rep["summary"]["recommendation"].startswith("no material improvement")


def test_run_spike_recommendation_browserbase_better():
    blocked = "Just a moment... challenges.cloudflare.com"
    good = "<html>" + "x" * 3000 + '<meta name="citation_author" content="A">'
    rep = run_spike(["10.1/a"], taxicab_fetch=lambda d: blocked, browserbase_fetch=lambda d: good)
    assert rep["summary"]["browserbase_only_useful"] == 1
    # raw-evidence mode always records the no-JS caveat.
    assert rep["js_execution"] is False and rep["evidence_format"] == "html"


# ---- browserbase spike: structured JSON extraction mode -------------------

def test_assess_extraction_usable_and_empty():
    full = {"authors": [{"name": "A", "rasses": "U"}], "abstract": "x", "pdf_url": "p"}
    o = assess_extraction(full)
    assert o.ok and o.authors and o.abstract and o.pdf_url and o.usable
    empty = assess_extraction(None)
    assert not empty.ok and not empty.usable
    # authors-only is not usable without abstract or pdf.
    assert assess_extraction({"authors": [{"name": "A"}]}).usable is False


def test_run_spike_structured_mode_reports_fill_rates():
    full = {"authors": [{"name": "A"}], "abstract": "x", "pdf_url": "p"}
    rep = run_spike(
        ["10.1/a", "10.1/b"],
        taxicab_fetch=lambda d: "irrelevant",
        browserbase_extract=lambda d: full,
    )
    # json-only: no raw-evidence summary, but a structured summary with full fill.
    assert "summary" not in rep
    ss = rep["structured_summary"]
    assert ss["usable_rate"] == 1.0 and ss["authors_rate"] == 1.0
    assert ss["recommendation"].startswith("candidate")
    assert rep["js_execution"] is False


def test_run_spike_both_modes_attribute_separately():
    good = "<html>" + "x" * 3000 + '<meta name="citation_author" content="A">'
    rep = run_spike(
        ["10.1/a"],
        taxicab_fetch=lambda d: good,
        browserbase_fetch=lambda d: good,
        browserbase_extract=lambda d: {"authors": [], "abstract": "", "pdf_url": ""},
    )
    assert "summary" in rep and "structured_summary" in rep
    assert rep["structured_summary"]["usable_rate"] == 0.0
    assert rep["structured_summary"]["recommendation"].startswith("Fetch Extract underfills")
