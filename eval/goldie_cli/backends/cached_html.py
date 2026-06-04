"""Primary backend: Taxicab cached HTML + direct Claude (Sonnet) structured extract.

This is the LOCKED production gold-standard generator (memory 2026-04-30): direct
Anthropic API on Taxicab's pre-harvested S3-cached HTML — 44x faster / 4x cheaper than
browser automation, and the cache predates bot-checks so it covers pages that block a
live fetch. Browser-use Cloud / local CDP are reserved for live JS-rendered pages.

Bridges to the proven ``fetch_html`` + ``extract_via_claude`` + ``_approx_cost`` in
extract_via_taxicab.py (the same functions the locked pipeline runs), then converts the
capitalized extraction to goldie's lowercase ``ExtractionOut`` contract. The cached HTML
is returned as ``raw_html`` so the post-LLM transforms run over the exact page evidence
the model saw. Evidence is page-only — no metadata API is consulted.
"""
from __future__ import annotations

import os
from typing import Any

from ..config import KEY_ANTHROPIC, DEFAULT_MODEL
from ..transforms._source import src as _tx  # shared bridge to extract_via_taxicab
from .base import ExtractionResult


def _caps_to_lower(caps: dict[str, Any]) -> dict[str, Any]:
    """extract_via_claude returns {"Authors","Abstract","PDF URL"}; goldie's contract is
    lowercase ExtractionOut. Authors keep their {name,rasses,corresponding_author} shape.
    The cached path surfaces no bot-check flag (cache predates bot-checks) → has_bot_check=False."""
    return {
        "authors": caps.get("Authors") or [],
        "abstract": caps.get("Abstract") or "",
        "pdf_url": caps.get("PDF URL") or "",
        "has_bot_check": False,
    }


class CachedHtmlBackend:
    name = "cached"

    def __init__(self, *, api_key: str | None = None, model: str = DEFAULT_MODEL) -> None:
        self._api_key = api_key or os.environ.get(KEY_ANTHROPIC)
        if not self._api_key:
            raise RuntimeError(f"{KEY_ANTHROPIC} not set")
        self._model = model

    async def extract(self, doi, link, *, html=None, schema=None, prompt) -> ExtractionResult:
        # Use provided cached HTML if the caller already fetched it; else fetch via Taxicab.
        resolved_url = link
        if html is None:
            html, resolved_url, err = _tx.fetch_html(doi)
            if err or html is None:
                return ExtractionResult(extraction=None, error=err or "taxicab: no html",
                                        raw_html=None, meta={"resolved_url": resolved_url})
        caps, usage = _tx.extract_via_claude(
            html, doi, link, system_prompt=prompt, model=self._model, api_key=self._api_key,
        )
        if caps is None:
            return ExtractionResult(
                extraction=None,
                error=(usage or {}).get("error", "claude call failed"),
                raw_html=html,
                meta={"resolved_url": resolved_url, "usage": usage or {}},
            )
        return ExtractionResult(
            extraction=_caps_to_lower(caps),
            cost_usd=round(_tx._approx_cost(usage or {}, self._model), 4),
            error=None,
            raw_html=html,  # page evidence for the post-LLM transforms
            meta={"resolved_url": resolved_url, "usage": usage or {}},
        )

    async def aclose(self) -> None:
        return None
