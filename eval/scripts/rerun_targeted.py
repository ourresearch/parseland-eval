"""Targeted Tier 1.5 -> Tier 2 cascade rerun for a subset of empty-row DOIs.

Reads a triage CSV (`rerun-no-authors.csv` or `rerun-no-rases.csv`), filters
to a DOI prefix (e.g. `10.1016` for Elsevier), and for each DOI:

  0. If the DOI's Link is a `chooser.crossref.org` page, resolve to the
     first plausible publisher link (`chooser_resolver.resolve_chooser`).
     Tier 2 will navigate to that resolved URL; Tier 1.5 still issues a
     normal DOI-based reharvest because the harvester does its own
     resolution.
  1. Tier 1.5 — POST harvester to trigger a fresh cache
     (`taxicab_reharvest.reharvest_one`).
       refreshed              → continue to Tier 1.5 re-extract+judge
       unchanged | timeout    → fall through to Tier 2
       rate-limited           → one 30s backoff retry, then fall through
       post-error | 5xx       → fall through (and HALT if too many in a row)
  2. Tier 1.5 re-extract+judge — re-extract from the refreshed cached HTML
     and run Opus 4.7 per-field verifier
     (`extract_with_judge.run_doi_with_judge`). If authors come back
     non-empty → record + checkpoint, done.
  3. Tier 2 (escalation) — drive real Chrome over CDP via browser-use Agent
     (`live_fetch_empty.fetch_one`) with the resolved publisher URL. Take
     extraction raw (cannot be judged against stale cache).

Outputs:
  --output <path>.csv       12-column gold schema, one row per processed DOI
  --output <path>.tier-log.jsonl   per-DOI cascade log (tier choices, cost, errors)
  runs/10k/rerun/.checkpoint/<output-stem>.partial.jsonl  resume marker

Loud failures (per project no-silent-failure rule):
  - missing input / output dir not writable               -> exit 2
  - rolling >=30% Tier 1.5 5xx over last 20 DOIs          -> exit 3
  - Tier 2 pre-flight: CDP at :9222 unreachable           -> exit 4
  - ANTHROPIC_API_KEY missing                             -> exit 5
  - --max-cost-usd exceeded                               -> exit 6
  - chooser unresolvable for a chooser-only row + dry-run -> WARN, still tried

Usage:
  python eval/scripts/rerun_targeted.py \\
      --input runs/10k/triage/rerun-no-authors.csv \\
      --prefix 10.1016 \\
      --output runs/10k/rerun/elsevier-no-authors-dry.csv \\
      --limit 10
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import re
import sys
import time
from collections import Counter, deque
from pathlib import Path
from typing import Any

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
RUNTIME_DIR = SCRIPT_DIR.parent / "browser-use" / "runtime"
sys.path.insert(0, str(RUNTIME_DIR))

from chooser_resolver import is_chooser_url, resolve_chooser  # noqa: E402
from extract_batch_cloud import (  # noqa: E402
    CloudClient,
    ExtractionOut,
    GOLD_COLUMNS,
    build_task,
    extraction_from_task,
    load_prompt,
    normalize_author,
    task_cost_usd,
)
from extract_with_judge import run_doi_with_judge, to_gold_row_dict  # noqa: E402
from live_fetch_empty import _strip_yaml_front_matter, _to_csv_row, fetch_one  # noqa: E402
from taxicab_reharvest import (  # noqa: E402
    DEFAULT_HARVESTER,
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_TIMEOUT_S,
    reharvest_one,
)
from triage_10k import REQUIRED_COLS, doi_prefix  # noqa: E402

log = logging.getLogger("rerun-targeted")

DEFAULT_CONCURRENCY = 4
DEFAULT_TIER2_SUBCONCURRENCY_LOCAL = 2
DEFAULT_TIER2_SUBCONCURRENCY_CLOUD = 25
DEFAULT_TIER2_MAX_STEPS = 18
DEFAULT_TIER2_MODEL_LOCAL = "claude-sonnet-4-5"
DEFAULT_TIER2_MODEL_CLOUD = "claude-opus-4.7"
DEFAULT_EXTRACTOR_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_COST_USD = 5000.0  # very high; no hard money cap per Phase 2 plan
DEFAULT_CDP_URL = os.environ.get("CDP_URL", "http://localhost:9222")
DEFAULT_PROMPT = SCRIPT_DIR.parent / "prompts" / "ai-goldie-v1.9.2.md"
DEFAULT_TIER2_BACKEND = "cloud_sessions"

# Tier 1.5 catastrophe detector — rolling window of last N outcomes
TIER15_ROLLING_WINDOW = 20
TIER15_FATAL_FRACTION = 0.30
RATE_LIMIT_BACKOFF_S = 30

# Tier 2 Cloud catastrophe detector — same shape as Tier 1.5
TIER2_CLOUD_ROLLING_WINDOW = 20
TIER2_CLOUD_FATAL_FRACTION = 0.30

# Approximate per-DOI Tier 2 cost for local_cdp backend (browser-use library
# doesn't expose token usage from Agent.run; flat ballpark for cost-guard
# math only). cloud_sessions backend uses the real Cloud totalCostUsd.
TIER2_LOCAL_APPROX_COST_USD = 0.05


# ---------- checkpoint -------------------------------------------------------

def _checkpoint_path(output_path: Path) -> Path:
    cp_dir = output_path.parent / ".checkpoint"
    return cp_dir / f"{output_path.stem}.partial.jsonl"


def _load_checkpoint(cp_path: Path) -> dict[str, dict[str, Any]]:
    """Return {doi: most-recent checkpoint entry} from an append-only JSONL.

    Last-write-wins on duplicate DOIs (allows safe re-runs of the same DOI
    after a transient failure).
    """
    by_doi: dict[str, dict[str, Any]] = {}
    if not cp_path.exists():
        return by_doi
    with cp_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                log.warning("checkpoint: skipping malformed line: %s", line[:120])
                continue
            doi = entry.get("doi")
            if doi:
                by_doi[doi] = entry
    return by_doi


def _append_checkpoint(cp_path: Path, entry: dict[str, Any]) -> None:
    cp_path.parent.mkdir(parents=True, exist_ok=True)
    with cp_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()


# ---------- pre-flight -------------------------------------------------------

def _preflight_cdp(cdp_url: str) -> bool:
    """HEAD/GET CDP /json/version. True if reachable, False otherwise."""
    try:
        resp = requests.get(
            cdp_url.rstrip("/") + "/json/version",
            timeout=5,
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False


def _preflight_cloud_api(api_key: str | None) -> tuple[bool, str]:
    """Verify BROWSER_USE_API_KEY exists and the Cloud API responds.
    Returns (ok, reason). Hits /api/v3/sessions GET (list) — a cheap
    auth-validity probe that doesn't create a billable session."""
    if not api_key:
        return False, "BROWSER_USE_API_KEY not set"
    try:
        resp = requests.get(
            "https://api.browser-use.com/api/v3/sessions",
            headers={"X-Browser-Use-API-Key": api_key},
            timeout=10,
        )
        if resp.status_code == 401 or resp.status_code == 403:
            return False, f"Cloud auth failed: {resp.status_code} {resp.text[:200]}"
        # 200 / 404 / 405 all indicate the endpoint is responding; we don't
        # care about the body — only that the key is accepted.
        return True, "ok"
    except requests.RequestException as e:
        return False, f"Cloud API unreachable: {e}"


