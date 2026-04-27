"""Production-scale AI Goldie extraction via browser-use Cloud Tasks API.

Reads `ai-goldie-source-10k.csv` in 100-DOI windows. For each window:
  - POSTs each DOI to `https://api.browser-use.com/api/v2/tasks` with the
    locked AI Goldie prompt + DOI URL as the `task` and the v1 record_extraction
    JSON Schema as `structuredOutput`.
  - Polls task status until terminal.
  - Writes `eval/data/ai-goldie-N.csv` in raw gold-standard column order.

Bullet-proof contract (per OBJECTIVE.md):
  1. Resumable     — `eval/data/.checkpoint/ai-goldie-N.partial.jsonl` records every
                     landed DOI; re-runs skip what's already in the partial.
  2. Atomic        — final CSV is `.tmp` + rename.
  3. Idempotent    — DOI-keyed; same window produces same output.
  4. Transparent   — `eval/data/ai-goldie-N.failures.jsonl` is the source of truth
                     for blank rows. Filled rows + failures lines == 100 per batch.
  5. Bot-resilient — on `has_bot_check=true`, retry once with `--proxy residential`.
  6. Schema-locked — `structuredOutput` enforced server-side by browser-use Cloud.
  7. Cost-capped   — `max_agent_steps=18`, retry cap N=3, optional `--max-cost-usd`.

Usage:
  Smoke (Phase E.1) — extract batch 1 only, then user reviews:
    eval/.venv/bin/python eval/scripts/extract_batch_cloud.py \\
      --prompt eval/prompts/ai-goldie-v1.md \\
      --source eval/data/ai-goldie-source-10k.csv \\
      --batches 1 --concurrency 100

  Burn-down (Phase E.2) — batches 2-100 in parallel:
    eval/.venv/bin/python eval/scripts/extract_batch_cloud.py \\
      --prompt eval/prompts/ai-goldie-v1.md \\
      --source eval/data/ai-goldie-source-10k.csv \\
      --start-batch 2 --batches 99 --concurrency 200

  Resume single batch:
    eval/.venv/bin/python eval/scripts/extract_batch_cloud.py \\
      --start-batch 17 --batches 1
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

EVAL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT = EVAL_DIR / "prompts" / "ai-goldie-v1.md"
DEFAULT_SOURCE = EVAL_DIR / "data" / "ai-goldie-source-10k.csv"
DEFAULT_OUTPUT_DIR = EVAL_DIR / "data"
CHECKPOINT_DIR_NAME = ".checkpoint"

API_BASE = "https://api.browser-use.com/api/v2"
DEFAULT_MODEL = "claude-sonnet-4-5"
BATCH_SIZE = 100
DEFAULT_MAX_AGENT_STEPS = 18
DEFAULT_RETRY_CAP = 3
RETRY_BACKOFF_SEC = (10.0, 60.0, 300.0)
DEFAULT_POLL_INTERVAL = 5.0
DEFAULT_TASK_TIMEOUT_SEC = 30 * 60  # 30 min hard cap per task

GOLD_COLUMNS = [
    "No", "DOI", "Link", "Authors", "Abstract", "PDF URL",
    "Status", "Notes", "Has Bot Check", "Resolves To PDF",
    "broken_doi", "no english",
]

log = logging.getLogger("ai-goldie-cloud")


# ---- v1 schema (matches eval/scripts/run_ai_goldie.py) ---------------------

class AuthorOut(BaseModel):
    name: str
    rasses: str = ""
    corresponding_author: bool = False
    affiliations: list[str] = Field(default_factory=list)


class ExtractionOut(BaseModel):
    authors: list[AuthorOut]
    abstract: str | None = None
    pdf_url: str | None = None
    has_bot_check: bool = False
    resolves_to_pdf: bool = False
    broken_doi: bool = False
    no_english: bool = False
    notes: str | None = None


# ---- prompt loading --------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
_SYSTEM_PROMPT_BLOCK_RE = re.compile(
    r"##\s*System prompt\s*\n+```[a-z]*\n(?P<body>.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


def load_prompt(path: Path) -> tuple[str, str]:
    raw = path.read_text(encoding="utf-8")
    version = "unknown"
    fm = _FRONTMATTER_RE.match(raw)
    if fm:
        for line in fm.group(0).splitlines():
            if line.strip().startswith("version:"):
                version = line.split(":", 1)[1].strip()
                break
    m = _SYSTEM_PROMPT_BLOCK_RE.search(raw)
    if not m:
        raise SystemExit(f"could not find '## System prompt' fenced block in {path}")
    return version, m.group("body").strip()


def build_task(system_prompt_body: str, doi: str, link: str) -> str:
    return (
        f"{system_prompt_body}\n\n"
        f"---\n\n"
        f"DOI: {doi}\n"
        f"URL: {link}\n\n"
        "Navigate to the URL and emit the structured extraction when you have enough data."
    )


# ---- per-DOI result types --------------------------------------------------

@dataclass
class TaskResult:
    no: int
    doi: str
    link: str
    extraction: dict[str, Any] | None
    task_id: str | None
    duration_s: float
    retries: int
    error: str | None = None
    cost_usd: float | None = None


# ---- checkpoint IO ---------------------------------------------------------

def checkpoint_dir(output_dir: Path) -> Path:
    return output_dir / CHECKPOINT_DIR_NAME


def partial_path(output_dir: Path, batch_no: int) -> Path:
    return checkpoint_dir(output_dir) / f"ai-goldie-{batch_no}.partial.jsonl"


def failures_path(output_dir: Path, batch_no: int) -> Path:
    return output_dir / f"ai-goldie-{batch_no}.failures.jsonl"


def final_csv_path(output_dir: Path, batch_no: int) -> Path:
    return output_dir / f"ai-goldie-{batch_no}.csv"


def load_partial(path: Path) -> dict[str, dict[str, Any]]:
    """DOI → record dict, from append-only JSONL."""
    if not path.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                doi = (obj.get("DOI") or "").strip()
                if doi:
                    out[doi] = obj  # last write wins (resume after retry)
            except json.JSONDecodeError:
                continue
    return out


def append_partial(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_failure(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


# ---- record shaping --------------------------------------------------------

def normalize_author(a: dict[str, Any]) -> dict[str, Any]:
    name = str(a.get("name") or "").strip()
    rasses = a.get("rasses")
    if isinstance(rasses, list):
        rasses = " | ".join(str(s or "").strip() for s in rasses if str(s or "").strip())
    elif rasses is None:
        affs = a.get("affiliations") or []
        if isinstance(affs, list):
            rasses = " | ".join(str(s or "").strip() for s in affs if str(s or "").strip())
        else:
            rasses = str(affs or "").strip()
    else:
        rasses = str(rasses).strip()
    corr = a.get("corresponding_author")
    if corr is None:
        corr = a.get("is_corresponding")
    return {"name": name, "rasses": rasses or "", "corresponding_author": bool(corr)}


def to_gold_row(r: TaskResult) -> dict[str, Any]:
    e = r.extraction or {}
    raw_authors = e.get("authors") or []
    authors = [normalize_author(a) for a in raw_authors if isinstance(a, dict)]
    return {
        "No": r.no,
        "DOI": r.doi,
        "Link": r.link,
        "Authors": json.dumps(authors, ensure_ascii=False) if authors else "",
        "Abstract": e.get("abstract") or "",
        "PDF URL": e.get("pdf_url") or "",
        "Status": "TRUE" if (e and not e.get("has_bot_check") and not r.error) else "FALSE",
        "Notes": e.get("notes") or (r.error or ""),
        "Has Bot Check": str(bool(e.get("has_bot_check"))).upper() if e else "",
        "Resolves To PDF": str(bool(e.get("resolves_to_pdf"))).upper() if e else "",
        "broken_doi": str(bool(e.get("broken_doi"))).upper() if e else "",
        "no english": str(bool(e.get("no_english"))).upper() if e else "",
    }


def write_csv_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GOLD_COLUMNS)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    tmp.replace(path)


# ---- browser-use Cloud client ----------------------------------------------

class CloudClient:
    """Minimal async client for browser-use Cloud Tasks v2.

    Notes:
      - Field naming: the OpenAPI schema uses camelCase (`structuredOutput`,
        `llm`, `maxAgentSteps`). If the deployed API rejects a field, the
        error body is logged and propagated for the caller to handle.
      - Polling: 5s default, with a 30 min hard cap per task.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        max_agent_steps: int = DEFAULT_MAX_AGENT_STEPS,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        task_timeout_sec: float = DEFAULT_TASK_TIMEOUT_SEC,
        timeout_sec: float = 60.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_agent_steps = max_agent_steps
        self._poll_interval = poll_interval
        self._task_timeout_sec = task_timeout_sec
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            headers={
                "X-Browser-Use-API-Key": api_key,
                "Content-Type": "application/json",
            },
            timeout=timeout_sec,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def create_task(
        self,
        task_text: str,
        structured_output_schema_json: str,
        proxy_residential: bool = False,
    ) -> str:
        body: dict[str, Any] = {
            "task": task_text,
            "structuredOutput": structured_output_schema_json,
            "llm": self._model,
            "maxAgentSteps": self._max_agent_steps,
            "saveBrowserData": False,
        }
        if proxy_residential:
            body["proxyType"] = "residential"
        resp = await self._client.post("/tasks", json=body)
        if resp.status_code >= 400:
            raise RuntimeError(f"create_task http {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        task_id = data.get("id") or data.get("task_id") or data.get("taskId")
        if not task_id:
            raise RuntimeError(f"create_task returned no id: {data}")
        return task_id

    async def wait_for_task(self, task_id: str) -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        start = loop.time()
        while True:
            if loop.time() - start > self._task_timeout_sec:
                raise TimeoutError(f"task {task_id} exceeded {self._task_timeout_sec}s")
            resp = await self._client.get(f"/tasks/{task_id}")
            if resp.status_code >= 400:
                raise RuntimeError(f"get_task http {resp.status_code}: {resp.text[:500]}")
            data = resp.json()
            status = (data.get("status") or "").lower()
            if status in {"finished", "completed", "succeeded", "success"}:
                return data
            if status in {"failed", "error", "cancelled", "canceled", "stopped"}:
                raise RuntimeError(f"task {task_id} terminal-failed: status={status} body={data}")
            await asyncio.sleep(self._poll_interval)


def extraction_from_task(task_data: dict[str, Any]) -> dict[str, Any] | None:
    """Walk common keys until we find the structured output object."""
    for k in ("output", "structured_output", "structuredOutput", "result", "data"):
        v = task_data.get(k)
        if v is None:
            continue
        if isinstance(v, dict):
            # If wrapper contains another `output`, drill in.
            inner = v.get("output") if "output" in v else v
            if isinstance(inner, dict) and ("authors" in inner or "abstract" in inner):
                return inner
            if isinstance(v, dict) and ("authors" in v or "abstract" in v):
                return v
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, dict) and ("authors" in parsed or "abstract" in parsed):
                    return parsed
            except json.JSONDecodeError:
                continue
    return None


