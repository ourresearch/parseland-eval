"""Unit tests for eval/scripts/merge_reruns.py."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "eval" / "scripts"))

from merge_reruns import _author_count, _is_better_rerun, _row_stats, merge  # noqa: E402
from triage_10k import REQUIRED_COLS  # noqa: E402


def _row(doi: str, authors: object, **extra: str) -> dict[str, str]:
    payload = {c: "" for c in REQUIRED_COLS}
    payload["DOI"] = doi
    payload["Link"] = f"https://doi.org/{doi}"
    if isinstance(authors, str):
        payload["Authors"] = authors
    else:
        payload["Authors"] = json.dumps(authors)
    payload.update(extra)
    return payload


def _write_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(REQUIRED_COLS))
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in REQUIRED_COLS})
    return path


@pytest.mark.unit
class TestAuthorCount:
    def test_empty(self) -> None:
        assert _author_count("") == 0
        assert _author_count("[]") == 0
        assert _author_count(None) == 0
        assert _author_count("not-json") == 0

    def test_counts_named_only(self) -> None:
        s = json.dumps([
            {"name": "A", "rasses": ""},
            {"name": "", "rasses": "skip me"},
            {"name": "  ", "rasses": ""},
            {"name": "B", "rasses": "MIT"},
        ])
        assert _author_count(s) == 2

    def test_handles_non_list(self) -> None:
        assert _author_count('{"name": "A"}') == 0


@pytest.mark.unit
class TestRowStats:
    def test_empty(self) -> None:
        ha, hr = _row_stats(_row("10.1016/x", []))
        assert ha is False and hr is False

    def test_authors_only(self) -> None:
        ha, hr = _row_stats(_row("10.1016/x", [{"name": "A", "rasses": ""}]))
        assert ha is True and hr is False

    def test_authors_and_rases(self) -> None:
        ha, hr = _row_stats(_row("10.1016/x", [{"name": "A", "rasses": "MIT"}]))
        assert ha is True and hr is True


@pytest.mark.unit
class TestMergeInsertion:
    def test_insert_new_doi(self, tmp_path: Path) -> None:
        good = _write_csv(tmp_path / "good.csv", [
            _row("10.1002/x", [{"name": "A", "rasses": "MIT"}]),
        ])
        rerun = _write_csv(tmp_path / "rerun-elsevier.csv", [
            _row("10.1016/y", [{"name": "B", "rasses": "Stanford"}]),
        ])
        out = tmp_path / "merged.csv"
        rc = merge(good, [rerun], out)
        assert rc == 0
        rows = list(csv.DictReader(out.open()))
        assert len(rows) == 2
        dois = {r["DOI"] for r in rows}
        assert dois == {"10.1002/x", "10.1016/y"}

    def test_rerun_redundant_with_good_does_not_overwrite(self, tmp_path: Path) -> None:
        good = _write_csv(tmp_path / "good.csv", [
            _row("10.1016/x", [{"name": "ORIGINAL", "rasses": "GOOD"}]),
        ])
        rerun = _write_csv(tmp_path / "rerun.csv", [
            _row("10.1016/x", [{"name": "SHOULD_NOT_OVERWRITE", "rasses": "BAD"}]),
        ])
        out = tmp_path / "merged.csv"
        rc = merge(good, [rerun], out)
        assert rc == 0
        rows = list(csv.DictReader(out.open()))
        assert len(rows) == 1
        # Good baseline preserved
        authors = json.loads(rows[0]["Authors"])
        assert authors[0]["name"] == "ORIGINAL"

    def test_empty_rerun_never_inserted_in_a_way_that_downgrades(self, tmp_path: Path) -> None:
        """If rerun has empty authors, it must not appear in the merged
        output as overwriting any non-empty existing entry. Since reruns
        only insert when the DOI is absent from good, an empty rerun for
        a new DOI is still inserted (it's the only data we have).
        That row's empty Authors is acceptable — it represents a DOI we
        tried and failed on; merge stats will show it. What MUST NOT happen
        is overwriting good or a populated earlier rerun."""
        good = _write_csv(tmp_path / "good.csv", [
            _row("10.1002/x", [{"name": "A", "rasses": "MIT"}]),
        ])
        rerun_a = _write_csv(tmp_path / "rerun-a.csv", [
            _row("10.1016/y", [{"name": "Recovered", "rasses": "Caltech"}]),
        ])
        rerun_b = _write_csv(tmp_path / "rerun-b.csv", [
            _row("10.1016/y", []),  # empty rerun for same DOI
        ])
        out = tmp_path / "merged.csv"
        rc = merge(good, [rerun_a, rerun_b], out)
        assert rc == 0
        rows = list(csv.DictReader(out.open()))
        recovered = next(r for r in rows if r["DOI"] == "10.1016/y")
        authors = json.loads(recovered["Authors"])
        assert authors[0]["name"] == "Recovered"


@pytest.mark.unit
class TestMergeConflict:
    def test_more_authors_wins(self, tmp_path: Path) -> None:
        good = _write_csv(tmp_path / "good.csv", [])
        rerun_a = _write_csv(tmp_path / "rerun-a.csv", [
            _row("10.1016/y", [{"name": "Solo", "rasses": ""}]),
        ])
        rerun_b = _write_csv(tmp_path / "rerun-b.csv", [
            _row("10.1016/y", [
                {"name": "A", "rasses": "MIT"},
                {"name": "B", "rasses": "Stanford"},
                {"name": "C", "rasses": "Caltech"},
            ]),
        ])
        out = tmp_path / "merged.csv"
        rc = merge(good, [rerun_a, rerun_b], out)
        assert rc == 0
        rows = list(csv.DictReader(out.open()))
        recovered = next(r for r in rows if r["DOI"] == "10.1016/y")
        assert _author_count(recovered["Authors"]) == 3

    def test_tie_alphabetical_first_wins(self, tmp_path: Path) -> None:
        good = _write_csv(tmp_path / "good.csv", [])
        rerun_a = _write_csv(tmp_path / "rerun-aaa.csv", [
            _row("10.1016/y", [{"name": "AAA-author", "rasses": "MIT"}]),
        ])
        rerun_b = _write_csv(tmp_path / "rerun-bbb.csv", [
            _row("10.1016/y", [{"name": "BBB-author", "rasses": "Stanford"}]),
        ])
        # Pass in non-alphabetical order; merge() sorts internally.
        out = tmp_path / "merged.csv"
        rc = merge(good, [rerun_b, rerun_a], out)
        assert rc == 0
        rows = list(csv.DictReader(out.open()))
        recovered = next(r for r in rows if r["DOI"] == "10.1016/y")
        authors = json.loads(recovered["Authors"])
        # rerun-aaa.csv is alphabetically first, so its row is the incumbent;
        # rerun-bbb.csv is the candidate with the same author count → tie →
        # incumbent stays.
        assert authors[0]["name"] == "AAA-author"


@pytest.mark.unit
class TestMergeIoErrors:
    def test_missing_good_exits_2(self, tmp_path: Path) -> None:
        rc = merge(tmp_path / "missing.csv", [], tmp_path / "out.csv")
        assert rc == 2

    def test_missing_rerun_exits_2(self, tmp_path: Path) -> None:
        good = _write_csv(tmp_path / "good.csv", [])
        rc = merge(good, [tmp_path / "missing.csv"], tmp_path / "out.csv")
        assert rc == 2

    def test_atomic_failure_returns_1(self, tmp_path: Path, monkeypatch) -> None:
        good = _write_csv(tmp_path / "good.csv", [
            _row("10.1002/x", [{"name": "A", "rasses": "MIT"}]),
        ])
        rerun = _write_csv(tmp_path / "rerun.csv", [])
        # Create the output file with sentinel content
        out = tmp_path / "merged.csv"
        out.write_text("SENTINEL")

        import merge_reruns as m

        def explode(_src, _dst):
            raise OSError("rename failure simulated")

        monkeypatch.setattr(m.os, "replace", explode)
        rc = merge(good, [rerun], out)
        assert rc == 1
        # Sentinel preserved
        assert out.read_text() == "SENTINEL"


@pytest.mark.unit
class TestIsBetterRerun:
    def test_more_authors(self) -> None:
        a = _row("x", [{"name": "A", "rasses": ""}, {"name": "B", "rasses": ""}])
        b = _row("x", [{"name": "A", "rasses": ""}])
        assert _is_better_rerun(a, b) is True

    def test_tie(self) -> None:
        a = _row("x", [{"name": "A", "rasses": ""}])
        b = _row("x", [{"name": "B", "rasses": ""}])
        assert _is_better_rerun(a, b) is False  # ties go to incumbent