# ---------- per-DOI cascade --------------------------------------------------

def _has_authors(row: dict[str, Any]) -> bool:
    """Inspect an extraction dict (gold-schema shape) or a CSV row for
    non-empty Authors."""
    authors = row.get("Authors") if isinstance(row, dict) else None
    if isinstance(authors, str):
        try:
            arr = json.loads(authors)
        except json.JSONDecodeError:
            return False
    elif isinstance(authors, list):
        arr = authors
    else:
        return False
    if not isinstance(arr, list):
        return False
    return any(isinstance(a, dict) and (a.get("name") or "").strip() for a in arr)


def _normalize_tier2_to_gold_row(no: int, doi: str, link: str,
                                  extraction: dict[str, Any],
                                  error: str | None) -> dict[str, str]:
    """Use live_fetch_empty._to_csv_row to produce a row, then ensure it
    matches the GOLD_COLUMNS field order/casing."""
    base = _to_csv_row(doi, link, extraction or {}, error)
    base["No"] = str(no)
    # _to_csv_row uses Title-case "True"/"False"; v2 CSVs use upper-case.
    for key in ("Status", "Has Bot Check", "Resolves To PDF", "broken_doi", "no english"):
        val = base.get(key, "")
        if isinstance(val, str):
            v = val.strip().lower()
            base[key] = "TRUE" if v == "true" else "FALSE" if v == "false" else val
    return {k: base.get(k, "") for k in GOLD_COLUMNS}