def task_cost_usd(task_data: dict[str, Any]) -> float | None:
    for k in ("cost_usd", "costUsd", "cost"):
        v = task_data.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return None


# ---- per-DOI runner --------------------------------------------------------

async def run_doi(
    client: CloudClient,
    sem: asyncio.Semaphore,
    no: int,
    doi: str,
    link: str,
    task_text: str,
    schema_json: str,
    retry_cap: int,
) -> TaskResult:
    loop = asyncio.get_event_loop()
    start = loop.time()
    last_error: str | None = None
    last_task_id: str | None = None
    last_cost: float | None = None
    proxy_used = False

    for attempt in range(retry_cap + 1):
        async with sem:
            try:
                task_id = await client.create_task(
                    task_text=task_text,
                    structured_output_schema_json=schema_json,
                    proxy_residential=proxy_used,
                )
                last_task_id = task_id
                data = await client.wait_for_task(task_id)
                last_cost = task_cost_usd(data)
                extraction = extraction_from_task(data)
                if extraction is None:
                    last_error = "no_structured_output"
                else:
                    # If bot-checked and we haven't tried proxy yet, retry with proxy.
                    if extraction.get("has_bot_check") and not proxy_used and attempt < retry_cap:
                        proxy_used = True
                        last_error = "has_bot_check (retrying with residential proxy)"
                        await asyncio.sleep(RETRY_BACKOFF_SEC[min(attempt, len(RETRY_BACKOFF_SEC) - 1)])
                        continue
                    return TaskResult(
                        no=no, doi=doi, link=link,
                        extraction=extraction,
                        task_id=task_id,
                        duration_s=round(loop.time() - start, 2),
                        retries=attempt,
                        error=None,
                        cost_usd=last_cost,
                    )
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"

        if attempt < retry_cap:
            backoff = RETRY_BACKOFF_SEC[min(attempt, len(RETRY_BACKOFF_SEC) - 1)]
            await asyncio.sleep(backoff)

    return TaskResult(
        no=no, doi=doi, link=link,
        extraction=None,
        task_id=last_task_id,
        duration_s=round(loop.time() - start, 2),
        retries=retry_cap,
        error=last_error or "unknown_failure",
        cost_usd=last_cost,
    )


