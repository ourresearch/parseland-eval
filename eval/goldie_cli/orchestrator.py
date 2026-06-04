"""Across-batch orchestrator — the concurrency-gap fix.

The old shell loop ran batches strictly sequentially while only DOIs within a batch
fanned out. Here a single GLOBAL DOI semaphore is threaded into every batch's
``run_single_batch`` (so overlapping batches never exceed the account/cloud cap), plus a
BATCH semaphore bounding how many batch-pipelines are open at once. Budget is pre-emptive
and shared; SIGINT/SIGTERM stop scheduling and flush. Writes merged.csv + manifest.
"""
from __future__ import annotations

import asyncio
import csv
import logging
from typing import Any, Sequence

from .budget import CostTracker
from .backends.base import Backend, RetryPolicy
from .io import write_csv_atomic
from .pipeline import run_single_batch
from .rundir import RunDir
from .schema import GOLD_COLUMNS

log = logging.getLogger("goldie")


async def run_corpus(
    backend: Backend,
    batches: Sequence[tuple[int, list[dict[str, str]]]],
    run_dir: RunDir,
    *,
    prompt: str,
    corpus: str = "corpus",
    model: str = "",
    schema: dict[str, Any] | None = None,
    policy: RetryPolicy | None = None,
    concurrency: int = 200,
    batch_concurrency: int = 4,
    max_cost_usd: float | None = None,
    skip_meta_tags: bool = False,
    shutdown_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    policy = policy or RetryPolicy()
    global_sem = asyncio.Semaphore(concurrency)   # caps in-flight DOIs across ALL batches
    batch_sem = asyncio.Semaphore(batch_concurrency)
    tracker = CostTracker(max_cost_usd)

    async def run_one(no: int, rows: list[dict[str, str]]) -> dict[str, Any]:
        async with batch_sem:
            summary = await run_single_batch(
                backend, rows,
                out_csv=run_dir.batch_csv(no),
                checkpoint_path=run_dir.checkpoint(no),
                failures_path=run_dir.failures(no),
                prompt=prompt, schema=schema, policy=policy,
                sem=global_sem, skip_meta_tags=skip_meta_tags,
                cost_tracker=tracker, shutdown_event=shutdown_event,
            )
            summary["batch"] = no
            return summary

    summaries = await asyncio.gather(*(run_one(no, rows) for no, rows in batches))

    interrupted = bool(shutdown_event is not None and shutdown_event.is_set())
    merged_rows = _concat_batches([run_dir.batch_csv(no) for no, _ in batches])
    write_csv_atomic(run_dir.merged_csv, merged_rows)

    total_rows = sum(s["rows"] for s in summaries)
    landed = sum(s["landed"] for s in summaries)
    failed = sum(s["failed"] for s in summaries)
    manifest = {
        "corpus": corpus,
        "model": model,
        "tiers": [backend.name],
        "concurrency": concurrency,
        "batch_concurrency": batch_concurrency,
        "max_cost_usd": max_cost_usd,
        "batches": len(summaries),
        "rows": total_rows,
        "landed": landed,
        "failed": failed,
        "cost_usd": tracker.spent,
        "status": "interrupted" if interrupted else "complete",
        "merged_csv": str(run_dir.merged_csv),
    }
    run_dir.write_manifest(manifest)
    log.info("corpus done: %d batches, %d/%d landed, %d failed, $%.2f, status=%s",
             len(summaries), landed, total_rows, failed, tracker.spent, manifest["status"])
    return manifest


def _concat_batches(csv_paths: list) -> list[dict[str, Any]]:
    """Concatenate per-batch CSVs into one corpus list, sorted by ``No``."""
    rows: list[dict[str, Any]] = []
    for p in csv_paths:
        if not p.exists():
            continue
        with p.open("r", encoding="utf-8", newline="") as f:
            rows.extend(csv.DictReader(f))
    rows.sort(key=lambda r: int(r.get("No") or 0))
    # keep only canonical columns (defensive against stray keys)
    return [{k: r.get(k, "") for k in GOLD_COLUMNS} for r in rows]
