"""Network-free backend for tests and the offline smoke run.

Returns scripted ``ExtractionResult``s so the retry/bot-check/pipeline logic can be
exercised deterministically with no API calls.
"""
from __future__ import annotations

from typing import Any, Callable

from .base import ExtractionResult


class StubBackend:
    name = "stub"

    def __init__(
        self,
        *,
        responder: Callable[[str, str], ExtractionResult] | None = None,
        default_extraction: dict[str, Any] | None = None,
    ) -> None:
        """``responder(doi, link) -> ExtractionResult`` for full control, else every
        DOI returns ``default_extraction`` (a one-author record by default)."""
        self._responder = responder
        self._default = default_extraction or {
            "authors": [{"name": "Stub Author", "rasses": "Stub University"}],
            "abstract": "stub abstract",
            "pdf_url": "",
        }
        self.calls = 0

    async def extract(self, doi, link, *, html=None, schema, prompt) -> ExtractionResult:
        self.calls += 1
        if self._responder is not None:
            return self._responder(doi, link)
        return ExtractionResult(extraction=dict(self._default), cost_usd=0.0,
                                raw_html=html, meta={"task_id": f"stub-{self.calls}"})

    async def aclose(self) -> None:
        return None
