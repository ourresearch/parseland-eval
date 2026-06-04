"""Live-rendered fallback backend: browser-use Agent over a CDP-attached Chrome.

Reserved for live JS-rendered pages (per the locked architecture, cached Taxicab+Claude
is primary; this is the tier-2 fallback for cache-thin / JS-only pages). Mirrors the
proven pattern in run_ai_goldie.py / live_fetch_empty.py: a ChatAnthropic LLM, an Agent
with ``output_model_schema=ExtractionOut`` and ``use_vision=False``, and a FRESH
``Browser(cdp_url=...)`` per call (Agent's stop-event closes the session, so a new handle
re-attaches to the live Chrome each time).

Returns lowercase ExtractionOut shape directly (matches the Backend contract). ``raw_html``
is None — the agent extracts from the rendered DOM; the post-LLM HTML transforms add
little here and the cached tier is where they have leverage.

Evidence is the live page only; no metadata API is consulted.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from ..config import DEFAULT_CDP_URL, DEFAULT_MODEL, KEY_ANTHROPIC
from .base import ExtractionResult


def _build_task(prompt: str, doi: str, link: str) -> str:
    return (
        f"{prompt}\n\n---\n\n"
        f"DOI: {doi}\nURL: {link}\n\n"
        f"Navigate to the URL and emit the structured extraction when you have enough data."
    )


class LocalCdpBackend:
    name = "local_cdp"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        cdp_url: str = DEFAULT_CDP_URL,
        max_steps: int = 18,
    ) -> None:
        self._api_key = api_key or os.environ.get(KEY_ANTHROPIC)
        if not self._api_key:
            raise RuntimeError(f"{KEY_ANTHROPIC} not set")
        self._model = model
        self._cdp_url = cdp_url
        self._max_steps = max_steps
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            from browser_use.llm import ChatAnthropic
            self._llm = ChatAnthropic(model=self._model, api_key=self._api_key, max_tokens=4096)
        return self._llm

    async def extract(self, doi, link, *, html=None, schema=None, prompt) -> ExtractionResult:
        from browser_use import Agent, Browser

        from ..schema import ExtractionOut

        start = time.monotonic()
        browser = Browser(cdp_url=self._cdp_url)  # fresh handle per call (re-attaches)
        try:
            agent = Agent(
                task=_build_task(prompt, doi, link),
                llm=self._get_llm(),
                browser=browser,
                output_model_schema=ExtractionOut,
                use_vision=False,
                max_failures=3,
            )
            history = await agent.run(max_steps=self._max_steps)
            final = history.final_result()
            if isinstance(final, ExtractionOut):
                extraction: dict[str, Any] | None = final.model_dump()
            elif isinstance(final, dict):
                extraction = final
            elif isinstance(final, str):
                try:
                    extraction = json.loads(final)
                except json.JSONDecodeError:
                    extraction = None
            else:
                extraction = None
            return ExtractionResult(
                extraction=extraction,
                error=None if extraction else "no_structured_output",
                raw_html=None,
                meta={"steps": len(history.history),
                      "duration_s": round(time.monotonic() - start, 2)},
            )
        except Exception as e:  # noqa: BLE001 - surface as a row error, never crash the batch
            return ExtractionResult(
                extraction=None,
                error=f"{type(e).__name__}: {e}",
                raw_html=None,
                meta={"duration_s": round(time.monotonic() - start, 2)},
            )

    async def aclose(self) -> None:
        return None