async def _tier2_local_cdp(
    *,
    doi: str,
    tier2_link: str,
    cdp_url: str,
    tier2_model: str,
    tier2_max_steps: int,
    tier2_prompt_body: str,
) -> tuple[str, dict[str, Any] | None, str, float, int, str | None]:
    """Existing local-Chrome path. Returns
    (final_tier, extraction, tier2_status, tier2_cost, tier2_authors_n, error)."""
    try:
        from browser_use import Browser
        from browser_use.llm import ChatAnthropic
    except ImportError as e:
        return "none", None, "import_error", 0.0, 0, f"tier2_unavailable: {e}"

    try:
        browser = Browser(cdp_url=cdp_url)
        llm = ChatAnthropic(model=tier2_model)
        target = {"doi": doi, "link": tier2_link, "reason": "tier2_escalation"}
        r2 = await fetch_one(browser, llm, target, tier2_prompt_body, tier2_max_steps)
    except Exception as e:  # noqa: BLE001
        return "none", None, f"exception:{type(e).__name__}", 0.0, 0, f"{type(e).__name__}: {e}"

    cost = TIER2_LOCAL_APPROX_COST_USD
    err = r2.get("error")
    raw = r2.get("extraction") or {}
    if raw and _has_authors_raw(raw):
        n_authors = sum(
            1 for a in raw.get("authors", [])
            if isinstance(a, dict) and (a.get("name") or "").strip()
        )
        return "tier2_raw", raw, "ok", cost, n_authors, None
    return "none", None, (f"error:{err[:120]}" if err else "ok"), cost, 0, err or "tier2_no_authors"


async def _tier2_cloud_sessions(
    *,
    doi: str,
    tier2_link: str,
    cloud_client: "CloudClient | None",
    tier2_prompt_body: str,
) -> tuple[str, dict[str, Any] | None, str, float, int, bool, str | None]:
    """browser-use Cloud v3 sessions API path. Returns
    (final_tier, extraction, tier2_status, tier2_cost, tier2_authors_n,
     has_bot_check, error).

    On bot_check + empty authors, returns final_tier='none' but signals
    `has_bot_check=True` so the caller can tag iter-R:cloudflare_blocked.
    On non-2xx after Cloud's internal retries, returns tier2_status starting
    with 'cloud_error:' so the rolling-fault detector in _run can count it.
    """
    if cloud_client is None:
        return "none", None, "no_client", 0.0, 0, False, "cloud_client_not_initialized"

    start_url = tier2_link or f"https://doi.org/{doi}"
    task_text = build_task(doi, start_url)
    output_schema = ExtractionOut.model_json_schema()

    try:
        session_id = await cloud_client.create_session(
            task_text=task_text,
            output_schema=output_schema,
            start_url=start_url,
            system_prompt_extension=tier2_prompt_body,
        )
        data = await cloud_client.wait_for_session(session_id)
    except RuntimeError as e:
        # Includes non-2xx and terminal-fail statuses from CloudClient
        msg = str(e)
        status_tag = "cloud_error:" + (msg.split(":", 2)[1].strip()[:60] if "http" in msg else msg[:60])
        return "none", None, status_tag, 0.0, 0, False, msg[:200]
    except TimeoutError as e:
        return "none", None, "cloud_timeout", 0.0, 0, False, str(e)
    except Exception as e:  # noqa: BLE001
        return "none", None, f"cloud_exception:{type(e).__name__}", 0.0, 0, False, f"{type(e).__name__}: {e}"

    cost = float(task_cost_usd(data) or 0.0)
    raw = extraction_from_task(data) or {}
    has_bot_check = bool(raw.get("has_bot_check"))

    if raw and _has_authors_raw(raw):
        n_authors = sum(
            1 for a in raw.get("authors", [])
            if isinstance(a, dict) and (a.get("name") or "").strip()
        )
        return "tier2_cloud", raw, "ok", cost, n_authors, has_bot_check, None

    if has_bot_check:
        return "none", None, "cloud_bot_check", cost, 0, True, "iter-R:cloudflare_blocked"
    return "none", None, "cloud_no_authors", cost, 0, False, "tier2_cloud_no_authors"


