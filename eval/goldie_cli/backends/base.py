"""Backend protocol + the backend-agnostic retry/bot-check wrapper.

``Backend.extract`` performs ONE attempt and returns an ``ExtractionResult``.
``extract_with_retries`` reproduces the proven ``run_doi`` policy from
extract_batch_cloud.py:414-477 (exponential backoff + one bot-check retry) and is
shared by every tier, so retry behaviour lives in one place.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

RETRY_BACKOFF_SEC: tuple[float, ...] = (10.0, 60.0, 300.0)


@dataclass
class ExtractionResult:
    """Outcome of one extraction attempt.

    ``raw_html`` is the page evidence (when the backend can supply it) so downstream
    transforms can run; it is ``None`` for backends that only return structured output.
    ``meta`` carries task_id / duration_s / retries / steps for the report.
    """

    extraction: dict[str, Any] | None
    cost_usd: float | None = None
    error: str | None = None
    raw_html: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Backend(Protocol):
    name: str

    async def extract(
        self,
        doi: str,
        link: str,
        *,
        html: str | None,
        schema: dict[str, Any],
        prompt: str,
    ) -> ExtractionResult: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class RetryPolicy:
    retry_cap: int = 3
    backoff_sec: tuple[float, ...] = RETRY_BACKOFF_SEC
    bot_check_retry: bool = True


async def extract_with_retries(
    backend: Backend,
    *,
    doi: str,
    link: str,
    html: str | None,
    schema: dict[str, Any],
    prompt: str,
    policy: RetryPolicy,
    sem: asyncio.Semaphore,
    sleep=asyncio.sleep,
    gate=None,
) -> ExtractionResult:
    """Run ``backend.extract`` under ``sem`` with retries + one bot-check retry.

    Mirrors run_doi: on a thrown error or ``has_bot_check`` (when retries remain) it
    backs off and retries; otherwise returns. ``sleep`` is injectable for tests.

    ``gate`` (if given) is evaluated INSIDE the semaphore before each attempt — so a
    budget/shutdown check sees costs that prior in-flight DOIs have already landed. When
    it returns True the DOI is skipped (returned with ``meta.skipped`` and no backend
    call), leaving it unlanded and resumable rather than written as a blank row.
    """
    loop = asyncio.get_event_loop()
    start = loop.time()
    last_error: str | None = None
    last_cost: float | None = None
    last_meta: dict[str, Any] = {}

    for attempt in range(policy.retry_cap + 1):
        async with sem:
            if gate is not None and gate():
                return ExtractionResult(extraction=None, error="__skipped__",
                                        meta={"skipped": True})
            try:
                res = await backend.extract(doi, link, html=html, schema=schema, prompt=prompt)
                last_cost = res.cost_usd if res.cost_usd is not None else last_cost
                last_meta = res.meta or last_meta
                if res.extraction is None:
                    last_error = res.error or "no_structured_output"
                else:
                    bot = bool(res.extraction.get("has_bot_check"))
                    if bot and policy.bot_check_retry and attempt < policy.retry_cap:
                        last_error = "has_bot_check (retrying)"
                        await sleep(policy.backoff_sec[min(attempt, len(policy.backoff_sec) - 1)])
                        continue
                    res.meta = {**last_meta, "retries": attempt,
                                "duration_s": round(loop.time() - start, 2)}
                    return res
            except Exception as e:  # noqa: BLE001 - record + retry, never silently swallow
                last_error = f"{type(e).__name__}: {e}"
        if attempt < policy.retry_cap:
            await sleep(policy.backoff_sec[min(attempt, len(policy.backoff_sec) - 1)])

    return ExtractionResult(
        extraction=None,
        cost_usd=last_cost,
        error=last_error or "unknown_failure",
        meta={**last_meta, "retries": policy.retry_cap,
              "duration_s": round(loop.time() - start, 2)},
    )
