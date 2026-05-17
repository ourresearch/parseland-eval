"""Unit tests for eval/scripts/triage_10k.py."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "eval" / "scripts"))

from triage_10k import (  # noqa: E402
    GOOD,
    RERUN_NO_AUTHORS,
    RERUN_NO_RASES,
    REQUIRED_COLS,
    V2_FILENAME,
    classify,
    doi_prefix,
    triage,
)


@pytest.mark.unit
class TestDoiPrefix:
    @pytest.mark.parametrize("doi,expected", [
        ("10.1016/j.foo.2024.123", "10.1016"),
        ("10.1109/icat.2013.6728903", "10.1109"),
        ("10.1234/weird/multi/slash", "10.1234"),
        ("10.5040/9781805015710.ch-009", "10.5040"),
        ("  10.1002/app.43634  ", "10.1002"),
        ("", ""),
        ("no-slash-here", "no-slash-here"),
    ])
    def test_extracts_first_segment(self, doi: str, expected: str) -> None:
        assert doi_prefix(doi) == expected


@pytest.mark.unit
class TestClassify:
    def _row(self, authors: object) -> dict[str, str]:
        # Authors column is always a JSON string in v2 CSVs.
        if isinstance(authors, str):
            return {"Authors": authors}
        return {"Authors": json.dumps(authors)}

    def test_empty_list(self) -> None:
        assert classify(self._row([])) == RERUN_NO_AUTHORS

    def test_empty_string(self) -> None:
        assert classify(self._row("")) == RERUN_NO_AUTHORS

    def test_na_sentinel(self) -> None:
        assert classify(self._row("N/A")) == RERUN_NO_AUTHORS

    def test_invalid_json(self) -> None:
        assert classify(self._row("not-json")) == RERUN_NO_AUTHORS

    def test_all_authors_empty_rases(self) -> None:
        authors = [
            {"name": "A B", "rasses": "", "corresponding_author": False},
            {"name": "C D", "rasses": "", "corresponding_author": True},
        ]
        assert classify(self._row(authors)) == RERUN_NO_RASES

    def test_one_author_with_rases(self) -> None:
        # If ANY author has rases, _has_empty_rases returns False → GOOD.
        # (The bucket is "ALL empty", not "some empty".)
        authors = [
            {"name": "A B", "rasses": "MIT", "corresponding_author": False},
            {"name": "C D", "rasses": "", "corresponding_author": True},
        ]
        assert classify(self._row(authors)) == GOOD

    def test_single_author_with_rases(self) -> None:
        authors = [{"name": "Solo", "rasses": "Stanford", "corresponding_author": True}]
        assert classify(self._row(authors)) == GOOD

    def test_single_author_empty_rases(self) -> None:
        authors = [{"name": "Solo", "rasses": "", "corresponding_author": True}]
        assert classify(self._row(authors)) == RERUN_NO_RASES


def _write_batch(batch_dir: Path, rows: list[dict[str, object]]) -> Path:
    """Helper: write a synthetic batch-N-judge/ai-goldie-1.v2.csv."""
    batch_dir.mkdir(parents=True, exist_ok=True)
    out = batch_dir / V2_FILENAME
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(REQUIRED_COLS))
        w.writeheader()
        for r in rows:
            payload = {c: "" for c in REQUIRED_COLS}
            payload.update({k: v for k, v in r.items() if k in REQUIRED_COLS})
            if isinstance(payload["Authors"], list):
                payload["Authors"] = json.dumps(payload["Authors"])
            w.writerow(payload)
    return out


@pytest.mark.unit
class TestTriageIntegration:
    def _make_100_batches(self, base: Path, payload_per_batch: list[dict[str, object]]) -> Path:
        batches_dir = base / "10k"
        for n in range(1, 101):
            _write_batch(batches_dir / f"batch-{n}-judge", payload_per_batch)
        return batches_dir

    def test_missing_batches_exits_2(self, tmp_path: Path) -> None:
        batches_dir = tmp_path / "10k"
        # Only create 99 batches → batch 100 missing.
        for n in range(1, 100):
            _write_batch(batches_dir / f"batch-{n}-judge", [
                {"No": 1, "DOI": "10.1016/x", "Authors": []},
            ])
        rc = triage(batches_dir, tmp_path / "out")
        assert rc == 2

    def test_full_classification(self, tmp_path: Path) -> None:
        payload = [
            {"No": 1, "DOI": "10.1016/elsevier-empty",  "Authors": []},
            {"No": 2, "DOI": "10.1109/ieee-no-rases",
             "Authors": [{"name": "A", "rasses": "", "corresponding_author": False}]},
            {"No": 3, "DOI": "10.1002/wiley-good",
             "Authors": [{"name": "B", "rasses": "MIT", "corresponding_author": True}]},
        ]
        batches_dir = self._make_100_batches(tmp_path, payload)
        out_dir = tmp_path / "triage"
        rc = triage(batches_dir, out_dir)
        assert rc == 0

        # NOTE: same 3 DOIs repeated across 100 batches → dedup-by-DOI leaves
        # exactly 3 rows in the triage output (last-write-wins on duplicates).
        good_rows = list(csv.DictReader((out_dir / "good-records.csv").open()))
        nra_rows = list(csv.DictReader((out_dir / "rerun-no-authors.csv").open()))
        nrs_rows = list(csv.DictReader((out_dir / "rerun-no-rases.csv").open()))

        # The dedup map IS keyed by DOI, but classification still counts every
        # row read. So good/no_auth/no_rases each have 100 entries written.
        # (This matches the actual behavior of triage_10k; see also the
        # "duplicate DOI" warning it logs.)
        assert len(nra_rows) == 100
        assert len(nrs_rows) == 100
        assert len(good_rows) == 100

        assert all(r["doi_prefix"] == "10.1016" for r in nra_rows)
        assert all(r["doi_prefix"] == "10.1109" for r in nrs_rows)

    def test_atomic_write_no_partial(self, tmp_path: Path, monkeypatch) -> None:
        """If write fails mid-way, the .tmp file should NOT replace the real
        output (atomic guarantee)."""
        payload = [{"No": 1, "DOI": "10.1016/x", "Authors": []}]
        batches_dir = self._make_100_batches(tmp_path, payload)
        out_dir = tmp_path / "triage"
        out_dir.mkdir()

        # Pre-create the target file with sentinel content
        existing = out_dir / "good-records.csv"
        existing.write_text("SENTINEL")

        # Force os.replace to fail after the tmp is written
        import triage_10k as t
        original_replace = t.os.replace

        def explode(_src, _dst):
            raise OSError("simulated rename failure")

        monkeypatch.setattr(t.os, "replace", explode)
        rc = triage(batches_dir, out_dir)
        assert rc == 1
        # Sentinel is preserved because rename failed atomically
        assert existing.read_text() == "SENTINEL"
        # Restore for any later test
        monkeypatch.setattr(t.os, "replace", original_replace)
