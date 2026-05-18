"""Recovery cascade for the no-rases bucket — Zyte residential proxy primary, local Chrome fallback.

Per-row tier ladder:
    Tier A — Taxicab POST re-harvest → re-extract + judge
    Tier B — Zyte API (`browserHtml: true` + residential geo) → re-extract + judge
    Tier C — local visible Chrome over CDP (fallback when Zyte was blocked)
    Tier D — terminal

We try Zyte before local Chrome here because the no-rases bucket
(3,826 rows) is 2.4× the no-authors bucket (1,619 rows) and Zyte
parallelism is API-limited, not local-Chrome-limited — running Zyte
first leaves the local Chromes for the smaller authors job that runs
alongside.

Usage:
    python eval/eval_local_taxicab_zyte/scripts/rerun_rases_zyte.py \\
        --label smoke --limit 10
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _common import (  # noqa: E402
    WORKSPACE_DIR,
    TierAttempt,
    append_failure,
    append_tier_log,
    build_gold_row,
    load_env,
    load_extraction_prompt,
    make_run_dir,
    make_tier_log_entry,
    read_input_rows,
    resolve_prompt_path,
    setup_logging,
    write_cost_ledger,
    write_results_csv,
)

# Reusable extractor + judge from eval/scripts/ — READ-ONLY imports.
sys.path.insert(0, str(WORKSPACE_DIR.parent / "scripts"))
from extract_via_taxicab import fetch_html  # type: ignore  # noqa: E402
from extract_with_judge import (  # type: ignore  # noqa: E402
    interpret_judge_verdicts,
    run_doi_with_judge_on_html,
)

# Workspace runtime.
sys.path.insert(0, str(WORKSPACE_DIR / "runtime"))
from local_chrome import fetch_via_local_chrome  # type: ignore  # noqa: E402
from taxicab_reharvest_client import reharvest  # type: ignore  # noqa: E402
from zyte_client import _zyte_blocked, fetch_with_country_rotation  # type: ignore  # noqa: E402

log = logging.getLogger("rerun_rases_zyte")

DEFAULT_INPUT = WORKSPACE_DIR / "data" / "rerun-no-rases.csv"
DEFAULT_CONCURRENCY = 6   # Zyte allows higher concurrency than local Chrome
DEFAULT_EXTRACTOR_MODEL = os.environ.get("EXTRACTOR_MODEL", "claude-sonnet-4-5")
DEFAULT_CDP_URL = os.environ.get("CDP_URL", "http://localhost:9222")
DEFAULT_MAX_RETRIES = 2

_CLOSING_VERDICTS = {"approved", "auth_wall_confirmed"}

# Per-prefix Zyte timeout overrides — hard publishers where Zyte's default
# 90s budget is too tight. Smoke v3 showed 10.1121/1.3650344 (AIP) hitting
# Zyte fetch_failed at 150s; bump AIP + APS prefixes to 300s.
_ZYTE_TIMEOUT_BY_PREFIX: dict[str, int] = {
    "10.1121": 300,   # AIP / Acoustical Society of America
    "10.1103": 300,   # APS / Physical Review series
}


def _zyte_timeout_for(doi: str) -> int:
    prefix = (doi or "").split("/", 1)[0]
    return _ZYTE_TIMEOUT_BY_PREFIX.get(prefix, 90)


# --- judge wrapper ----------------------------------------------------------

async def _judge_html(
    *,
    html: str,
    no: int,
    doi: str,
    link: str,
    system_prompt: str,
    extractor_model: str,
    api_key: str,
    max_retries: int,
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: run_doi_with_judge_on_html(
            html=html, no=no, doi=doi, link=link,
            system_prompt=system_prompt,
            extractor_model=extractor_model,
            api_key=api_key,
            max_retries=max_retries,
        ),
    )


def _last_round_verdicts(judge_result: dict[str, Any]) -> dict[str, Any]:
    rounds = judge_result.get("verdicts") or []
    if not rounds:
        return {}
    return rounds[-1].get("verdicts") or {}


# --- tier implementations ---------------------------------------------------

async def _tier_a_reharvest(
    *,
    no: int, doi: str, link: str,
    system_prompt: str, extractor_model: str, api_key: str, max_retries: int,
) -> tuple[TierAttempt, dict[str, Any] | None, str | None]:
    start = time.perf_counter()
    loop = asyncio.get_running_loop()
    rh = await loop.run_in_executor(None, lambda: reharvest(doi))
    rh_status = rh.get("status") or "unknown"
    if rh_status != "refreshed":
        return (
            TierAttempt(
                tier="tier_a_reharvest",
                status="reharvest_no_change",
                error=f"reharvest status={rh_status}",
                duration_s=time.perf_counter() - start,
                extra={"reharvest_status": rh_status},
            ),
            None, None,
        )

    html, _resolved, fetch_err = await loop.run_in_executor(
        None, lambda: fetch_html(doi),
    )
    if fetch_err or not html:
        return (
            TierAttempt(
                tier="tier_a_reharvest",
                status="fetch_failed",
                error=fetch_err or "no html after reharvest",
                duration_s=time.perf_counter() - start,
            ),
            None, None,
        )

    judge = await _judge_html(
        html=html, no=no, doi=doi, link=link,
        system_prompt=system_prompt, extractor_model=extractor_model,
        api_key=api_key, max_retries=max_retries,
    )
    if judge.get("tier") == "failed":
        return (
            TierAttempt(
                tier="tier_a_reharvest",
                status="extractor_failed",
                error=judge.get("error") or "extractor failed",
                duration_s=time.perf_counter() - start,
                cost_usd=judge.get("cost_usd") or 0.0,
            ),
            judge, None,
        )

    verdicts = _last_round_verdicts(judge)
    decision = interpret_judge_verdicts(verdicts, html)
    return (
        TierAttempt(
            tier="tier_a_reharvest",
            status=decision,
            duration_s=time.perf_counter() - start,
            cost_usd=judge.get("cost_usd") or 0.0,
        ),
        judge, decision,
    )


async def _tier_b_zyte(
    *,
    no: int, doi: str, link: str,
    system_prompt: str, extractor_model: str, api_key: str, max_retries: int,
) -> tuple[TierAttempt, dict[str, Any] | None, str | None, bool]:
    """Returns (attempt, judge_result, verdict, zyte_blocked).
    `zyte_blocked` lets the caller decide whether Tier C local-Chrome
    fallback should fire (only when Zyte itself was the wall).
    """
    start = time.perf_counter()
    html, fetch_err, country_used = await fetch_with_country_rotation(
        link, timeout_s=_zyte_timeout_for(doi),
    )
    if fetch_err or not html:
        return (
            TierAttempt(
                tier="tier_b_zyte",
                status="fetch_failed",
                error=fetch_err,
                duration_s=time.perf_counter() - start,
                extra={"country_tried": country_used},
            ),
            None, None,
            _zyte_blocked(fetch_err),
        )

    judge = await _judge_html(
        html=html, no=no, doi=doi, link=link,
        system_prompt=system_prompt, extractor_model=extractor_model,
        api_key=api_key, max_retries=max_retries,
    )
    if judge.get("tier") == "failed":
        return (
            TierAttempt(
                tier="tier_b_zyte",
                status="extractor_failed",
                error=judge.get("error") or "extractor failed",
                duration_s=time.perf_counter() - start,
                cost_usd=judge.get("cost_usd") or 0.0,
                extra={"country_used": country_used},
            ),
            judge, None, False,
        )

    verdicts = _last_round_verdicts(judge)
    decision = interpret_judge_verdicts(verdicts, html)
    return (
        TierAttempt(
            tier="tier_b_zyte",
            status=decision,
            duration_s=time.perf_counter() - start,
            cost_usd=judge.get("cost_usd") or 0.0,
            extra={"country_used": country_used},
        ),
        judge, decision, False,
    )


async def _tier_c_local_chrome(
    *,
    no: int, doi: str, link: str, cdp_url: str,
    system_prompt: str, extractor_model: str, api_key: str, max_retries: int,
) -> tuple[TierAttempt, dict[str, Any] | None, str | None]:
    start = time.perf_counter()
    html, candidate, nav_err, _wall = await fetch_via_local_chrome(
        doi=doi, url=link,
        system_prompt_body=system_prompt,
        cdp_url=cdp_url,
    )

    if not html and not candidate:
        return (
            TierAttempt(
                tier="tier_c_local_chrome",
                status="capture_failed",
                error=nav_err or "no html, no candidate",
                duration_s=time.perf_counter() - start,
            ),
            None, None,
        )

    if not html:
        return (
            TierAttempt(
                tier="tier_c_local_chrome",
                status="approved",
                error=nav_err,
                duration_s=time.perf_counter() - start,
                extra={"close_reason": "ok_no_judge_candidate_only"},
            ),
            {"extraction": candidate, "cost_usd": 0.0, "verdicts": []},
            "approved",
        )

    judge = await _judge_html(
        html=html, no=no, doi=doi, link=link,
        system_prompt=system_prompt, extractor_model=extractor_model,
        api_key=api_key, max_retries=max_retries,
    )
    if judge.get("tier") == "failed":
        return (
            TierAttempt(
                tier="tier_c_local_chrome",
                status="extractor_failed",
                error=judge.get("error") or "extractor failed",
                duration_s=time.perf_counter() - start,
                cost_usd=judge.get("cost_usd") or 0.0,
            ),
            judge, None,
        )

    verdicts = _last_round_verdicts(judge)
    decision = interpret_judge_verdicts(verdicts, html)
    return (
        TierAttempt(
            tier="tier_c_local_chrome",
            status=decision,
            duration_s=time.perf_counter() - start,
            cost_usd=judge.get("cost_usd") or 0.0,
        ),
        judge, decision,
    )


# --- row orchestration ------------------------------------------------------

async def run_one_row(
    row: dict[str, str],
    *,
    cdp_url: str,
    system_prompt: str,
    extractor_model: str,
    api_key: str,
    max_retries: int,
    enable_local_fallback: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    doi = (row["DOI"] or "").strip()
    link = (row["Link"] or "").strip() or f"https://doi.org/{doi}"
    try:
        no = int(row.get("No") or 0)
    except ValueError:
        no = 0
    doi_prefix = (row.get("doi_prefix") or doi.split("/", 1)[0]).strip()

    attempts: list[TierAttempt] = []
    best_extraction: dict[str, Any] | None = None
    closed_at = "terminal"
    last_verdict: str | None = None

    # Tier A
    att_a, judge_a, verdict_a = await _tier_a_reharvest(
        no=no, doi=doi, link=link,
        system_prompt=system_prompt, extractor_model=extractor_model,
        api_key=api_key, max_retries=max_retries,
    )
    attempts.append(att_a)
    if judge_a and judge_a.get("extraction"):
        best_extraction = judge_a["extraction"]
    last_verdict = verdict_a
    if verdict_a in _CLOSING_VERDICTS:
        closed_at = att_a.tier
        return _finalize(no, doi, link, doi_prefix, attempts, closed_at, best_extraction, verdict_a)

    # Tier B — Zyte
    att_b, judge_b, verdict_b, zyte_blocked = await _tier_b_zyte(
        no=no, doi=doi, link=link,
        system_prompt=system_prompt, extractor_model=extractor_model,
        api_key=api_key, max_retries=max_retries,
    )
    attempts.append(att_b)
    if judge_b and judge_b.get("extraction"):
        best_extraction = judge_b["extraction"]
    if verdict_b is not None:
        last_verdict = verdict_b
    if verdict_b in _CLOSING_VERDICTS:
        closed_at = att_b.tier
        return _finalize(no, doi, link, doi_prefix, attempts, closed_at, best_extraction, verdict_b)

    # Tier C — local Chrome fallback (only when Zyte itself was walled).
    if enable_local_fallback and zyte_blocked:
        att_c, judge_c, verdict_c = await _tier_c_local_chrome(
            no=no, doi=doi, link=link, cdp_url=cdp_url,
            system_prompt=system_prompt, extractor_model=extractor_model,
            api_key=api_key, max_retries=max_retries,
        )
        attempts.append(att_c)
        if judge_c and judge_c.get("extraction"):
            best_extraction = judge_c["extraction"]
        if verdict_c is not None:
            last_verdict = verdict_c
        if verdict_c in _CLOSING_VERDICTS:
            closed_at = att_c.tier

    log.info("[done]  %s closed=%s attempts=%d", doi, closed_at, len(attempts))
    return _finalize(no, doi, link, doi_prefix, attempts, closed_at, best_extraction, last_verdict)


def _finalize(
    no: int, doi: str, link: str, doi_prefix: str,
    attempts: list[TierAttempt], closed_at: str,
    extraction: dict[str, Any] | None, last_verdict: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    notes = f"closed_at={closed_at};verdict={last_verdict or 'n/a'}"
    gold = build_gold_row(no=no, doi=doi, link=link, extraction=extraction, notes=notes)
    entry = make_tier_log_entry(
        doi=doi, no=no, link=link, doi_prefix=doi_prefix,
        attempts=attempts, closed_at=closed_at,
        final_extraction=extraction,
    )
    return gold, entry


# --- driver -----------------------------------------------------------------

async def main_async(args: argparse.Namespace) -> int:
    load_env()
    api_key = os.environ.get("ANTHROPIC_API_KEY") or ""
    if not api_key:
        log.error("ANTHROPIC_API_KEY is not set — refusing to run.")
        return 2
    if not (os.environ.get("ZYTE_API_KEY") or "").strip():
        log.error("ZYTE_API_KEY is not set — refusing to run (this script's Tier B needs Zyte).")
        return 2

    prompt_path = resolve_prompt_path(args.prompt)
    if not prompt_path.exists():
        log.error("prompt not found: %s", prompt_path)
        return 2
    version, system_prompt = load_extraction_prompt(prompt_path)
    log.info("prompt=%s version=%s chars=%d", prompt_path, version, len(system_prompt))

    input_path = Path(args.input or DEFAULT_INPUT)
    if not input_path.exists():
        log.error("input not found: %s", input_path)
        return 2
    rows = read_input_rows(input_path, limit=args.limit)
    log.info("rows to process: %d (input=%s)", len(rows), input_path)

    out_dir = make_run_dir(
        WORKSPACE_DIR / "runs",
        prefix="rases-zyte",
        label=args.label,
    )
    log.info("out_dir=%s", out_dir)

    sem = asyncio.Semaphore(args.concurrency)
    gold_rows: list[dict[str, Any]] = []
    total_cost = 0.0

    async def one(row: dict[str, str]) -> None:
        nonlocal total_cost
        async with sem:
            gold, entry = await run_one_row(
                row,
                cdp_url=args.cdp_url,
                system_prompt=system_prompt,
                extractor_model=args.extractor_model,
                api_key=api_key,
                max_retries=args.max_retries,
                enable_local_fallback=not args.no_local_fallback,
            )
            gold_rows.append(gold)
            append_tier_log(out_dir, entry)
            total_cost += entry.get("total_cost_usd") or 0.0
            if entry["closed_at"] == "terminal":
                append_failure(out_dir, entry)

    await asyncio.gather(*(one(r) for r in rows))

    gold_rows.sort(key=lambda r: (r.get("No") or 0, r.get("DOI") or ""))
    csv_path = write_results_csv(out_dir, gold_rows)

    closed_counts: dict[str, int] = {}
    for r in gold_rows:
        notes = r.get("Notes") or ""
        bucket = (
            notes.split(";", 1)[0].split("=", 1)[1]
            if notes.startswith("closed_at=") else "unknown"
        )
        closed_counts[bucket] = closed_counts.get(bucket, 0) + 1

    write_cost_ledger(
        out_dir,
        {
            "label": args.label,
            "input_csv": str(input_path),
            "rows_processed": len(rows),
            "total_cost_usd": round(total_cost, 4),
            "closed_by_tier": closed_counts,
            "concurrency": args.concurrency,
            "extractor_model": args.extractor_model,
            "max_retries": args.max_retries,
            "cdp_url": args.cdp_url,
            "enable_local_fallback": not args.no_local_fallback,
            "prompt": str(prompt_path),
            "prompt_version": version,
        },
    )
    log.info("CSV: %s", csv_path)
    log.info("cost: $%.2f", total_cost)
    log.info("closed by tier: %s", closed_counts)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", type=Path, default=None,
                    help=f"Input CSV (default: {DEFAULT_INPUT})")
    ap.add_argument("--label", required=True, help="Run label")
    ap.add_argument("--limit", type=int, default=0,
                    help="Process only the first N rows (0 = all)")
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                    help=f"Parallel rows (default: {DEFAULT_CONCURRENCY})")
    ap.add_argument("--extractor-model", default=DEFAULT_EXTRACTOR_MODEL,
                    help=f"Sonnet wire id (default: {DEFAULT_EXTRACTOR_MODEL})")
    ap.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES,
                    help=f"Judge retry rounds (default: {DEFAULT_MAX_RETRIES})")
    ap.add_argument("--cdp-url", default=DEFAULT_CDP_URL,
                    help=f"CDP URL of live Chrome (default: {DEFAULT_CDP_URL})")
    ap.add_argument("--no-local-fallback", action="store_true",
                    help="Skip Tier C local-Chrome fallback when Zyte is blocked")
    ap.add_argument("--prompt", default=None,
                    help="Extraction prompt path (default: eval/prompts/ai-goldie-v1.9.2.md)")
    ap.add_argument("--log-level", default="INFO")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
