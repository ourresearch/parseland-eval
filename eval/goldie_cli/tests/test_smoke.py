"""Offline end-to-end smoke: corpus rows → stub backend → transforms → gold CSV.

No network. Exercises the full spine (pipeline + backend seam + retry + transforms +
checkpoint + atomic CSV) and the resume invariant.
"""
from __future__ import annotations

import asyncio
import csv

from goldie_cli.backends.stub import StubBackend
from goldie_cli.pipeline import run_single_batch
from goldie_cli.schema import GOLD_COLUMNS

ROWS = [
    {"No": "1", "DOI": "10.1/a", "Link": "https://doi.org/10.1/a"},
    {"No": "2", "DOI": "10.1/b", "Link": "https://doi.org/10.1/b"},
]


def _paths(tmp_path):
    return {
        "out_csv": tmp_path / "batches" / "batch-001" / "ai-goldie.csv",
        "checkpoint_path": tmp_path / "checkpoints" / "batch-001.partial.jsonl",
        "failures_path": tmp_path / "failures" / "batch-001.failures.jsonl",
    }


def test_offline_smoke_produces_gold_csv(tmp_path):
    backend = StubBackend()
    p = _paths(tmp_path)
    summary = asyncio.run(run_single_batch(
        backend, ROWS, prompt="p", concurrency=4, skip_meta_tags=True, **p,
    ))
    assert summary["rows"] == 2
    assert summary["landed"] == 2
    assert summary["failed"] == 0
    assert backend.calls == 2

    with p["out_csv"].open() as f:
        got = list(csv.DictReader(f))
    assert [r["DOI"] for r in got] == ["10.1/a", "10.1/b"]
    assert list(got[0]) == GOLD_COLUMNS
    assert got[0]["Status"] == "TRUE"
    # stub returns one author with an affiliation
    assert "Stub Author" in got[0]["Authors"]


def test_resume_skips_landed_rows(tmp_path):
    p = _paths(tmp_path)
    first = StubBackend()
    asyncio.run(run_single_batch(first, ROWS, prompt="p", concurrency=4,
                                 skip_meta_tags=True, **p))
    assert first.calls == 2

    # Second run with a fresh backend: everything already in the checkpoint → 0 calls.
    second = StubBackend()
    summary = asyncio.run(run_single_batch(second, ROWS, prompt="p", concurrency=4,
                                           skip_meta_tags=True, **p))
    assert second.calls == 0
    assert summary["landed"] == 2


def test_failure_row_is_blank_false_and_logged(tmp_path):
    from goldie_cli.backends.base import ExtractionResult

    def responder(doi, link):
        if doi == "10.1/b":
            raise RuntimeError("boom")
        return ExtractionResult(extraction={"authors": [{"name": "Ok"}]}, cost_usd=0.0)

    backend = StubBackend(responder=responder)
    p = _paths(tmp_path)
    from goldie_cli.backends.base import RetryPolicy
    summary = asyncio.run(run_single_batch(
        backend, ROWS, prompt="p", concurrency=4, skip_meta_tags=True,
        policy=RetryPolicy(retry_cap=0), **p,
    ))
    assert summary["failed"] == 1
    with p["out_csv"].open() as f:
        got = {r["DOI"]: r for r in csv.DictReader(f)}
    assert got["10.1/b"]["Status"] == "FALSE"
    assert got["10.1/a"]["Status"] == "TRUE"
    assert p["failures_path"].exists()