async def process_doi(
    row: dict[str, str],
    *,
    no: int,
    cdp_url: str,
    extractor_model: str,
    tier2_model: str,
    tier2_max_steps: int,
    tier2_sem: asyncio.Semaphore,
    system_prompt: str,
    tier2_prompt_body: str,
    api_key: str,
    harvester_url: str,
    tier1_5_timeout_s: int,
    tier1_5_poll_interval_s: int,
    skip_tier: str | None,
    tier2_backend: str = DEFAULT_TIER2_BACKEND,
    cloud_client: "CloudClient | None" = None,
) -> dict[str, Any]:
    """Run a single DOI through the cascade. Returns checkpoint entry +
    csv_row payload."""
    doi = (row.get("DOI") or "").strip()
    link = (row.get("Link") or "").strip() or f"https://doi.org/{doi}"
    started = time.perf_counter()

    chooser_resolved: str | None = None
    if is_chooser_url(link):
        loop = asyncio.get_running_loop()
        chooser_resolved = await loop.run_in_executor(
            None, lambda: resolve_chooser(link)
        )
        if chooser_resolved:
            log.info("[%s] chooser resolved: %s -> %s", doi, link, chooser_resolved)
        else:
            log.warning("[%s] chooser URL not resolvable; cascading anyway", doi)

    tier2_link = chooser_resolved or link

    # ---- Tier 1.5 reharvest -------------------------------------------------
    tier15_status = "skipped"
    tier15_authors_n = 0
    tier15_verdicts: list[Any] = []
    tier15_cost = 0.0
    extraction: dict[str, Any] | None = None
    final_tier = "none"
    error_msg: str | None = None

    if skip_tier != "1.5":
        loop = asyncio.get_running_loop()
        rh = await loop.run_in_executor(
            None,
            lambda: reharvest_one(
                doi, harvester_url, tier1_5_timeout_s,
                tier1_5_poll_interval_s,
            ),
        )
        tier15_status = rh["status"]
        if tier15_status == "rate-limited":
            log.info("[%s] tier1.5 rate-limited; backing off %ds", doi, RATE_LIMIT_BACKOFF_S)
            await asyncio.sleep(RATE_LIMIT_BACKOFF_S)
            rh = await loop.run_in_executor(
                None,
                lambda: reharvest_one(
                    doi, harvester_url, tier1_5_timeout_s,
                    tier1_5_poll_interval_s,
                ),
            )
            tier15_status = rh["status"]

        if tier15_status == "refreshed":
            # Re-extract + judge against the refreshed cache
            result = await loop.run_in_executor(
                None,
                lambda: run_doi_with_judge(
                    no, doi, link,
                    system_prompt=system_prompt,
                    extractor_model=extractor_model,
                    api_key=api_key,
                ),
            )
            extraction = result.get("extraction")
            tier15_cost = float(result.get("cost_usd") or 0.0)
            tier15_verdicts = result.get("verdicts") or []
            if extraction and _has_authors(extraction):
                tier15_authors_n = len(extraction.get("Authors") or [])
                final_tier = "tier1.5_judged"
            else:
                error_msg = (result.get("error")
                             or "tier1.5_judged_returned_empty_authors")

    # ---- Tier 2 escalation --------------------------------------------------
    tier2_status = "skipped"
    tier2_authors_n = 0
    tier2_cost = 0.0
    tier2_has_bot_check = False
    if final_tier == "none" and skip_tier != "2":
        async with tier2_sem:
            if tier2_backend == "cloud_sessions":
                (final_tier, extraction, tier2_status, tier2_cost,
                 tier2_authors_n, tier2_has_bot_check, t2_err) = await _tier2_cloud_sessions(
                    doi=doi, tier2_link=tier2_link,
                    cloud_client=cloud_client,
                    tier2_prompt_body=tier2_prompt_body,
                )
                if t2_err and not error_msg:
                    error_msg = t2_err
            else:
                # local_cdp (existing path)
                (final_tier, extraction, tier2_status, tier2_cost,
                 tier2_authors_n, t2_err) = await _tier2_local_cdp(
                    doi=doi, tier2_link=tier2_link,
                    cdp_url=cdp_url, tier2_model=tier2_model,
                    tier2_max_steps=tier2_max_steps,
                    tier2_prompt_body=tier2_prompt_body,
                )
                if t2_err and not error_msg:
                    error_msg = t2_err

    # ---- compose output row --------------------------------------------------
    duration_s = round(time.perf_counter() - started, 2)
    if final_tier == "tier1.5_judged":
        # to_gold_row_dict consumes the run_doi_with_judge result shape;
        # build a minimal one.
        result_for_row = {
            "no": no, "doi": doi, "link": link,
            "extraction": extraction, "error": None,
        }
        csv_row = to_gold_row_dict(result_for_row)
    elif final_tier in ("tier2_raw", "tier2_cloud"):
        # Both tier2 paths return a lowercase-keyed dict matching the
        # ExtractionOut / browser-use Agent shape. _normalize_tier2_to_gold_row
        # produces the 12-column gold schema.
        csv_row = _normalize_tier2_to_gold_row(no, doi, tier2_link, extraction, None)
    else:
        # No tier produced authors. Emit a row that preserves the original
        # 12-column shape; tag Notes with cloudflare_blocked (per rule #14)
        # when Cloud Sessions flagged has_bot_check, otherwise generic
        # rerun_failed.
        csv_row = {k: row.get(k, "") for k in GOLD_COLUMNS}
        csv_row["No"] = str(no)
        csv_row["DOI"] = doi
        csv_row["Link"] = link
        if tier2_has_bot_check:
            tag = "iter-R:cloudflare_blocked"
            csv_row["Has Bot Check"] = "TRUE"
        else:
            tag = f"rerun_failed:{error_msg or 'all_tiers_empty'}"
        existing_notes = (csv_row.get("Notes") or "").strip()
        csv_row["Notes"] = (existing_notes + "|" + tag) if existing_notes else tag

    entry: dict[str, Any] = {
        "doi": doi,
        "no": no,
        "final_tier": final_tier,
        "duration_s": duration_s,
        "tier15_status": tier15_status,
        "tier15_authors_n": tier15_authors_n,
        "tier15_cost_usd": round(tier15_cost, 4),
        "tier15_verdicts_summary": _summarize_verdicts(tier15_verdicts),
        "tier2_status": tier2_status,
        "tier2_authors_n": tier2_authors_n,
        "tier2_cost_usd": round(tier2_cost, 4),
        "cost_usd": round(tier15_cost + tier2_cost, 4),
        "chooser_resolved": chooser_resolved,
        "error": error_msg,
        "csv_row": csv_row,
    }
    return entry


