"""Default backend: browser-use Cloud v3 sessions API (raw HTTP via httpx).

Lifts ``CloudClient`` + ``extraction_from_task`` + ``task_cost_usd`` from
extract_batch_cloud.py:275-409 so behaviour is identical. One ``extract`` call =
one create-session + poll-to-terminal + parse. Retries live in ``base.extract_with_retries``.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

import httpx

from ..config import KEY_BROWSER_USE
from .base import ExtractionResult

API_BASE = "https://api.browser-use.com/api/v3"
DEFAULT_POLL_INTERVAL = 5.0
DEFAULT_TASK_TIMEOUT_SEC = 30 * 60
TERMINAL_OK = {"idle", "stopped"}
TERMINAL_FAIL = {"error", "timed_out"}

_MODEL_DOT_RE = re.compile(r"^(claude-[a-z]+-\d+)-(\d+)$")


def to_cloud_model(model: str) -> str:
    """Translate the hyphenated API id (``claude-sonnet-4-6``) to the dotted form
    browser-use Cloud expects (``claude-sonnet-4.6``). Leaves already-dotted ids alone."""
    m = _MODEL_DOT_RE.match(model)
    return f"{m.group(1)}.{m.group(2)}" if m else model


def build_task(doi: str, link: str) -> str:
    """Short per-DOI directive; the extraction rules live in system_prompt_extension."""
    return (
        f"Extract scholarly metadata for DOI {doi} from this landing page "
        f"({link}). Follow the rules in the system prompt and emit the structured "
        f"extraction matching the provided output_schema. Stop as soon as you "
        f"have enough data — do not browse indefinitely."
    )


def extraction_from_task(task_data: dict[str, Any]) -> dict[str, Any] | None:
    """Walk common keys until the structured output object is found (lifted)."""
    for k in ("output", "structured_output", "result", "data"):
        v = task_data.get(k)
        if v is None:
            continue
        if isinstance(v, dict):
            inner = v.get("output") if "output" in v else v
            if isinstance(inner, dict) and ("authors" in inner or "abstract" in inner):
                return inner
            if "authors" in v or "abstract" in v:
                return v
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and ("authors" in parsed or "abstract" in parsed):
                return parsed
    return None


def task_cost_usd(task_data: dict[str, Any]) -> float | None:
    for k in ("totalCostUsd", "total_cost_usd", "cost_usd", "costUsd", "cost"):
        v = task_data.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


class BrowserUseCloudBackend:
    name = "cloud"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-6",
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        task_timeout_sec: float = DEFAULT_TASK_TIMEOUT_SEC,
        use_judge: bool = True,
        proxy_country_code: str | None = "default",
        http_timeout_sec: float = 180.0,
    ) -> None:
        key = api_key or os.environ.get(KEY_BROWSER_USE)
        if not key:
            raise RuntimeError(f"{KEY_BROWSER_USE} not set")
        self._model = to_cloud_model(model)
        self._poll_interval = poll_interval
        self._task_timeout_sec = task_timeout_sec
        self._use_judge = use_judge
        self._proxy_country_code = proxy_country_code
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            headers={"X-Browser-Use-API-Key": key, "Content-Type": "application/json"},
            timeout=http_timeout_sec,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _create_session(self, task_text, output_schema, start_url, system_prompt_extension) -> str:
        body: dict[str, Any] = {
            "task": task_text,
            "llm": self._model,
            "output_schema": output_schema,
            "start_url": start_url,
            "system_prompt_extension": system_prompt_extension,
            "vision": False,
            "judge": self._use_judge,
        }
        if self._proxy_country_code != "default":
            body["proxy_country_code"] = self._proxy_country_code
        resp = await self._client.post("/sessions", json=body)
        if resp.status_code >= 400:
            raise RuntimeError(f"create_session http {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        sid = data.get("id") or data.get("session_id") or data.get("sessionId")
        if not sid:
            raise RuntimeError(f"create_session returned no id: {data}")
        return sid

    async def _wait_for_session(self, session_id: str) -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        start = loop.time()
        while True:
            if loop.time() - start > self._task_timeout_sec:
                raise TimeoutError(f"session {session_id} exceeded {self._task_timeout_sec}s")
            resp = await self._client.get(f"/sessions/{session_id}")
            if resp.status_code >= 400:
                raise RuntimeError(f"get_session http {resp.status_code}: {resp.text[:500]}")
            data = resp.json()
            status = (data.get("status") or "").lower()
            if status in TERMINAL_OK:
                return data
            if status in TERMINAL_FAIL:
                raise RuntimeError(f"session {session_id} terminal-failed: status={status}")
            await asyncio.sleep(self._poll_interval)

    async def extract(self, doi, link, *, html=None, schema, prompt) -> ExtractionResult:
        # Cloud navigates the live page itself; `html` is ignored (no cached input).
        sid = await self._create_session(build_task(doi, link), schema, link, prompt)
        data = await self._wait_for_session(sid)
        return ExtractionResult(
            extraction=extraction_from_task(data),
            cost_usd=task_cost_usd(data),
            error=None,
            raw_html=None,
            meta={"task_id": sid},
        )
