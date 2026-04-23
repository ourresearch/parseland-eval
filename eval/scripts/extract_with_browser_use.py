"""Pilot Pass C: extract metadata using browser-use + user's real Chrome.

Connects to a Chrome instance the user has launched with
    open -a "Google Chrome" --args --remote-debugging-port=9222 --profile-directory="Profile 2"

Per DOI, spawns a browser_use.Agent with the user's real Chrome (via CDP),
instructs it to extract gold-standard fields, and persists the structured
output as a row in `eval/data/random-50-chrome.csv`.

Differences from Pass B (our hand-rolled Claude tool-use loop):
- browser-use owns the agent loop (history management, DOM extraction, retries)
- uses user's real Chrome profile → real cookies, real fingerprint
- uses Pydantic `output_model_schema` for schema-enforced final output
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from parseland_eval.paths import EVAL_DIR

try:
    from dotenv import load_dotenv
    load_dotenv(EVAL_DIR / ".env", override=True)
except ImportError:
    pass

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

# browser-use's ChatAnthropic Literal doesn't include claude-sonnet-4-6 in this
# release — closest available is claude-sonnet-4-5. For our 3-way comparison
# this is the faithfulness-vs-availability tradeoff.
DEFAULT_MODEL = "claude-sonnet-4-5"
CDP_URL = "http://localhost:9222"
INPUT_CSV = EVAL_DIR / "data" / "random-50.csv"
OUTPUT_CSV = EVAL_DIR / "data" / "random-50-chrome.csv"
META_JSON = EVAL_DIR / "data" / "random-50-chrome.meta.json"

GOLD_COLUMNS = [
    "No", "DOI", "Link", "Authors", "Abstract", "PDF URL",
    "Status", "Notes", "Has Bot Check", "Resolves To PDF",
    "broken_doi", "no english",
]


class AuthorOut(BaseModel):
    name: str
    affiliations: list[str] = Field(default_factory=list)


class ExtractionOut(BaseModel):
    """Structured output the browser-use agent must emit per DOI."""
    authors: list[AuthorOut]
    abstract: str | None = None
    pdf_url: str | None = None
    has_bot_check: bool = False
    resolves_to_pdf: bool = False
    broken_doi: bool = False
    no_english: bool = False
    notes: str | None = None


def _build_task(doi: str, link: str) -> str:
    return (
        f"Navigate to {link} and extract scholarly-article metadata from the landing "
        f"page. DOI is {doi}.\n\n"
        "Goals:\n"
        "1. Navigate to the URL. If redirected through Crossref/chooser, follow to the publisher page.\n"
        "2. Identify and extract:\n"
        "   - authors: list of authors exactly as shown on the page, with affiliations if present\n"
        "   - abstract: the verbatim abstract paragraph, or null if no abstract is shown\n"
        "   - pdf_url: an absolute URL to the article PDF if a link is present, else null\n"
        "3. Set flags if applicable:\n"
        "   - has_bot_check: true if the page shows a captcha / Cloudflare challenge / access-denied message\n"
        "   - resolves_to_pdf: true if the final page URL ends in .pdf\n"
        "   - broken_doi: true if the DOI resolver returned 404 / 'DOI not found'\n"
        "   - no_english: true if the primary content language is not English\n"
        "4. Put any short caveats in `notes` (e.g. 'paywall', 'partial metadata'), or null.\n\n"
        "When you have enough data, emit the final structured output. "
        "Do NOT linger clicking around — be efficient."
    )


@dataclass
class ChromeRow:
    no: int
    doi: str
    link: str
    extraction: dict[str, Any]
    step_count: int
    duration_s: float
    error: str | None


def extraction_to_gold_row(row: ChromeRow) -> dict[str, Any]:
    e = row.extraction or {}
    authors = e.get("authors") or []
    status = "FALSE" if (e.get("has_bot_check") or row.error) else "TRUE"
    return {
        "No": row.no,
        "DOI": row.doi,
        "Link": row.link,
        "Authors": json.dumps(authors, ensure_ascii=False) if authors else "",
        "Abstract": e.get("abstract") or "",
        "PDF URL": e.get("pdf_url") or "",
        "Status": status,
        "Notes": e.get("notes") or (row.error or ""),
        "Has Bot Check": str(bool(e.get("has_bot_check"))).upper() if e else "",
        "Resolves To PDF": str(bool(e.get("resolves_to_pdf"))).upper() if e else "",
        "broken_doi": str(bool(e.get("broken_doi"))).upper() if e else "",
        "no english": str(bool(e.get("no_english"))).upper() if e else "",
    }


async def extract_one(
    browser, llm, row: dict[str, str], model: str, max_steps: int = 20
) -> ChromeRow:
    from browser_use import Agent
    no = int(row["No"])
    doi = row["DOI"]
    link = row["Link"]
    start = time.monotonic()
    try:
        agent = Agent(
            task=_build_task(doi, link),
            llm=llm,
            browser=browser,
            output_model_schema=ExtractionOut,
            use_vision=False,  # text-only to cut cost + speed
            max_failures=3,
        )
        history = await agent.run(max_steps=max_steps)
        # Parse structured output from final result
        final = history.final_result()
        if isinstance(final, ExtractionOut):
            extraction_dict = final.model_dump()
        elif isinstance(final, dict):
            extraction_dict = final
        elif isinstance(final, str):
            try:
                extraction_dict = json.loads(final)
            except json.JSONDecodeError:
                extraction_dict = {}
        else:
            extraction_dict = {}
        return ChromeRow(
            no=no, doi=doi, link=link,
            extraction=extraction_dict,
            step_count=len(history.history),
            duration_s=time.monotonic() - start,
            error=None if extraction_dict else "no_structured_output",
        )
    except Exception as e:
        return ChromeRow(
            no=no, doi=doi, link=link, extraction={},
            step_count=0, duration_s=time.monotonic() - start,
            error=f"{type(e).__name__}: {e}",
        )


async def main_async(args) -> int:
    from browser_use import Agent, Browser
    from browser_use.llm import ChatAnthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in eval/.env")

    llm = ChatAnthropic(model=args.model, api_key=api_key, max_tokens=4096)
    browser = Browser(cdp_url=args.cdp_url)

    with open(args.input, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if args.limit:
        rows = rows[: args.limit]

    log.info("extracting %d DOIs via browser-use + %s (CDP: %s)",
             len(rows), args.model, args.cdp_url)
    results: list[ChromeRow] = []
    for row in rows:
        r = await extract_one(browser, llm, row, args.model)
        results.append(r)
        status = "err" if r.error else "ok"
        log.info("[%s] %s/%s %s  steps=%d  %.1fs",
                 status, r.no, len(rows), r.doi, r.step_count, r.duration_s)

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=GOLD_COLUMNS)
        writer.writeheader()
        for r in results:
            writer.writerow(extraction_to_gold_row(r))

    META_JSON.write_text(
        json.dumps({
            "rows": [asdict(r) for r in results],
            "totals": {
                "rows": len(results),
                "errors": sum(1 for r in results if r.error),
                "wall_seconds": round(sum(r.duration_s for r in results), 2),
                "total_steps": sum(r.step_count for r in results),
            },
            "model": args.model,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    errors = sum(1 for r in results if r.error)
    print(f"wrote {len(results)} rows, {errors} errors")
    print(f"  csv:  {OUTPUT_CSV}")
    print(f"  meta: {META_JSON}")
    return 0 if errors == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(INPUT_CSV))
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--cdp-url", default=CDP_URL)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(main())