def _has_authors_raw(raw: dict[str, Any]) -> bool:
    """Tier 2 returns a dict with lowercase keys ('authors' list of dicts
    with 'name')."""
    arr = raw.get("authors") or []
    if not isinstance(arr, list):
        return False
    return any(isinstance(a, dict) and (a.get("name") or "").strip() for a in arr)


def _summarize_verdicts(rounds: list[Any]) -> dict[str, int]:
    """Compact summary of judge verdict counts for the checkpoint log."""
    summary = Counter()
    for r in rounds or []:
        verdicts = r.get("verdicts") or {} if isinstance(r, dict) else {}
        for field, v in verdicts.items():
            if isinstance(v, dict) and v.get("verdict"):
                summary[f"{field}_{v['verdict']}"] += 1
    return dict(summary)


# ---------- orchestrator ------------------------------------------------------

def _load_input_rows(input_path: Path, prefix: str | None, start_from: int, limit: int) -> list[dict[str, str]]:
    """If prefix is None, accept all DOIs; otherwise filter to the prefix."""
    if not input_path.exists():
        raise FileNotFoundError(input_path)
    rows: list[dict[str, str]] = []
    with input_path.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            doi = (r.get("DOI") or "").strip()
            if not doi:
                continue
            if prefix is not None and doi_prefix(doi) != prefix:
                continue
            rows.append(r)
    if start_from > 0:
        rows = rows[start_from:]
    if limit > 0:
        rows = rows[:limit]
    return rows


