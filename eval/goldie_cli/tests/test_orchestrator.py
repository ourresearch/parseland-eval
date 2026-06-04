from __future__ import annotations

import asyncio
import csv

from goldie_cli.backends.base import ExtractionResult
from goldie_cli.budget import CostTracker
from goldie_cli.orchestrator import run_corpus
from goldie_cli.rundir import RunDir, utc_stamp


class CountingBackend:
    """Tracks max concurrent in-flight extracts to verify the global cap."""
    name = "counting"

    def __init__(self, cost: float = 0.0):
        self.inflight = 0
        self.max_inflight = 0
        self.calls = 0
        self._cost = cost

    async def extract(self, doi, link, *, html=None, schema=None, prompt=""):
        self.calls += 1
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            await asyncio.sleep(0.01)
            return ExtractionResult(extraction={"authors": [{"name": "A"}]},
                                    cost_usd=self._cost, raw_html=None)
        finally:
            self.inflight -= 1

    async def aclose(self):
        return None


def _batches(n_batches, per):
    out = []
    no = 1
    for b in range(1, n_batches + 1):
        rows = []
        for _ in range(per):
            rows.append({"No": str(no), "DOI": f"10.1/{no}", "Link": f"https://doi.org/10.1/{no}"})
            no += 1
        out.append((b, rows))
    return out


def test_cost_tracker():
    t = CostTracker(2.0)
    assert not t.would_exceed()
    t.add(1.0); assert not t.would_exceed()
    t.add(1.0); assert t.would_exceed()
    assert t.spent == 2.0 and t.remaining == 0.0
    assert CostTracker(None).would_exceed() is False


def test_rundir_layout(tmp_path):
    rd = RunDir.create("smoke", runs_dir=tmp_path, stamp="20260604T000000Z")
    assert rd.root == tmp_path / "smoke-20260604T000000Z"
    assert rd.batch_csv(1).name == "ai-goldie.csv"
    assert "batch-001" in str(rd.batch_csv(1))
    assert rd.checkpoint(7).name == "batch-007.partial.jsonl"
    rd.write_manifest({"status": "complete"})
    assert rd.read_manifest()["status"] == "complete"
    assert len(utc_stamp()) == len("20260604T000000Z")


def test_global_concurrency_cap_across_batches(tmp_path):
    backend = CountingBackend()
    rd = RunDir.create("c", runs_dir=tmp_path, stamp="t")
    manifest = asyncio.run(run_corpus(
        backend, _batches(4, 5), rd, prompt="p",
        concurrency=3, batch_concurrency=4, skip_meta_tags=True,
    ))
    assert backend.calls == 20
    assert backend.max_inflight <= 3          # global cap respected across 4 batches
    assert backend.max_inflight >= 2          # genuine concurrency happened
    assert manifest["status"] == "complete"
    assert manifest["rows"] == 20 and manifest["landed"] == 20


def test_merged_csv_sorted(tmp_path):
    rd = RunDir.create("m", runs_dir=tmp_path, stamp="t")
    asyncio.run(run_corpus(CountingBackend(), _batches(2, 3), rd, prompt="p",
                           concurrency=4, batch_concurrency=2, skip_meta_tags=True))
    with rd.merged_csv.open() as f:
        nos = [int(r["No"]) for r in csv.DictReader(f)]
    assert nos == [1, 2, 3, 4, 5, 6]


def test_budget_preempts(tmp_path):
    backend = CountingBackend(cost=1.0)
    rd = RunDir.create("b", runs_dir=tmp_path, stamp="t")
    manifest = asyncio.run(run_corpus(
        backend, _batches(1, 5), rd, prompt="p",
        concurrency=1, batch_concurrency=1, max_cost_usd=2.0, skip_meta_tags=True,
    ))
    assert backend.calls == 2          # stops after spend hits the cap
    assert manifest["landed"] == 2
    assert manifest["cost_usd"] == 2.0


def test_shutdown_preset_skips_all(tmp_path):
    backend = CountingBackend()
    rd = RunDir.create("s", runs_dir=tmp_path, stamp="t")
    ev = asyncio.Event()
    ev.set()
    manifest = asyncio.run(run_corpus(
        backend, _batches(2, 3), rd, prompt="p", concurrency=4,
        batch_concurrency=2, skip_meta_tags=True, shutdown_event=ev,
    ))
    assert backend.calls == 0
    assert manifest["status"] == "interrupted"
    assert manifest["landed"] == 0
