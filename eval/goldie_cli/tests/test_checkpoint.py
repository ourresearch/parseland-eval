from __future__ import annotations

from goldie_cli.checkpoint import append_failure, append_partial, count_lines, load_partial


def test_partial_last_write_wins(tmp_path):
    p = tmp_path / ".checkpoint" / "ai-goldie-1.partial.jsonl"
    append_partial(p, {"DOI": "10.1/a", "Abstract": "v1"})
    append_partial(p, {"DOI": "10.1/b", "Abstract": "b"})
    append_partial(p, {"DOI": "10.1/a", "Abstract": "v2"})  # retry overwrites
    landed = load_partial(p)
    assert set(landed) == {"10.1/a", "10.1/b"}
    assert landed["10.1/a"]["Abstract"] == "v2"
    assert count_lines(p) == 3


def test_load_partial_missing_file(tmp_path):
    assert load_partial(tmp_path / "nope.jsonl") == {}
    assert count_lines(tmp_path / "nope.jsonl") == 0


def test_load_partial_skips_bad_lines(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"DOI":"10.1/a"}\nnot-json\n\n{"DOI":"10.1/c"}\n', encoding="utf-8")
    assert set(load_partial(p)) == {"10.1/a", "10.1/c"}


def test_append_failure(tmp_path):
    p = tmp_path / "ai-goldie-1.failures.jsonl"
    append_failure(p, {"DOI": "10.1/a", "No": 1, "error": "boom", "retries": 3})
    assert count_lines(p) == 1
