"""Single-batch extraction pipeline: backend → post-LLM transforms → gold CSV.

Resumable (DOI-keyed checkpoint), transparent (separate failures log), atomic CSV.
Backend-agnostic, so the offline ``StubBackend`` exercises the whole spine with no
network. The across-batch orchestrator (Phase 5) wraps ``run_single_batch``.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from .backends.base import Backend, RetryPolicy, extract_with_retries
from .checkpoint import append_failure, append_partial, load_partial
from .io import apply_transform_dict, to_gold_row, to_transform_dict, write_csv_atomic
from .schema import extraction_json_schema
from .transforms import TransformContext, apply_transforms

log = logging.getLogger("goldie")


async def extract_one(
    backend: Backend,
    sem: asyncio.Semaphore,
    policy: RetryPolicy,
    *,
    no: int,
    doi: str,
    link: str,
    prompt: str,
    schema: dict[str, Any] | None = None,
    skip_meta_tags: bool = False,
    run_transforms: bool = True,
    gate=None,
) -> tuple[dict[str, Any] | None, float | None, str | None]:
    """Extract one DOI and return ``(gold_row, cost_usd, error)``.

    Returns ``(None, cost, "__skipped__")`` when the budget/shutdown ``gate`` fired inside
    the semaphore — the caller must NOT land a skipped DOI. Page evidence only: transforms
    run over ``result.raw_html``; if the backend supplied no HTML, HTML transforms no-op.
    """
    schema = schema if schema is not None else extraction_json_schema()
    res = await extract_with_retries(
        backend, doi=doi, link=link, html=None, schema=schema, prompt=prompt,
        policy=policy, sem=sem, gate=gate,
    )
    if res.meta.get("skipped"):
        return None, res.cost_usd, "__skipped__"
    if res.extraction is None:
        return to_gold_row(no=no, doi=doi, link=link, extraction=None, error=res.error), res.cost_usd, res.error

    if run_transforms:
        ctx = TransformContext(
            html=res.raw_html or "",
            doi=doi,
            link=link,
            resolved_url=res.meta.get("resolved_url"),
            # No page HTML → nothing for HTML/meta transforms to read; skip them.
            skip_meta_tags=skip_meta_tags or not res.raw_html,
        )
        cap = to_transform_dict(res.extraction)
        apply_transforms(cap, ctx)
        apply_transform_dict(res.extraction, cap)

    return to_gold_row(no=no, doi=doi, link=link, extraction=res.extraction, error=None), res.cost_usd, None


async def run_single_batch(
    backend: Backend,
    rows: list[dict[str, str]],
    *,
    out_csv: Path,
    checkpoint_path: Path,
    failures_path: Path,
    prompt: str,
    schema: dict[str, Any] | None = None,
    policy: RetryPolicy | None = None,
    concurrency: int = 8,
    sem: asyncio.Semaphore | None = None,
    skip_meta_tags: bool = False,
    run_transforms: bool = True,
    cost_tracker: Any = None,
    shutdown_event: Any = None,
) -> dict[str, Any]:
    """Run one batch of ``{No,DOI,Link}`` rows. Resumes from the checkpoint, writes a
    sorted CSV with one row per input (blank FALSE rows for any never-landed DOI), and
    returns a summary. Invariant: ``len(landed) == len(rows)`` after a clean run.
    """
    policy = policy or RetryPolicy()
    sem = sem or asyncio.Semaphore(concurrency)
    landed = load_partial(checkpoint_path)
    pending = [r for r in rows if (r.get("DOI") or "").strip() not in landed]
    log.info("batch: %d rows, %d already landed, %d pending", len(rows), len(landed), len(pending))

    cost_total = 0.0
    failures = 0

    def _gate() -> bool:
        """Evaluated inside the semaphore: stop if shutting down or over budget."""
        if shutdown_event is not None and shutdown_event.is_set():
            return True
        if cost_tracker is not None and cost_tracker.would_exceed():
            return True
        return False

    async def _run(r: dict[str, str]) -> None:
        nonlocal cost_total, failures
        no = int(r.get("No") or 0)
        doi = (r.get("DOI") or "").strip()
        link = (r.get("Link") or "").strip() or f"https://doi.org/{doi}"
        gold_row, cost, error = await extract_one(
            backend, sem, policy, no=no, doi=doi, link=link, prompt=prompt,
            schema=schema, skip_meta_tags=skip_meta_tags, run_transforms=run_transforms,
            gate=_gate,
        )
        if error == "__skipped__":
            return  # gated: leave DOI unlanded (resumable), don't write a blank row
        append_partial(checkpoint_path, gold_row)
        if cost:
            cost_total += cost
            if cost_tracker is not None:
                cost_tracker.add(cost)
        if error:
            failures += 1
            append_failure(failures_path, {"No": no, "DOI": doi, "error": error})

    if pending:
        await asyncio.gather(*(_run(r) for r in pending))

    # Finalize: re-read checkpoint, emit one row per input in No order.
    landed = load_partial(checkpoint_path)
    final: list[dict[str, Any]] = []
    for r in sorted(rows, key=lambda x: int(x.get("No") or 0)):
        doi = (r.get("DOI") or "").strip()
        rec = landed.get(doi)
        if rec is None:
            rec = to_gold_row(no=int(r.get("No") or 0), doi=doi,
                              link=(r.get("Link") or "").strip(), extraction=None,
                              error="missing_from_checkpoint")
        final.append(rec)
    write_csv_atomic(out_csv, final)

    return {
        "rows": len(rows),
        "landed": len(landed),
        "failed": failures,
        "cost_usd": round(cost_total, 4),
        "csv": str(out_csv),
    }
