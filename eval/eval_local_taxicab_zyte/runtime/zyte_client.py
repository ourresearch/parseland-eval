"""Zyte API client — residential proxy + headless browser + auto-CAPTCHA.

NEW integration. The wider repo had zero Zyte code before this workspace
(every prior `zyte` reference was a doc-only deferral). We use the modern
Zyte API (`api.zyte.com/v1/extract`) — not Smart Proxy Manager — because
it bundles the browser rendering and CAPTCHA solving we need for
Cloudflare-walled publishers in one call.

Surface:

  await fetch_via_zyte(url, country='us') → (html, error)

Per memory `feedback_no_silent_failures`: any 4xx/5xx, JSON parse miss, or
empty body is returned as a loud error string — never swallowed.

Auth: HTTP Basic, API key as username, empty password. Key comes from
$ZYTE_API_KEY (raises if absent at call time, matching the project's
no-silent-failure rule).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import Any

import httpx

log = logging.getLogger("eval_local_taxicab_zyte.zyte")

ZYTE_API_URL = "https://api.zyte.com/v1/extract"
DEFAULT_TIMEOUT_S = 90  # Zyte's own browser-render can take 30-60s
DEFAULT_COUNTRY = "us"

# Ordered country list for rotation on block — first hit at US, then EU,
# then APAC. Per Zyte docs, geo affects which residential exit IP pool is
# used; publisher walls sometimes only accept specific regions.
COUNTRY_ROTATION: tuple[str, ...] = ("us", "gb", "de", "jp")

# Errors we treat as "Zyte itself can't reach the page" — callers should
# fall back to local Chrome on these. NOT a list of retryable errors;
# these are terminal-for-Zyte signals.
ZYTE_BLOCKED_MARKERS: tuple[str, ...] = (
    "zyte:403",
    "zyte:451",
    "zyte:520",
    "zyte:524",
    "zyte:cloudflare-challenge-unsolved",
    "zyte:max-retries",
)


def _zyte_blocked(error: str | None) -> bool:
    if not error:
        return False
    return any(m in error for m in ZYTE_BLOCKED_MARKERS)


def _require_key() -> str:
    key = os.environ.get("ZYTE_API_KEY") or ""
    if not key.strip():
        raise RuntimeError(
            "ZYTE_API_KEY is not set. Add it to eval/eval_local_taxicab_zyte/.env"
        )
    return key


async def fetch_via_zyte(
    url: str,
    *,
    country: str = DEFAULT_COUNTRY,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> tuple[str | None, str | None]:
    """POST a single extract request and return (browser_html, error).

    `browser_html` is Zyte's fully-rendered DOM after JS execution and
    CAPTCHA solving (when applicable). Use it the same way you'd use
    `extract_via_taxicab.fetch_html`'s return value — feed it straight
    into `extract_via_claude` → `run_judge_round`.

    Errors prefix `zyte:` for grep-ability. Callers can check
    `_zyte_blocked(error)` to decide whether to fall back to local Chrome.
    """
    key = _require_key()
    payload: dict[str, Any] = {
        "url": url,
        "browserHtml": True,
        "geolocation": country.upper(),
    }
    auth = (key, "")
    try:
        async with httpx.AsyncClient(timeout=timeout_s, auth=auth) as client:
            resp = await client.post(ZYTE_API_URL, json=payload)
    except httpx.TimeoutException:
        return None, f"zyte:timeout after {timeout_s}s"
    except httpx.HTTPError as exc:
        return None, f"zyte:transport {type(exc).__name__}: {exc}"

    if resp.status_code == 401:
        return None, "zyte:401 unauthorized — check ZYTE_API_KEY"
    if resp.status_code == 429:
        return None, "zyte:429 rate-limited"
    if resp.status_code in (403, 451, 520, 524):
        # Zyte returns these when the target itself blocks even Zyte's
        # residential / smart-proxy stack — caller should fall back.
        return None, f"zyte:{resp.status_code}"
    if resp.status_code >= 400:
        body = (resp.text or "")[:300]
        return None, f"zyte:{resp.status_code} {body}"

    try:
        body = resp.json()
    except ValueError:
        return None, "zyte:non-json-response"

    # `browserHtml` is a string when rendering succeeded.
    html = body.get("browserHtml")
    if isinstance(html, str) and html.strip():
        log.debug("zyte ok url=%s country=%s bytes=%d", url, country, len(html))
        return html, None

    # `httpResponseBody` is base64 — present when Zyte fell back from
    # browser rendering. Decode and surface as HTML, but flag as warning.
    raw_b64 = body.get("httpResponseBody")
    if isinstance(raw_b64, str) and raw_b64.strip():
        try:
            decoded = base64.b64decode(raw_b64).decode("utf-8", errors="replace")
            log.warning(
                "zyte returned httpResponseBody (no browser render) url=%s", url,
            )
            return decoded, None
        except (ValueError, UnicodeDecodeError) as exc:
            return None, f"zyte:body-decode-failed {exc}"

    # Empty response — surface loudly.
    return None, "zyte:empty-response"


async def fetch_with_country_rotation(
    url: str,
    *,
    countries: tuple[str, ...] = COUNTRY_ROTATION,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> tuple[str | None, str | None, str]:
    """Try `fetch_via_zyte` across `countries` until one succeeds.

    Returns (html, error, country_used). On full failure, the error from
    the LAST attempt is returned and `country_used` is the last country
    tried.
    """
    last_err: str | None = "zyte:no-attempts"
    last_country: str = countries[0] if countries else DEFAULT_COUNTRY
    for country in countries:
        html, err = await fetch_via_zyte(url, country=country, timeout_s=timeout_s)
        last_country = country
        last_err = err
        if html:
            return html, None, country
        if not _zyte_blocked(err):
            # Non-block error (timeout / 5xx / transport) — don't burn the
            # remaining country attempts on something the geo won't fix.
            return None, err, country
        # Otherwise: try the next country (Zyte was blocked at this geo).
        log.info("zyte blocked at country=%s err=%s — rotating", country, err)
        await asyncio.sleep(1)
    return None, last_err, last_country


__all__ = [
    "ZYTE_API_URL",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_COUNTRY",
    "COUNTRY_ROTATION",
    "ZYTE_BLOCKED_MARKERS",
    "fetch_via_zyte",
    "fetch_with_country_rotation",
    "_zyte_blocked",
]
