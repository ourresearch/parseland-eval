"""Live-fetch tier for parseland-eval — targets DOIs where the Taxicab cached
HTML is content-empty (bot-blocked, JS-rendered, or paywalled-stub) and the
gold says the field IS present on the page.

Strategy: for each target DOI, drive a real Chrome (via CDP) with browser-use
Agent. The Agent navigates to the publisher URL, executes the page's JS,
and extracts the same fields the v1.8 prompt asks for. Output is a *delta*
CSV that can be merged with the v1.8 baseline before scoring.

Inputs:
  --targets <path-to-json>   list of {doi, link, reason}
  --prompt   <path>          v1.8 prompt body (system instructions)
  --output   <path>          delta CSV in gold-standard.json shape

Pre-req: Chrome running with --remote-debugging-port=9222.
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
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR / "scripts"))

DEFAULT_CDP = "http://localhost:9222"
DEFAULT_MODEL = "claude-sonnet-4-5"  # browser-use's id for Sonnet 4.6
DEFAULT_MAX_STEPS = 18

log = logging.getLogger("live-fetch")


class AuthorOut(BaseModel):
    name: str
    rasses: str | None = ""
    corresponding_author: bool = False


class ExtractionOut(BaseModel):
    title: str | None = None
    authors: list[AuthorOut] = Field(default_factory=list)
    abstract: str | None = None
    pdf_url: str | None = None
    has_bot_check: bool = False
    resolves_to_pdf: bool = False
    broken_doi: bool = False
    no_english: bool = False
    notes: str | None = None


def _strip_yaml_front_matter(prompt_text: str) -> str:
    """Drop YAML front matter and grab the System prompt block."""
    lines = prompt_text.splitlines()
    if lines and lines[0] == "---":
        end = next((i for i in range(1, len(lines)) if lines[i] == "---"), 0)
        lines = lines[end + 1:]
    text = "\n".join(lines)
    if "## System prompt" in text and "```" in text:
        block = text.split("## System prompt", 1)[1]
        block = block.split("```", 2)
        if len(block) >= 2:
            return block[1].strip()
    return text


def _build_task(system_prompt_body: str, doi: str, link: str) -> str:
    return (
        f"{system_prompt_body}\n\n"
        f"---\n\n"
        f"DOI: {doi}\n"
        f"URL: {link}\n\n"
        f"Open the URL, wait for the page to render fully, expand any "
        f"'Author Info' / 'Affiliations' / 'Acknowledgements' sections, "
        f"then return the structured extraction. Hard cap ~10 navigation steps."
    )


async def fetch_one(browser, llm, target: dict, system_prompt_body: str,
                    max_steps: int) -> dict:
    from browser_use import Agent

    doi = target["doi"]
    link = target["link"] or f"https://doi.org/{doi}"
    start = time.monotonic()
    try:
        agent = Agent(
            task=_build_task(system_prompt_body, doi, link),
            llm=llm,
            browser=browser,
            output_model_schema=ExtractionOut,
            use_vision=False,
            max_failures=2,
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
        return {
            "doi": doi,
            "extraction": extraction,
            "steps": len(history.history),
            "duration_s": round(time.monotonic() - start, 2),
            "error": None if extraction else "no_structured_output",
        }
    except Exception as e:
        return {
            "doi": doi,
            "extraction": {},
            "steps": 0,
            "duration_s": round(time.monotonic() - start, 2),
            "error": f"{type(e).__name__}: {e}",
        }


def _normalize_authors(authors: list[Any]) -> list[dict]:
    out = []
    for a in authors:
        if not isinstance(a, dict):
            continue
        name = str(a.get("name") or "").strip()
        if not name:
            continue
        rasses = a.get("rasses")
        if isinstance(rasses, list):
            rasses = " | ".join(s for s in (str(x or "").strip() for x in rasses) if s)
        elif rasses is None:
            rasses = ""
        else:
            rasses = str(rasses).strip()
        ca = a.get("corresponding_author")
        out.append({
            "name": name,
            "rasses": rasses,
            "corresponding_author": bool(ca) if ca is not None else False,
        })
    return out


def _to_csv_row(doi: str, link: str, extraction: dict, error: str | None) -> dict:
    e = extraction or {}
    authors = _normalize_authors(e.get("authors") or [])
    return {
        "No": "",
        "DOI": doi,
        "Link": link,
        "Authors": json.dumps(authors, ensure_ascii=False),
        "Abstract": e.get("abstract") or "",
        "PDF URL": e.get("pdf_url") or "",
        "Status": "True" if (not e.get("has_bot_check") and not error) else "False",
        "Notes": e.get("notes") or (error or ""),
        "Has Bot Check": "True" if e.get("has_bot_check") else "False",
        "Resolves To PDF": "True" if e.get("resolves_to_pdf") else "False",
        "broken_doi": "True" if e.get("broken_doi") else "False",
        "no english": "True" if e.get("no_english") else "False",
    }


async def main_async(args) -> int:
    from browser_use import Browser
    from browser_use.llm import ChatAnthropic

    try:
        from dotenv import load_dotenv
        load_dotenv(EVAL_DIR / ".env", override=True)
    except ImportError:
        pass

    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.error("ANTHROPIC_API_KEY not set")
        return 2

    targets = json.loads(Path(args.targets).read_text())
    if args.limit:
        targets = targets[:args.limit]
    log.info("targets: %d", len(targets))

    prompt_body = _strip_yaml_front_matter(Path(args.prompt).read_text())

    browser = Browser(cdp_url=args.cdp_url)
    llm = ChatAnthropic(model=args.model)

    results = []
    sem = asyncio.Semaphore(args.concurrency)

    async def one(t):
        async with sem:
            log.info("[fetch] %s reason=%s", t["doi"], t.get("reason", ""))
            r = await fetch_one(browser, llm, t, prompt_body, args.max_steps)
            log.info("  done %.1fs steps=%d %s",
                     r["duration_s"], r["steps"], r.get("error") or "ok")
            return r

    tasks = [one(t) for t in targets]
    for coro in asyncio.as_completed(tasks):
        r = await coro
        results.append(r)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["No", "DOI", "Link", "Authors", "Abstract", "PDF URL",
                  "Status", "Notes", "Has Bot Check", "Resolves To PDF",
                  "broken_doi", "no english"]
    target_by_doi = {t["doi"]: t for t in targets}
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in results:
            link = target_by_doi.get(r["doi"], {}).get("link", "")
            w.writerow(_to_csv_row(r["doi"], link, r["extraction"], r["error"]))

    meta_path = out.with_suffix(".meta.json")
    meta_path.write_text(json.dumps({
        "n_targets": len(targets),
        "results": [{k: v for k, v in r.items() if k != "extraction"} for r in results],
    }, indent=2))
    log.info("wrote %s + %s", out, meta_path)
    return 0


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--cdp-url", default=DEFAULT_CDP)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