async def _run(args: argparse.Namespace) -> int:
    # Load eval/.env with override=True per CLAUDE.md "Dotenv gotcha" —
    # without override, a stale shell-exported ANTHROPIC_API_KEY can
    # silently shadow the clean value in eval/.env.
    try:
        from dotenv import load_dotenv
        env_path = SCRIPT_DIR.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=True)
    except ImportError:
        pass

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY not set in environment")
        return 5

    # Tier-2 pre-flight depends on backend.
    tier2_will_run = args.skip_tier != "2" and args.skip_tier_2 is False
    cloud_client: CloudClient | None = None
    if tier2_will_run:
        if args.tier2_backend == "cloud_sessions":
            bu_api_key = os.environ.get("BROWSER_USE_API_KEY")
            ok, reason = _preflight_cloud_api(bu_api_key)
            if not ok:
                log.error("Cloud Sessions Tier 2 pre-flight failed: %s", reason)
                return 5
            cloud_client = CloudClient(
                api_key=bu_api_key,
                model=args.tier2_model,
            )
            log.info(
                "Tier 2 backend=cloud_sessions  model=%s  proxy=US-residential(default)  concurrency=%d",
                args.tier2_model, args.tier2_concurrency,
            )
        else:
            if not _preflight_cdp(args.cdp_url):
                log.error(
                    "Tier 2 CDP pre-flight failed: %s not reachable. "
                    "Start Chrome with `--remote-debugging-port=9222` first.",
                    args.cdp_url,
                )
                return 4
            log.info("Tier 2 backend=local_cdp  cdp_url=%s", args.cdp_url)

    # Load the locked prompt twice — judge wants the post-loader form, Tier 2
    # wants the stripped front-matter form.
    version, system_prompt = load_prompt(args.prompt)
    prompt_text = args.prompt.read_text()
    tier2_prompt_body = _strip_yaml_front_matter(prompt_text)
    log.info("prompt=%s version=%s", args.prompt, version)

    output_path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cp_path = _checkpoint_path(output_path)
    cp = _load_checkpoint(cp_path)
    if cp:
        log.info("checkpoint: %d DOIs already processed in %s", len(cp), cp_path)

    log_jsonl = output_path.with_suffix(".tier-log.jsonl")

    rows = _load_input_rows(args.input, args.prefix, args.start_from, args.limit)
    if not rows:
        log.error("no rows match prefix=%s in %s", args.prefix, args.input)
        return 2
    log.info("input rows after prefix filter: %d  prefix=%s",
             len(rows), args.prefix if args.prefix else "(all)")

    to_process = [r for r in rows if (r.get("DOI") or "").strip() not in cp]
    log.info("rows to process (after checkpoint skip): %d", len(to_process))

    sem = asyncio.Semaphore(args.concurrency)
    tier2_sem = asyncio.Semaphore(args.tier2_concurrency)

    rolling_tier15 = deque(maxlen=TIER15_ROLLING_WINDOW)
    rolling_tier2_cloud = deque(maxlen=TIER2_CLOUD_ROLLING_WINDOW)
    total_cost = sum(float(e.get("cost_usd") or 0.0) for e in cp.values())
    halt_reason: str | None = None
    halt_code = 0

    async def one(row: dict[str, str], n: int) -> dict[str, Any]:
        async with sem:
            return await process_doi(
                row, no=n,
                cdp_url=args.cdp_url,
                extractor_model=args.extractor_model,
                tier2_model=args.tier2_model,
                tier2_max_steps=args.tier2_max_steps,
                tier2_sem=tier2_sem,
                system_prompt=system_prompt,
                tier2_prompt_body=tier2_prompt_body,
                api_key=api_key,
                harvester_url=args.harvester_url,
                tier1_5_timeout_s=args.tier15_timeout,
                tier1_5_poll_interval_s=args.tier15_poll,
                skip_tier=args.skip_tier,
                tier2_backend=args.tier2_backend,
                cloud_client=cloud_client,
            )

    tasks = [asyncio.create_task(one(r, idx + 1)) for idx, r in enumerate(to_process)]
    processed = 0
    for fut in asyncio.as_completed(tasks):
        try:
            entry = await fut
        except Exception as e:  # noqa: BLE001
            log.error("task crashed: %s", e)
            continue
        processed += 1
        _append_checkpoint(cp_path, entry)
        with log_jsonl.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        cp[entry["doi"]] = entry

        # Rolling Tier 1.5 health check
        t15 = entry.get("tier15_status")
        rolling_tier15.append(t15)
        if len(rolling_tier15) == TIER15_ROLLING_WINDOW:
            n_fatal = sum(
                1 for s in rolling_tier15
                if s in {"post-error", "harvester-5xx"}
            )
            if n_fatal / TIER15_ROLLING_WINDOW >= TIER15_FATAL_FRACTION:
                halt_reason = (
                    f"Tier 1.5 health: {n_fatal}/{TIER15_ROLLING_WINDOW} "
                    f"of last DOIs hit post-error/5xx — harvester likely down"
                )
                halt_code = 3
                break

        # Rolling Tier 2 Cloud health check (only when backend=cloud_sessions)
        if args.tier2_backend == "cloud_sessions":
            t2 = entry.get("tier2_status") or ""
            rolling_tier2_cloud.append(t2)
            if len(rolling_tier2_cloud) == TIER2_CLOUD_ROLLING_WINDOW:
                n_cloud_fatal = sum(
                    1 for s in rolling_tier2_cloud
                    if s.startswith("cloud_error:5") or s == "cloud_timeout"
                    or s.startswith("cloud_exception:")
                )
                if n_cloud_fatal / TIER2_CLOUD_ROLLING_WINDOW >= TIER2_CLOUD_FATAL_FRACTION:
                    halt_reason = (
                        f"Tier 2 Cloud health: {n_cloud_fatal}/{TIER2_CLOUD_ROLLING_WINDOW} "
                        f"of last DOIs hit 5xx/timeout/exception — Cloud likely degraded"
                    )
                    halt_code = 8
                    break

        # Cost guard
        total_cost += float(entry.get("cost_usd") or 0.0)
        if total_cost >= args.max_cost_usd:
            halt_reason = (
                f"max-cost reached: ${total_cost:.2f} >= ${args.max_cost_usd:.2f}"
            )
            halt_code = 6
            break

        log.info(
            "[%d/%d] %s tier=%s t15=%s t2=%s cost=$%.3f (cum $%.2f)",
            processed, len(to_process), entry["doi"],
            entry["final_tier"], entry["tier15_status"], entry["tier2_status"],
            entry["cost_usd"], total_cost,
        )

    # Cancel any still-pending tasks if we halted
    for t in tasks:
        if not t.done():
            t.cancel()
    # Drain cancellations
    for t in tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    # Close the shared CloudClient if we opened one
    if cloud_client is not None:
        try:
            await cloud_client.aclose()
        except Exception:  # noqa: BLE001
            pass

    if halt_reason:
        log.error("HALT: %s", halt_reason)

    # ---- write subset CSV ---------------------------------------------------
    # Use the final-state of cp (checkpoint dict) since it has everything.
    csv_rows = []
    for doi, entry in cp.items():
        row = entry.get("csv_row")
        if row:
            csv_rows.append(row)
    # Stable order: by No
    csv_rows.sort(key=lambda r: int(r.get("No") or 0))

    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GOLD_COLUMNS)
        w.writeheader()
        for r in csv_rows:
            w.writerow({k: r.get(k, "") for k in GOLD_COLUMNS})
    os.replace(tmp, output_path)
    log.info("wrote %s (%d rows)", output_path, len(csv_rows))

    # ---- summary ------------------------------------------------------------
    by_tier = Counter(e.get("final_tier") for e in cp.values())
    n_chooser = sum(1 for e in cp.values() if e.get("chooser_resolved"))
    print()
    print(f"=== rerun_targeted summary  prefix={args.prefix} ===")
    print(f"  processed in this run    : {processed}")
    print(f"  total in checkpoint      : {len(cp)}")
    print(f"  chooser resolutions      : {n_chooser}")
    print(f"  total cost (cumulative)  : ${total_cost:.2f}")
    print(f"  final_tier breakdown     :")
    for t, n in by_tier.most_common():
        print(f"    {t:<24s} {n}")
    print(f"  CSV : {output_path}")
    print(f"  log : {log_jsonl}")
    print(f"  checkpoint : {cp_path}")
    if halt_reason:
        print(f"  HALT REASON : {halt_reason}")
    print()
    return halt_code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", type=Path, required=True,
                    help="A triage CSV (rerun-no-authors.csv / rerun-no-rases.csv)")
    ap.add_argument("--prefix", default=None,
                    help="Optional DOI prefix filter (e.g. 10.1016, 10.1109). Omit to process all rows.")
    ap.add_argument("--output", type=Path, required=True,
                    help="Subset CSV output path (12-column gold schema)")
    ap.add_argument("--limit", type=int, default=0,
                    help="Dry-run cap: only the first N matching rows (0 = no cap)")
    ap.add_argument("--start-from", type=int, default=0,
                    help="Skip the first N matching rows (resume after partial fail)")
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                    help=f"Top-level parallel DOIs (default: {DEFAULT_CONCURRENCY})")
    ap.add_argument("--tier2-backend", choices=["cloud_sessions", "local_cdp"],
                    default=DEFAULT_TIER2_BACKEND,
                    help=f"Tier 2 implementation (default: {DEFAULT_TIER2_BACKEND}). "
                         "cloud_sessions uses browser-use Cloud v3 sessions API "
                         "(residential proxies + auto-CAPTCHA). local_cdp uses "
                         "the local Chrome at --cdp-url.")
    ap.add_argument("--tier2-concurrency", type=int, default=None,
                    help="Tier 2 sub-concurrency (default: 25 for cloud_sessions, "
                         "2 for local_cdp). Cloud Starter tier supports 50 concurrent.")
    ap.add_argument("--cdp-url", default=DEFAULT_CDP_URL,
                    help=f"Chrome CDP URL for local_cdp backend (default: {DEFAULT_CDP_URL})")
    ap.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT,
                    help=f"Locked extraction prompt (default: {DEFAULT_PROMPT})")
    ap.add_argument("--extractor-model", default=DEFAULT_EXTRACTOR_MODEL)
    ap.add_argument("--tier2-model", default=None,
                    help=f"Tier 2 LLM model (default: {DEFAULT_TIER2_MODEL_CLOUD} "
                         f"for cloud_sessions, {DEFAULT_TIER2_MODEL_LOCAL} for local_cdp)")
    ap.add_argument("--tier2-max-steps", type=int, default=DEFAULT_TIER2_MAX_STEPS,
                    help="local_cdp only: max browser-use Agent steps per DOI")
    ap.add_argument("--harvester-url", default=DEFAULT_HARVESTER)
    ap.add_argument("--tier15-timeout", type=int, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--tier15-poll", type=int, default=DEFAULT_POLL_INTERVAL_S)
    ap.add_argument("--max-cost-usd", type=float, default=DEFAULT_MAX_COST_USD,
                    help=f"Halt when cumulative cost reaches this (default: ${DEFAULT_MAX_COST_USD:.0f})")
    ap.add_argument("--skip-tier", choices=["1.5", "2"], default=None,
                    help="Debug-only: skip Tier 1.5 or Tier 2 entirely")
    # Compat alias
    ap.add_argument("--skip-tier-2", action="store_true",
                    help="Alias for --skip-tier 2 (kept for shell convenience)")
    args = ap.parse_args(argv)

    if args.skip_tier_2:
        args.skip_tier = "2"

    # Backend-dependent defaults
    if args.tier2_concurrency is None:
        args.tier2_concurrency = (
            DEFAULT_TIER2_SUBCONCURRENCY_CLOUD if args.tier2_backend == "cloud_sessions"
            else DEFAULT_TIER2_SUBCONCURRENCY_LOCAL
        )
    if args.tier2_model is None:
        args.tier2_model = (
            DEFAULT_TIER2_MODEL_CLOUD if args.tier2_backend == "cloud_sessions"
            else DEFAULT_TIER2_MODEL_LOCAL
        )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Validate input early
    if not args.input.exists():
        log.error("input not found: %s", args.input)
        return 2

    # Validate prefix shape (rough) if provided
    if args.prefix and not re.match(r"^10\.\d+$", args.prefix):
        log.warning("unusual --prefix %r; expected like '10.1016' or '10.1109'", args.prefix)

    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
