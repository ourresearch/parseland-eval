"""Live-API client for the deployed Taxicab + Parseland services.

Taxicab resolves a DOI to one or more harvest records (UUIDs + S3 paths for
the cached landing-page HTML). Parseland takes a harvest UUID and returns
the extracted metadata (authors, affiliations, abstract, PDF URLs).

Base URLs default to the production ELBs but are overridable via env vars
(``TAXICAB_URL``, ``PARSELAND_URL``) for staging or local testing.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from parseland_eval.fetch import TIMEOUT_SECONDS, USER_AGENT

log = logging.getLogger(__name__)

_DEFAULT_TAXICAB = (
    "http://harvester-load-balancer-366186003.us-east-1.elb.amazonaws.com"
)
_DEFAULT_PARSELAND = (
    "http://parseland-load-balancer-667160048.us-east-1.elb.amazonaws.com"
)

TAXICAB_BASE = os.environ.get("TAXICAB_URL", _DEFAULT_TAXICAB).rstrip("/")
PARSELAND_BASE = os.environ.get("PARSELAND_URL", _DEFAULT_PARSELAND).rstrip("/")


@dataclass(frozen=True)
class ApiCall:
    """Timing + outcome for a single upstream request."""

    status_code: int | None
    duration_ms: float
    error: str | None


def _build_session() -> requests.Session:
    """Session with retries for transient 5xx / connection errors."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


_SESSION = _build_session()


def _get_json(url: str, *, doi: str | None = None, stage: str) -> tuple[Any, ApiCall]:
    start = time.perf_counter()
    try:
        resp = _SESSION.get(url, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        duration_ms = (time.perf_counter() - start) * 1000.0
        log.warning("api %s failed: doi=%s url=%s error=%s", stage, doi, url, exc)
        return None, ApiCall(status_code=None, duration_ms=duration_ms, error=str(exc))

    duration_ms = (time.perf_counter() - start) * 1000.0
    if resp.status_code != 200:
        log.warning(
            "api %s non-200: doi=%s url=%s status=%s body=%s",
            stage,
            doi,
            url,
            resp.status_code,
            resp.text[:200],
        )
        return None, ApiCall(
            status_code=resp.status_code, duration_ms=duration_ms, error=f"status_{resp.status_code}"
        )

    try:
        return resp.json(), ApiCall(
            status_code=resp.status_code, duration_ms=duration_ms, error=None
        )
    except ValueError as exc:
        log.warning("api %s json-decode failed: doi=%s url=%s error=%s", stage, doi, url, exc)
        return None, ApiCall(
            status_code=resp.status_code, duration_ms=duration_ms, error=f"json_decode: {exc}"
        )


def resolve_harvest_uuid(doi: str) -> tuple[str | None, ApiCall]:
    """Resolve a DOI to the first harvested HTML record's UUID.

    Returns ``(None, call)`` if the DOI has not been harvested yet
    (empty ``html[]``) or the request failed.
    """
    url = f"{TAXICAB_BASE}/taxicab/doi/{doi}"
    body, call = _get_json(url, doi=doi, stage="taxicab")
    if body is None:
        return None, call
    html_records = body.get("html") or []
    if not html_records:
        log.info("taxicab: no harvested html for doi=%s", doi)
        return None, call
    first = html_records[0]
    uuid = first.get("id") if isinstance(first, dict) else None
    if not uuid:
        log.warning("taxicab: missing id in first html record for doi=%s body=%s", doi, body)
        return None, call
    return str(uuid), call


def fetch_parsed(uuid: str) -> tuple[dict[str, Any] | None, ApiCall]:
    """Fetch extracted metadata for a harvest UUID from the Parseland service."""
    url = f"{PARSELAND_BASE}/parseland/{uuid}"
    body, call = _get_json(url, doi=None, stage="parseland")
    if body is None:
        return None, call
    if not isinstance(body, dict):
        log.warning("parseland: non-object response for uuid=%s type=%s", uuid, type(body).__name__)
        return None, ApiCall(
            status_code=call.status_code, duration_ms=call.duration_ms, error="non_object_response"
        )
    return body, call


def parsed_api_to_parseland_shape(api_json: dict[str, Any]) -> dict[str, Any]:
    """Adapter: map Parseland API response to the shape the scorers expect.

    Currently a near no-op because the API response already matches
    ``parseland-lib.parse_page``'s output keys (``authors[].name``,
    ``authors[].affiliations[].name``, ``urls[]``, ``abstract``). A dedicated
    function exists so future drift stays in one place and so tests can
    assert the invariant.
    """
    authors = api_json.get("authors") or []
    urls = api_json.get("urls") or []
    return {
        "authors": list(authors),
        "urls": list(urls),
        "abstract": api_json.get("abstract"),
        "license": api_json.get("license"),
        "version": api_json.get("version"),
    }
