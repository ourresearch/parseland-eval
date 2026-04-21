"""Invoke the deployed Parseland service for every gold row.

Each run resolves DOI → harvest UUID via Taxicab, then fetches the extracted
metadata from the Parseland service. There is no in-process fallback — if
the live service is down, the eval fails loudly rather than quietly scoring
a different parser.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from parseland_eval.api import (
    fetch_parsed,
    parsed_api_to_parseland_shape,
    resolve_harvest_uuid,
)
from parseland_eval.gold import GoldRow

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParserRun:
    doi: str
    parsed: dict[str, Any] | None
    error: str | None
    duration_ms: float
    publisher_domain: str
    harvest_uuid: str | None = None
    taxicab_duration_ms: float = 0.0
    parseland_duration_ms: float = 0.0


def _publisher_domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host.removeprefix("www.")
    except Exception:  # noqa: BLE001
        return ""


def run_one(row: GoldRow) -> ParserRun:
    """Call the deployed Parseland service via Taxicab for a single gold row."""
    publisher = _publisher_domain(row.link)
    uuid, taxicab_call = resolve_harvest_uuid(row.doi)
    if uuid is None:
        err = taxicab_call.error or "taxicab-no-html"
        return ParserRun(
            doi=row.doi,
            parsed=None,
            error=f"taxicab: {err}",
            duration_ms=taxicab_call.duration_ms,
            publisher_domain=publisher,
            harvest_uuid=None,
            taxicab_duration_ms=taxicab_call.duration_ms,
            parseland_duration_ms=0.0,
        )

    body, parseland_call = fetch_parsed(uuid)
    if body is None:
        err = parseland_call.error or "parseland-no-body"
        return ParserRun(
            doi=row.doi,
            parsed=None,
            error=f"parseland: {err}",
            duration_ms=taxicab_call.duration_ms + parseland_call.duration_ms,
            publisher_domain=publisher,
            harvest_uuid=uuid,
            taxicab_duration_ms=taxicab_call.duration_ms,
            parseland_duration_ms=parseland_call.duration_ms,
        )

    parsed = parsed_api_to_parseland_shape(body)
    return ParserRun(
        doi=row.doi,
        parsed=parsed,
        error=None,
        duration_ms=taxicab_call.duration_ms + parseland_call.duration_ms,
        publisher_domain=publisher,
        harvest_uuid=uuid,
        taxicab_duration_ms=taxicab_call.duration_ms,
        parseland_duration_ms=parseland_call.duration_ms,
    )


def run_all(rows: list[GoldRow]) -> list[ParserRun]:
    out: list[ParserRun] = []
    for idx, row in enumerate(rows, start=1):
        if idx == 1 or idx % 10 == 0:
            log.info("runner: %d/%d doi=%s", idx, len(rows), row.doi)
        out.append(run_one(row))
    return out