# ---- per-batch runner ------------------------------------------------------

def read_window(source_csv: Path, batch_no: int) -> list[dict[str, str]]:
    """Return rows whose `No` is in the window for this batch (1-indexed)."""
    lo = (batch_no - 1) * BATCH_SIZE + 1
    hi = batch_no * BATCH_SIZE
    rows: list[dict[str, str]] = []
    with source_csv.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            try:
                n = int(r.get("No") or 0)
            except ValueError:
                continue
            if lo <= n <= hi:
                rows.append({"No": str(n), "DOI": r.get("DOI") or "", "Link": r.get("Link") or ""})
    return rows


async def run_batch(
    client: CloudClient,
    output_dir: Path,
    batch_no: int,
    rows: list[dict[str, str]],
    system_prompt_body: str,
    schema_json: str,
    concurrency: int,
    retry_cap: int,
) -> dict[str, Any]:
    p_path = partial_path(output_dir, batch_no)
    f_path = failures_path(output_dir, batch_no)
    final_path = final_csv_path(output_dir, batch_no)

    landed = load_partial(p_path)
    log.info("batch %d: %d/%d already in partial", batch_no, len(landed), len(rows))

    pending = [r for r in rows if r["DOI"] not in landed]

    sem = asyncio.Semaphore(concurrency)

    async def runner(row):
        no = int(row["No"])
        doi = row["DOI"]
        link = row["Link"] or f"https://doi.org/{doi}"
        task_text = build_task(system_prompt_body, doi, link)
        result = await run_doi(client, sem, no, doi, link, task_text, schema_json, retry_cap)
        gold_row = to_gold_row(result)
        gold_row["_meta"] = {
            "task_id": result.task_id,
            "duration_s": result.duration_s,
            "retries": result.retries,
            "cost_usd": result.cost_usd,
            "error": result.error,
        }
        append_partial(p_path, gold_row)
        if result.error or not result.extraction:
            append_failure(f_path, {
                "DOI": result.doi,
                "No": result.no,
                "task_id": result.task_id,
                "error": result.error,
                "retries": result.retries,
            })
            log.warning("[%d/%d] No=%d %s FAIL: %s", batch_no, len(rows), no, doi, result.error)
        else:
            log.info("[%d/%d] No=%d %s ok %.1fs retries=%d",
                     batch_no, len(rows), no, doi, result.duration_s, result.retries)

    if pending:
        await asyncio.gather(*(runner(r) for r in pending))

    landed_full = load_partial(p_path)
    by_no: dict[int, dict[str, Any]] = {}
    for rec in landed_full.values():
        try:
            by_no[int(rec.get("No"))] = rec
        except (TypeError, ValueError):
            continue

    rows_out: list[dict[str, Any]] = []
    for r in rows:
        no = int(r["No"])
        rec = by_no.get(no)
        if rec is None:
            rows_out.append({
                "No": no, "DOI": r["DOI"], "Link": r["Link"],
                "Authors": "", "Abstract": "", "PDF URL": "",
                "Status": "FALSE", "Notes": "missing_from_partial",
                "Has Bot Check": "", "Resolves To PDF": "",
                "broken_doi": "", "no english": "",
            })
        else:
            clean = {k: rec.get(k, "") for k in GOLD_COLUMNS}
            rows_out.append(clean)

    write_csv_atomic(final_path, rows_out)

    failures = sum(1 for r in rows_out if r.get("Status") == "FALSE" or r.get("Notes"))
    return {
        "batch": batch_no,
        "rows": len(rows_out),
        "failures": failures,
        "csv": str(final_path),
        "partial": str(p_path),
        "failures_log": str(f_path),
    }


