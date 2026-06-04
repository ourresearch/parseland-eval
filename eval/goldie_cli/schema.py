"""Single source of truth for the gold extraction schema + CSV columns.

Lifted verbatim from ``eval/scripts/extract_batch_cloud.py`` (the production spine)
so every backend/tier shares one contract. The 12-column ``GOLD_COLUMNS`` order is
load-bearing — downstream consumers read ``ai-goldie-{N}.csv`` by this exact header.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# The canonical raw gold-standard CSV column order. Do NOT reorder without an
# explicit schema migration — downstream consumers depend on it.
GOLD_COLUMNS: list[str] = [
    "No", "DOI", "Link", "Authors", "Abstract", "PDF URL",
    "Status", "Notes", "Has Bot Check", "Resolves To PDF",
    "broken_doi", "no english",
]


class AuthorOut(BaseModel):
    """One author as the extractor emits it.

    ``rasses`` is the gold-standard affiliation string (the historical key name);
    ``affiliations`` is the structured list the LLM may return instead. ``io`` collapses
    the two in ``normalize_author``.
    """

    name: str
    rasses: str = ""
    corresponding_author: bool = False
    affiliations: list[str] = Field(default_factory=list)


class ExtractionOut(BaseModel):
    """Structured output contract enforced server-side by the cloud backend and
    used as the Pydantic schema for local/cached backends."""

    authors: list[AuthorOut]
    abstract: str | None = None
    pdf_url: str | None = None
    has_bot_check: bool = False
    resolves_to_pdf: bool = False
    broken_doi: bool = False
    no_english: bool = False
    notes: str | None = None


def extraction_json_schema() -> dict:
    """JSON Schema for backends that pass an ``output_schema`` (browser-use Cloud)."""
    return ExtractionOut.model_json_schema()
