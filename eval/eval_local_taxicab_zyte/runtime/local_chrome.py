"""Per-DOI visible-Chrome handle factory for the local-Chrome recovery tier.

Mirrors the pattern proven in `eval/scripts/rerun_targeted.py:_tier2_browser_use_cloud`:
an Agent drives navigation + JS rendering, and the rendered DOM is then
captured via CDP `Runtime.evaluate` for the downstream judge chain. The
Agent's structured output is also surfaced as a `candidate_extraction`
so the judge can be merged with what the live page surfaced.

Per-DOI Browser handle (per `live_fetch_empty.py:210`): re-create per
DOI so the previous Agent's BrowserStopEvent doesn't leave the next
session in a closed state.

Prereq: visible Chrome must be running with `--remote-debugging-port=9222`
(or whatever `CDP_URL` points to). NEVER use headless — the live-fetch
tier exists specifically because headless gets bot-walled (per memory
`project_livefetch_tier`).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

log = logging.getLogger("eval_local_taxicab_zyte.local_chrome")

DEFAULT_AGENT_MODEL = os.environ.get("LOCAL_CHROME_AGENT_MODEL", "claude-sonnet-4-5")
DEFAULT_MAX_STEPS = 12
DEFAULT_AGENT_TIMEOUT_S = 180


def _build_agent_task(doi: str, url: str, system_prompt_body: str, max_steps: int) -> str:
    """Mirror the Tier 2 task shape from `rerun_targeted.py:530-536` so
    the live-fetch tier behaves identically to the existing Cloud path."""
    return (
        f"{system_prompt_body}\n\n---\n\n"
        f"DOI: {doi}\nURL: {url}\n\n"
        f"Open the URL, wait for the page to render fully, expand any "
        f"'Author Info'/'Affiliations'/'Acknowledgements' sections, then "
        f"return the structured extraction. Hard cap ~{max_steps} steps."
    )


def _extract_to_candidate_shape(extraction_v0: dict[str, Any]) -> dict[str, Any]:
    """Convert the Agent's `ExtractionOut` (lowercase author/affiliation fields)
    into the gold-row shape (Authors / Abstract / PDF URL) the judge expects.

    Schema crib from `live_fetch_empty.py:42-58` ExtractionOut +
    `rerun_targeted.py:_cloud_extraction_to_gold_shape` (same idea).
    """
    if not isinstance(extraction_v0, dict):
        return {}
    raw_authors = extraction_v0.get("authors") or []
    gold_authors: list[dict[str, Any]] = []
    for a in raw_authors:
        if not isinstance(a, dict):
            continue
        name = (a.get("name") or "").strip()
        if not name:
            continue
        rasses = a.get("rasses")
        if isinstance(rasses, list):
            rasses = " | ".join(str(x or "").strip() for x in rasses if str(x or "").strip())
        elif rasses is None:
            rasses = ""
        else:
            rasses = str(rasses).strip()
        gold_authors.append({
            "name": name,
            "rasses": rasses,
            "corresponding_author": bool(a.get("corresponding_author")),
        })
    return {
        "Authors": gold_authors,
        "Abstract": (extraction_v0.get("abstract") or "").strip(),
        "PDF URL": (extraction_v0.get("pdf_url") or "").strip(),
        "has_bot_check": bool(extraction_v0.get("has_bot_check")),
        "resolves_to_pdf": bool(extraction_v0.get("resolves_to_pdf")),
        "broken_doi": bool(extraction_v0.get("broken_doi")),
        "no_english": bool(extraction_v0.get("no_english")),
    }


async def fetch_via_local_chrome(
    *,
    doi: str,
    url: str,
    system_prompt_body: str,
    cdp_url: str,
    agent_model: str = DEFAULT_AGENT_MODEL,
    max_steps: int = DEFAULT_MAX_STEPS,
    timeout_s: int = DEFAULT_AGENT_TIMEOUT_S,
) -> tuple[str | None, dict[str, Any] | None, str | None, float]:
    """Drive a fresh per-DOI Browser via an Agent → capture rendered DOM.

    Returns (html, candidate_extraction, error, duration_s).
      - html: the fully-rendered `document.documentElement.outerHTML`
        captured via CDP after the Agent settles.
      - candidate_extraction: gold-shape dict derived from the Agent's
        structured ExtractionOut (or None if Agent returned nothing).
      - error: short tag if anything failed; None on success.
      - duration_s: total wall time.
    """
    start = time.perf_counter()
    try:
        from browser_use import Agent, Browser  # type: ignore
        from browser_use.llm import ChatAnthropic  # type: ignore
    except ImportError as exc:
        return None, None, f"local_chrome:import_error: {exc}", 0.0

    # Lazy import of the Agent's output schema so we don't pull pydantic
    # at module-import time if the runner never needs Tier B.
    try:
        import sys
        from pathlib import Path
        _SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
        if str(_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS))
        from live_fetch_empty import ExtractionOut  # type: ignore
    except Exception as exc:
        return None, None, f"local_chrome:schema_import_failed: {exc}", 0.0

    browser = None
    extraction_v0: dict[str, Any] | None = None
    html: str | None = None
    nav_err: str | None = None

    try:
        browser = Browser(cdp_url=cdp_url)
        llm = ChatAnthropic(model=agent_model)
        task = _build_agent_task(doi, url, system_prompt_body, max_steps)
        agent = Agent(
            task=task,
            llm=llm,
            browser=browser,
            output_model_schema=ExtractionOut,
            use_vision=False,
            max_failures=2,
        )
        history = await asyncio.wait_for(
            agent.run(max_steps=max_steps),
            timeout=timeout_s,
        )
        final = history.final_result()
        if isinstance(final, ExtractionOut):
            extraction_v0 = final.model_dump()
        elif isinstance(final, dict):
            extraction_v0 = final
        elif isinstance(final, str):
            try:
                extraction_v0 = json.loads(final)
            except json.JSONDecodeError:
                extraction_v0 = None

        # Capture the rendered DOM for the judge chain.
        try:
            cdp = await browser.get_or_create_cdp_session()
            r = await cdp.cdp_client.send.Runtime.evaluate(
                params={
                    "expression": "document.documentElement.outerHTML",
                    "returnByValue": True,
                },
                session_id=cdp.session_id,
            )
            html = ((r or {}).get("result") or {}).get("value")
            if not isinstance(html, str) or not html.strip():
                nav_err = "local_chrome:cdp-runtime-evaluate-empty"
                html = None
        except Exception as exc:
            nav_err = f"local_chrome:cdp_capture_failed: {type(exc).__name__}: {exc}"

    except asyncio.TimeoutError:
        nav_err = f"local_chrome:agent_timeout after {timeout_s}s"
    except Exception as exc:
        nav_err = f"local_chrome:agent_exception: {type(exc).__name__}: {exc}"
    finally:
        # Best-effort cleanup — kill first (newer API), fall back to close.
        if browser is not None:
            try:
                await browser.kill()
            except Exception:
                try:
                    close = getattr(browser, "close", None)
                    if close:
                        res = close()
                        if asyncio.iscoroutine(res):
                            await res
                except Exception:
                    pass

    candidate = _extract_to_candidate_shape(extraction_v0) if extraction_v0 else None
    # If we got NO html AND no candidate, surface the nav error loudly.
    if html is None and not candidate:
        return None, None, nav_err or "local_chrome:no_output", time.perf_counter() - start
    # If we got candidate but no html, return candidate so the caller can
    # still close the row at "ok_no_judge".
    return html, candidate, nav_err, time.perf_counter() - start


# --- legacy surface (kept so older callers don't break) ---------------------

def open_browser_for(doi: str, *, cdp_url: str) -> Any:
    """Legacy entry point — fresh Browser handle. Prefer
    `fetch_via_local_chrome` which wraps the full Agent + capture lifecycle.
    """
    from browser_use import Browser  # type: ignore
    log.debug("open_browser_for doi=%s cdp=%s", doi, cdp_url)
    return Browser(cdp_url=cdp_url)


__all__ = [
    "DEFAULT_AGENT_MODEL",
    "DEFAULT_MAX_STEPS",
    "DEFAULT_AGENT_TIMEOUT_S",
    "fetch_via_local_chrome",
    "open_browser_for",
]
