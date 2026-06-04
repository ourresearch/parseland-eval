from __future__ import annotations

import csv
import json

from goldie_cli.io import normalize_author, read_source_rows, to_gold_row, write_csv_atomic
from goldie_cli.schema import GOLD_COLUMNS


def test_normalize_author_rasses_list():
    out = normalize_author({"name": " Jane ", "rasses": ["MIT", "", "CERN"]})
    assert out == {"name": "Jane", "rasses": "MIT | CERN", "corresponding_author": False}


def test_normalize_author_affiliations_fallback_and_alias():
    out = normalize_author({"name": "X", "affiliations": ["A"], "is_corresponding": True})
    assert out["rasses"] == "A"
    assert out["corresponding_author"] is True


def test_to_gold_row_status_true_when_clean():
    row = to_gold_row(no=1, doi="10.1/x", link="https://doi.org/10.1/x",
                      extraction={"authors": [{"name": "A"}], "abstract": "hi"})
    assert row["Status"] == "TRUE"
    assert json.loads(row["Authors"])[0]["name"] == "A"
    assert row["Abstract"] == "hi"
    assert set(row) == set(GOLD_COLUMNS)


def test_to_gold_row_status_false_on_botcheck_or_error():
    bot = to_gold_row(no=2, doi="d", link="l",
                      extraction={"authors": [], "has_bot_check": True})
    assert bot["Status"] == "FALSE"
    assert bot["Has Bot Check"] == "TRUE"
    err = to_gold_row(no=3, doi="d", link="l", extraction=None, error="boom")
    assert err["Status"] == "FALSE"
    assert err["Notes"] == "boom"


def test_write_csv_atomic_roundtrip(tmp_path):
    out = tmp_path / "sub" / "ai-goldie-1.csv"
    rows = [to_gold_row(no=1, doi="10.1/a", link="L", extraction={"authors": [{"name": "A"}]})]
    write_csv_atomic(out, rows)
    assert out.exists()
    assert not out.with_suffix(out.suffix + ".tmp").exists()  # tmp cleaned up
    with out.open() as f:
        got = list(csv.DictReader(f))
    assert got[0]["DOI"] == "10.1/a"
    assert list(got[0]) == GOLD_COLUMNS


def test_read_source_rows(tmp_path):
    p = tmp_path / "corpus.csv"
    p.write_text("No,DOI,Link\n1,10.1/a,https://doi.org/10.1/a\n", encoding="utf-8")
    rows = read_source_rows(p)
    assert rows[0]["DOI"] == "10.1/a"
