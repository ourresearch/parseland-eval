from __future__ import annotations

import csv

from goldie_cli.maintenance import RunSnapshot, clean, find_clutter, migrate_check
from goldie_cli.rundir import RunDir
from goldie_cli.sample import keep_item, sample_dois, write_corpus_csv
from goldie_cli.spike.browserbase_fetch import assess_html, run_spike, summarize


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
    assert rows[0] == {"No": "1", "DOI": "10.1/x", "Link": "https://doi.org/10.1/x"}


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
                       "failed": 0, "cost_usd": 1.23})
    s = RunSnapshot.read(rd)
    assert s.status == "complete" and s.landed == 5 and s.cost_usd == 1.23


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