# ---- main ------------------------------------------------------------------

async def main_async(args) -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv(EVAL_DIR / ".env", override=True)
    except ImportError:
        pass

    api_key = os.environ.get("BROWSER_USE_API_KEY")
    if not api_key:
        raise SystemExit("BROWSER_USE_API_KEY not set (eval/.env)")

    version, system_prompt_body = load_prompt(args.prompt)
    log.info("prompt %s (version=%s, %d chars)", args.prompt, version, len(system_prompt_body))
    schema_json = json.dumps(ExtractionOut.model_json_schema())

    client = CloudClient(
        api_key=api_key,
        model=args.model,
        max_agent_steps=args.max_agent_steps,
    )
    try:
        end_batch = args.start_batch + args.batches - 1
        log.info("running batches %d..%d concurrency=%d model=%s",
                 args.start_batch, end_batch, args.concurrency, args.model)
        summaries = []
        total_cost = 0.0
        for batch_no in range(args.start_batch, end_batch + 1):
            rows = read_window(args.source, batch_no)
            if not rows:
                log.warning("batch %d: no rows in source CSV — skipping", batch_no)
                continue
            summary = await run_batch(
                client=client,
                output_dir=args.output_dir,
                batch_no=batch_no,
                rows=rows,
                system_prompt_body=system_prompt_body,
                schema_json=schema_json,
                concurrency=args.concurrency,
                retry_cap=args.retry_cap,
            )
            summaries.append(summary)
            log.info("batch %d done: %s", batch_no, summary)

            if args.max_cost_usd and total_cost > args.max_cost_usd:
                log.warning("cost cap %.2f reached after batch %d — stopping", args.max_cost_usd, batch_no)
                break

        ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        meta = {
            "version": version,
            "prompt": str(args.prompt),
            "source": str(args.source),
            "model": args.model,
            "start_batch": args.start_batch,
            "end_batch": end_batch,
            "concurrency": args.concurrency,
            "summaries": summaries,
            "timestamp": ts,
        }
        meta_path = args.output_dir / f"ai-goldie-batches-{args.start_batch}-{end_batch}-{ts}.meta.json"
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        print(f"wrote run meta: {meta_path}")
    finally:
        await client.aclose()

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    ap.add_argument("--start-batch", type=int, default=1)
    ap.add_argument("--batches", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=200)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--max-agent-steps", type=int, default=DEFAULT_MAX_AGENT_STEPS)
    ap.add_argument("--retry-cap", type=int, default=DEFAULT_RETRY_CAP)
    ap.add_argument("--max-cost-usd", type=float, default=None)
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    sys.exit(main())
