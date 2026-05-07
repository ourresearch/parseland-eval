"""Unit tests for merge_livefetch.py — gold-aware override guardrail."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from merge_livefetch import (  # noqa: E402
    main,
    merge,
)


# ---- helpers ---------------------------------------------------------------


_FIELDS = [
    "No", "DOI", "Link", "Authors", "Abstract", "PDF URL",
    "Status", "Notes", "Has Bot Check", "Resolves To PDF",
    "broken_doi", "no english",
]


def _write_csv(path: Path, rows: list[dict]) -> Path:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in _FIELDS})
    return path


def _load_csv(path: Path) -> list[dict]:
    return list(csv.DictReader(path.open()))


# ---- gold-empty guardrail (the v4i6.14 case) -------------------------------


def test_skips_field_when_gold_says_na(tmp_path: Path):
    """Worked example: train DOI 10.36838/v4i6.14 (terra-docs IJHSR).

    Gold deliberately marks Authors=N/A and Abstract=N/A because the DOI
    resolves directly to a PDF. The diff_goldie.py comparator treats
    both-empty as a match, so the AI baseline's empty Authors/Abstract
    matches gold. Live-fetch happens to extract content from the PDF
    metadata. Without --gold, the merger fills baseline-empty fields and
    flips a match into a miss. With --gold, the fill is skipped.
    """
    gold = _write_csv(tmp_path / "gold.csv", [{
        "No": "1",
        "DOI": "10.36838/v4i6.14",
        "Authors": "N/A",
        "Abstract": "N/A",
        "PDF URL": "https://terra-docs.s3.us-east-2.amazonaws.com/.../paper.pdf",
    }])
    baseline = _write_csv(tmp_path / "baseline.csv", [{
        "No": "1",
        "DOI": "10.36838/v4i6.14",
        "Authors": "[]",
        "Abstract": "",
        "PDF URL": "",
    }])
    delta = _write_csv(tmp_path / "delta.csv", [{
        "No": "1",
        "DOI": "10.36838/v4i6.14",
        "Authors": json.dumps([
            {"name": "Salim Asfirane", "rasses": "SATIE-CNRS", "corresponding_author": False},
        ]),
        "Abstract": "This paper presents a study on a novel design …",
        "PDF URL": "",
    }])
    out = tmp_path / "merged.csv"

    merge(baseline, [delta], out, gold)

    merged = _load_csv(out)[0]
    assert merged["Authors"] == "[]", "gold-N/A Authors must not be filled"
    assert merged["Abstract"] == "", "gold-N/A Abstract must not be filled"


def test_fills_field_when_gold_has_content_and_baseline_empty(tmp_path: Path):
    """The legitimate use case: gold has content, baseline is empty (the AI
    missed it), delta supplies the value. Merge should fill normally.
    """
    gold = _write_csv(tmp_path / "gold.csv", [{
        "No": "1",
        "DOI": "10.1234/example",
        "Authors": json.dumps([
            {"name": "Real Author", "rasses": "Real University", "corresponding_author": True},
        ]),
        "Abstract": "Real abstract text.",
        "PDF URL": "https://publisher.example.com/paper.pdf",
    }])
    baseline = _write_csv(tmp_path / "baseline.csv", [{
        "No": "1",
        "DOI": "10.1234/example",
        "Authors": "[]",
        "Abstract": "",
        "PDF URL": "",
    }])
    delta = _write_csv(tmp_path / "delta.csv", [{
        "No": "1",
        "DOI": "10.1234/example",
        "Authors": json.dumps([
            {"name": "Real Author", "rasses": "Real University", "corresponding_author": True},
        ]),
        "Abstract": "Real abstract text.",
        "PDF URL": "https://publisher.example.com/paper.pdf",
    }])
    out = tmp_path / "merged.csv"

    merge(baseline, [delta], out, gold)

    merged = _load_csv(out)[0]
    assert "Real Author" in merged["Authors"]
    assert merged["Abstract"] == "Real abstract text."
    assert merged["PDF URL"] == "https://publisher.example.com/paper.pdf"


def test_main_aborts_without_gold_flag(tmp_path: Path):
    """Without --gold, argparse should reject and main() should exit non-zero."""
    baseline = _write_csv(tmp_path / "baseline.csv", [])
    delta = _write_csv(tmp_path / "delta.csv", [])

    argv = [
        "merge_livefetch",
        "--baseline", str(baseline),
        "--deltas", str(delta),
        "--output", str(tmp_path / "out.csv"),
        # NOTE: no --gold
    ]
    saved_argv = sys.argv
    sys.argv = argv
    try:
        with pytest.raises(SystemExit) as exc:
            main()
        # argparse's "missing required" exits with code 2
        assert exc.value.code != 0
    finally:
        sys.argv = saved_argv


# ---- counterfactual baseline-not-empty + gold-N/A flag ---------------------


def test_warns_when_baseline_violates_gold_na_but_does_not_revert(tmp_path: Path, capsys):
    """If baseline has content where gold says N/A, the merger should leave it
    alone (no auto-revert) but emit a stderr warning. Manual edit / pollution
    is the user's call to fix; the merger doesn't silently rewrite."""
    gold = _write_csv(tmp_path / "gold.csv", [{
        "No": "1", "DOI": "10.36838/v4i6.14",
        "Authors": "N/A", "Abstract": "N/A",
        "PDF URL": "https://terra-docs.example/paper.pdf",
    }])
    baseline = _write_csv(tmp_path / "baseline.csv", [{
        "No": "1", "DOI": "10.36838/v4i6.14",
        "Authors": json.dumps([{"name": "Polluted", "rasses": "", "corresponding_author": False}]),
        "Abstract": "Some text that violates gold-N/A.",
        "PDF URL": "",
    }])
    delta = _write_csv(tmp_path / "delta.csv", [{
        "No": "1", "DOI": "10.36838/v4i6.14",
        "Authors": json.dumps([{"name": "From Delta", "rasses": "", "corresponding_author": False}]),
        "Abstract": "Delta abstract.", "PDF URL": "",
    }])
    out = tmp_path / "merged.csv"

    merge(baseline, [delta], out, gold)

    merged = _load_csv(out)[0]
    # Baseline content preserved (no auto-revert)
    assert "Polluted" in merged["Authors"]
    assert merged["Abstract"] == "Some text that violates gold-N/A."
    # Warning surfaced on stderr
    captured = capsys.readouterr()
    assert "contradict gold" in captured.err
