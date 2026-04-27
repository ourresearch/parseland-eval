"""Run the AI Goldie Machine prompt against a CSV of DOIs and emit gold-standard.json shape.

Defaults to train-50.csv. Hard-refuses to run on holdout (prompt iteration leakage guard).

Stack:
  - browser-use library + real Chrome over CDP (the Pass C codepath)
  - v0 prompt + record_extraction tool schema lifted into a Pydantic model
  - sequential by default; --concurrency N to parallelize via asyncio (best-effort, browser-use manages tabs)
  - BYOK Anthropic via eval/.env

Output:
  runs/ai-goldie-<version>-<input-stem>-<timestamp>.json   list of records, gold-standard.json shape
  runs/ai-goldie-<version>-<input-stem>-<timestamp>.meta.json  timing + step counts + errors

Launch Chrome first:
    pkill -x "Google Chrome"          # quit fully
    open -a "Google Chrome" --args \\
      --remote-debugging-port=9222 \\
      --profile-directory="Profile 2"

Run:
    /path/to/eval/.venv/bin/python eval/scripts/run_ai_goldie.py \\
        --prompt eval/prompts/ai-goldie-v0.md \\
        --input  eval/goldie/train-50.csv \\
        --limit  5                            # smoke first
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
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

EVAL_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = EVAL_DIR.parent
DEFAULT_PROMPT = EVAL_DIR / "prompts" / "ai-goldie-v0.md"
DEFAULT_INPUT = EVAL_DIR / "goldie" / "train-50.csv"
DEFAULT_OUTPUT_DIR = REPO_DIR / "runs"
DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_CDP = "http://localhost:9222"
DEFAULT_MAX_STEPS = 18

log = logging.getLogger("ai-goldie")


# ---- v0 schema (lifted from eval/prompts/ai-goldie-v0.md, record_extraction tool) ----

class AuthorOut(BaseModel):
    name: str
    affiliations: list[str] = Field(default_factory=list)


class ExtractionOut(BaseModel):
    """Mirror of the v0 record_extraction input_schema. Verbatim from prompt v0."""
    authors: list[AuthorOut]
    abstract: str | None = None
    pdf_url: str | None = None
    has_bot_check: bool = False
    resolves_to_pdf: bool = False
    broken_doi: bool = False
    no_english: bool = False
    notes: str | None = None


# ---- prompt loading ----

_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
_SYSTEM_PROMPT_BLOCK_RE = re.compile(
    r"##\s*System prompt\s*\n+```[a-z]*\n(?P<body>.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


def load_prompt(path: Path) -> tuple[str, str]:
    """Return (version, system_prompt_body) extracted from a versioned prompt .md."""
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
    """Compose the per-DOI task. We lift the v0 system-prompt body verbatim and
    append the DOI/URL. browser-use's framework supplies its own tool surface;
    the v0 prompt's references to browser_open/browser_snapshot/etc are advisory
    (they map conceptually to browser-use's navigate/get_state/etc)."""
    return (
        f"{system_prompt_body}\n\n"
        f"---\n\n"
        f"DOI: {doi}\n"
        f"URL: {link}\n\n"
        f"Navigate to the URL and emit the structured extraction when you have enough data."
    )


# ---- per-row execution ----

@dataclass
class RunRow:
    no: int
    doi: str
    link: str
    extraction: dict[str, Any]
    step_count: int
    duration_s: float
    error: str | None = None


async def extract_one(
    browser, llm, row: dict[str, str], system_prompt_body: str, max_steps: int
) -> RunRow:
    from browser_use import Agent

    no = int(row["No"])
    doi = row["DOI"]
    link = row["Link"]
    start = time.monotonic()
    try:
        agent = Agent(
            task=build_task(system_prompt_body, doi, link),
            llm=llm,
            browser=browser,
            output_model_schema=ExtractionOut,
            use_vision=False,
            max_failures=3,
        )
        history = await agent.run(max_steps=max_steps)
        final = history.final_result()
        if isinstance(final, ExtractionOut):
            extraction = final.model_dump()
        elif isinstance(final, dict):
            extraction = final
        elif isinstance(final, str):
            try:
                extraction = json.loads(final)
            except json.JSONDecodeError:
                extraction = {}
        else:
            extraction = {}
        return RunRow(
            no=no, doi=doi, link=link,
            extraction=extraction,
            step_count=len(history.history),
            duration_s=round(time.monotonic() - start, 2),
            error=None if extraction else "no_structured_output",
        )
    except Exception as e:
        return RunRow(
            no=no, doi=doi, link=link, extraction={},
            step_count=0, duration_s=round(time.monotonic() - start, 2),
            error=f"{type(e).__name__}: {e}",
        )


# ---- output writing (gold-standard.json shape) ----

def to_gold_record(r: RunRow) -> dict[str, Any]:
    e = r.extraction or {}
    authors = e.get("authors") or []
    return {
        "No": r.no,
        "DOI": r.doi,
        "Link": r.link,
        "Authors": authors,
        "Abstract": e.get("abstract") or "",
        "PDF URL": e.get("pdf_url") or "",
        "Status": (not e.get("has_bot_check")) and not r.error,
        "Notes": e.get("notes") or (r.error or ""),
        "Has Bot Check": bool(e.get("has_bot_check")) if e else None,
        "Resolves To PDF": bool(e.get("resolves_to_pdf")) if e else None,
        "broken_doi": bool(e.get("broken_doi")) if e else False,
        "no english": bool(e.get("no_english")) if e else False,
    }


# ---- main ----

async def main_async(args) -> int:
    from browser_use import Browser
    from browser_use.llm import ChatAnthropic

    try:
        from dotenv import load_dotenv
        load_dotenv(EVAL_DIR / ".env", override=True)
    except ImportError:
        pass

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set (eval/.env)")

    input_path = Path(args.input)
    if "holdout" in input_path.name.lower() and not args.allow_holdout:
        raise SystemExit(
            f"refusing to run on '{input_path.name}' — holdout is sealed during prompt iteration. "
            f"Pass --allow-holdout if you really mean it (final validation only)."
        )

    version, system_prompt_body = load_prompt(Path(args.prompt))
    log.info("prompt: %s (version=%s, %d chars)", args.prompt, version, len(system_prompt_body))

    with input_path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    rows = [r for r in rows if r.get("No")]
    if args.limit:
        rows = rows[: args.limit]
    log.info("input: %s (%d DOIs, model=%s, cdp=%s, concurrency=%d, max_steps=%d)",
             input_path.name, len(rows), args.model, args.cdp_url, args.concurrency, args.max_steps)

    llm = ChatAnthropic(model=args.model, api_key=api_key, max_tokens=4096)
    browser = Browser(cdp_url=args.cdp_url)

    results: list[RunRow] = []
    sem = asyncio.Semaphore(args.concurrency)
    total = len(rows)
    completed = 0
    completed_lock = asyncio.Lock()

    async def runner(row):
        nonlocal completed
        async with sem:
            r = await extract_one(browser, llm, row, system_prompt_body, args.max_steps)
            async with completed_lock:
                completed += 1
                idx = completed
            status = "err" if r.error else "ok"
            log.info("[%s] %d/%d No=%d %s steps=%d %.1fs %s",
                     status, idx, total, r.no, r.doi, r.step_count, r.duration_s,
                     (r.error or "")[:80])
            return r

    if args.concurrency <= 1:
        for row in rows:
            results.append(await runner(row))
    else:
        results = await asyncio.gather(*(runner(r) for r in rows))

    results.sort(key=lambda r: r.no)

    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = f"ai-goldie-{version}-{input_path.stem}-{ts}"
    out_json = args.output_dir / f"{stem}.json"
    out_meta = args.output_dir / f"{stem}.meta.json"

    out_json.write_text(
        json.dumps([to_gold_record(r) for r in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    errors = sum(1 for r in results if r.error)
    out_meta.write_text(
        json.dumps({
            "version": version,
            "prompt_path": str(args.prompt),
            "input_path": str(input_path),
            "model": args.model,
            "cdp_url": args.cdp_url,
            "concurrency": args.concurrency,
            "max_steps": args.max_steps,
            "rows": [asdict(r) for r in results],
            "totals": {
                "rows": len(results),
                "errors": errors,
                "wall_seconds": round(sum(r.duration_s for r in results), 2),
                "total_steps": sum(r.step_count for r in results),
            },
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"wrote {len(results)} rows ({errors} errors)")
    print(f"  ai output : {out_json}")
    print(f"  meta      : {out_meta}")
    print(f"\nDiff next:\n  python eval/scripts/diff_goldie.py \\\n"
          f"    --human eval/goldie/human-goldie-v1-pre-audit.csv \\\n"
          f"    --ai {out_json.relative_to(REPO_DIR)} \\\n"
          f"    --output-md eval/goldie/disagreements-{stem}.md \\\n"
          f"    --output-summary eval/goldie/summary-{stem}.json")
    return 0 if errors == 0 else 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prompt", default=str(DEFAULT_PROMPT), help=f"default: {DEFAULT_PROMPT}")
    ap.add_argument("--input", default=str(DEFAULT_INPUT), help=f"default: {DEFAULT_INPUT}")
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help=f"default: {DEFAULT_OUTPUT_DIR}")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--cdp-url", default=DEFAULT_CDP)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=1, help="parallel agents (default 1; >1 best-effort)")
    ap.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    ap.add_argument("--allow-holdout", action="store_true",
                    help="DANGER: bypass the holdout-leakage guard. Only for final validation.")
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    sys.exit(main())
