"""Thin re-export of the in-flight Taxicab re-harvest client.

Imports `reharvest_one` from `eval/browser-use/runtime/taxicab_reharvest.py`
WITHOUT modifying it, so this workspace stays isolated from the cascade
already executing in `eval/browser-use/`.

We re-export with a workspace-tagged logger so failures land under
`eval_local_taxicab_zyte` for grep-ability.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

# Resolve eval/browser-use/runtime/ and import the production reharvest_one.
_THIS_DIR = Path(__file__).resolve().parent
_EVAL_DIR = _THIS_DIR.parent.parent  # eval_local_taxicab_zyte/runtime → eval/
_BROWSER_USE_RUNTIME = _EVAL_DIR / "browser-use" / "runtime"
if str(_BROWSER_USE_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_BROWSER_USE_RUNTIME))

# `taxicab_reharvest` is a module file inside eval/browser-use/runtime/.
# Direct module import works because we added the parent dir to sys.path.
from taxicab_reharvest import (  # type: ignore[import-not-found]  # noqa: E402
    DEFAULT_HARVESTER,
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_TIMEOUT_S,
    reharvest_one as _reharvest_one,
)

log = logging.getLogger("eval_local_taxicab_zyte.taxicab")


def reharvest(
    doi: str,
    *,
    harvester_url: str = DEFAULT_HARVESTER,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    poll_interval_s: int = DEFAULT_POLL_INTERVAL_S,
) -> dict[str, Any]:
    """POST the Taxicab harvester to trigger a fresh re-scrape for `doi`.

    Wraps `taxicab_reharvest.reharvest_one` with workspace-tagged logging.
    Returns the same result dict shape as the underlying function:
        {doi, status, duration_s, http_status_post, http_status_get,
         pre_fingerprint, post_fingerprint, error}

    `status` is one of: refreshed | unchanged | timeout | rate-limited |
    post-error | harvester-5xx | dry-run.
    """
    log.info("reharvest start doi=%s", doi)
    result = _reharvest_one(
        doi=doi,
        harvester_url=harvester_url,
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
        dry_run=False,
    )
    log.info(
        "reharvest done doi=%s status=%s duration_s=%.1f",
        doi, result.get("status"), result.get("duration_s") or 0.0,
    )
    return result


__all__ = [
    "DEFAULT_HARVESTER",
    "DEFAULT_POLL_INTERVAL_S",
    "DEFAULT_TIMEOUT_S",
    "reharvest",
]
