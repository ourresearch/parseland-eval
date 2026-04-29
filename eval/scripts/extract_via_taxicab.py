"""Extract gold-standard metadata from Taxicab-cached HTML.

Strategy (cheapest → most expensive, fall through on miss):

  1. **Citation meta tags** (deterministic, free): parse `citation_title`,
     `citation_author`, `citation_author_institution`, `citation_abstract`,
     `citation_pdf_url`. Most scholarly publishers ship these (Google Scholar
     convention). When complete, no LLM needed.

  2. **Claude API on full HTML** (fallback, ~$0.05–0.20/DOI): pass cleaned HTML
     to Claude Sonnet 4.6 with the v1.4-style extraction prompt + Pydantic
     schema. Used only when citation_* tags are absent or incomplete.

Why this exists: Taxicab pre-harvested HTML for DOIs that browser-use Cloud
gets 403/bot-checked at fetch time (verified 2026-04-29 against APS, Oxford,
ScienceDirect, Érudit, OJS — all 11 holdout-50 fetch-fails are in S3). We
already paid the harvest cost; we should not pay again to re-fetch.

Output schema mirrors v1.4 (matches `human-goldie.csv` / `diff_goldie.py`).
Output CSV slots into the existing comparison pipeline alongside
`runs/holdout-v1.4/ai-goldie-1.csv`.

CLI surface mirrors `extract_batch_cloud.py` for consistency.

Usage:
    python eval/scripts/extract_via_taxicab.py \\
        --source eval/goldie/holdout-50.csv \\
        --output-dir runs/holdout-v1.4-taxicab \\
        --prompt eval/prompts/ai-goldie-v1.4.md \\
        --concurrency 10
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import html as html_lib
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

# Reuse Taxicab client + extraction prompt loader from sibling code.
SCRIPT_DIR = Path(__file__).resolve().parent
EVAL_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(EVAL_DIR))
sys.path.insert(0, str(EVAL_DIR / "scripts"))

from parseland_eval.api import TAXICAB_BASE, resolve_harvest_uuid  # noqa: E402
from extract_batch_cloud import (  # noqa: E402
    ExtractionOut,
    GOLD_COLUMNS,
    load_prompt,
)

log = logging.getLogger("taxicab-extract")

DEFAULT_CONCURRENCY = 10
DEFAULT_MODEL = "claude-sonnet-4-5"  # cost-optimized default; Sonnet 4.6 wire id
DEFAULT_LLM_MAX_TOKENS = 4096
HTML_BUDGET_CHARS = 60_000  # Claude API input cap; head + meta + body excerpt


# ---- Taxicab HTML retrieval ------------------------------------------------

def fetch_html(doi: str) -> tuple[str | None, str | None, str | None]:
    """Resolve DOI -> harvest UUID -> download HTML.

    Returns (html_text, resolved_url, error). HTML is decoded; gzip handled.
    """
    uuid, _call = resolve_harvest_uuid(doi)
    if not uuid:
        return None, None, "taxicab: no harvested html"

    # Direct download. The harvester returns whatever Taxicab cached.
    download_url = f"{TAXICAB_BASE}/taxicab/{uuid}"
    try:
        resp = requests.get(download_url, timeout=30)
    except requests.RequestException as exc:
        return None, None, f"download error: {exc}"
    if resp.status_code != 200:
        return None, None, f"download status: {resp.status_code}"

    # Some records arrive gzipped (s3_path ends .html.gz), some don't.
    body = resp.content
    try:
        text = gzip.decompress(body).decode("utf-8", errors="replace")
    except (OSError, gzip.BadGzipFile):
        text = resp.text

    # Pull resolved_url from a fresh Taxicab call so we record where we got it.
    try:
        meta = requests.get(f"{TAXICAB_BASE}/taxicab/doi/{doi}", timeout=10).json()
        resolved = (meta.get("html") or [{}])[0].get("resolved_url")
    except Exception:
        resolved = None

    return text, resolved, None


# ---- Tier 1: citation_* meta tag extraction --------------------------------

_META_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _meta_re(name: str) -> re.Pattern[str]:
    cached = _META_RE_CACHE.get(name)
    if cached is not None:
        return cached
    # Tolerate name="..." or property="..."; ordering varies; case-insensitive.
    pat = re.compile(
        rf'<meta\b[^>]*?\b(?:name|property)\s*=\s*"{re.escape(name)}"[^>]*?\bcontent\s*=\s*"([^"]*)"',
        re.IGNORECASE,
    )
    _META_RE_CACHE[name] = pat
    return pat


def _all_meta(html: str, name: str) -> list[str]:
    return [html_lib.unescape(m) for m in _meta_re(name).findall(html)]


def _first_meta(html: str, *names: str) -> str:
    """First non-empty value across alternative meta names."""
    for n in names:
        vals = _all_meta(html, n)
        for v in vals:
            v = v.strip()
            if v:
                return v
    return ""


def _fix_encoding(s: str) -> str:
    """Fix common mojibake — page is utf-8 but parser saw latin-1."""
    if not s:
        return s
    if "Ã" in s or "â" in s:
        try:
            return s.encode("latin-1", errors="ignore").decode("utf-8", errors="replace")
        except Exception:
            return s
    return s


def extract_via_meta_tags(html: str, doi: str, link: str) -> dict[str, Any] | None:
    """Try to extract everything from citation_* meta tags. Returns None if not enough."""
    title = _fix_encoding(_first_meta(html, "citation_title", "DC.title", "og:title"))
    if not title:
        return None  # No Highwire tags — fall through.

    authors_raw = [_fix_encoding(a) for a in _all_meta(html, "citation_author")]
    affs_raw = [_fix_encoding(a) for a in _all_meta(html, "citation_author_institution")]
    abstract = _fix_encoding(_first_meta(
        html, "citation_abstract", "dc.description", "DC.description", "og:description"
    ))
    pdf_url = _first_meta(html, "citation_pdf_url", "citation_full_html_url")

    if not authors_raw:
        return None  # Highwire pages typically have authors; if missing, don't trust the tag set.

    # Pair authors and affiliations by index. citation_author_institution can repeat
    # per-affiliation (one author with two affils → two institution tags). Best-effort:
    # if affiliation count == author count, 1:1; if affiliation count > author count,
    # join extras onto the last; if shorter, leave trailing authors with "".
    authors = []
    if len(affs_raw) == len(authors_raw):
        pairs = list(zip(authors_raw, affs_raw))
    elif len(affs_raw) > len(authors_raw) and authors_raw:
        # Distribute affs evenly; trailing affs join onto last author.
        per_author = len(affs_raw) // len(authors_raw)
        pairs = []
        idx = 0
        for i, name in enumerate(authors_raw):
            if i == len(authors_raw) - 1:
                chunk = affs_raw[idx:]
            else:
                chunk = affs_raw[idx : idx + per_author]
                idx += per_author
            pairs.append((name, "; ".join(chunk)))
    else:
        pairs = []
        for i, name in enumerate(authors_raw):
            aff = affs_raw[i] if i < len(affs_raw) else ""
            pairs.append((name, aff))

    for i, (name, aff) in enumerate(pairs):
        authors.append({
            "name": name.strip(),
            "rasses": aff.strip(),
            "corresponding_author": (i == 0),  # heuristic — Highwire doesn't expose CA flag
        })

    return {
        "DOI": doi,
        "Link": link,
        "Authors": authors,
        "Abstract": abstract,
        "PDF URL": pdf_url,
    }


# ---- Tier 2: Claude API on cleaned HTML ------------------------------------

def _strip_for_llm(html: str, budget_chars: int = HTML_BUDGET_CHARS) -> str:
    """Trim scripts/styles, keep <head> + first chunk of <body>."""
    # Drop scripts/styles aggressively; they're noise to the LLM.
    cleaned = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<style\b[^>]*>.*?</style>", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<!--.*?-->", "", cleaned, flags=re.DOTALL)
    if len(cleaned) <= budget_chars:
        return cleaned
    # Keep the head + as much body as fits.
    head_m = re.search(r"<head\b[^>]*>(.*?)</head>", cleaned, re.IGNORECASE | re.DOTALL)
    head = head_m.group(0) if head_m else ""
    body_m = re.search(r"<body\b[^>]*>(.*?)</body>", cleaned, re.IGNORECASE | re.DOTALL)
    body = body_m.group(1) if body_m else cleaned[len(head):]
    remaining = budget_chars - len(head) - 200
    if remaining > 0:
        body = body[:remaining]
    return head + "\n<body>\n" + body + "\n</body>"


def extract_via_claude(
    html: str,
    doi: str,
    link: str,
    *,
    system_prompt: str,
    model: str,
    api_key: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Pass cleaned HTML to Claude with the v1.4-style extraction prompt.

    Returns (extraction_dict, usage_dict). extraction is shaped to match the
    `human-goldie.csv` row format used by `diff_goldie.py`.
    """
    try:
        import anthropic  # local import — only needed in the fallback path
    except ImportError:
        return None, {"error": "anthropic SDK not installed"}

    client = anthropic.Anthropic(api_key=api_key)
    cleaned = _strip_for_llm(html)
    user_msg = (
        f"DOI: {doi}\nLanding page (resolved): {link}\n\n"
        f"Below is the cached landing-page HTML. Extract scholarly metadata "
        f"per the rules in your system instructions and return ONLY a JSON "
        f"object matching this Pydantic schema:\n"
        f"```json\n{json.dumps(ExtractionOut.model_json_schema(), indent=2)}\n```\n\n"
        f"--- HTML ---\n{cleaned}"
    )

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=DEFAULT_LLM_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as exc:  # noqa: BLE001 — surface whatever
        return None, {"error": f"anthropic call failed: {exc}"}

    raw = resp.content[0].text.strip() if resp.content else ""
    json_text = raw
    if json_text.startswith("```"):
        # Strip ```json ... ``` fences.
        json_text = re.sub(r"^```(?:json)?\n", "", json_text)
        json_text = re.sub(r"\n```\s*$", "", json_text)

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        return None, {"error": f"json decode: {exc}", "raw": raw[:400]}

    usage = {
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }

    # Coerce to the shape diff_goldie.py expects.
    extraction = {
        "DOI": doi,
        "Link": link,
        "Authors": data.get("authors") or [],
        "Abstract": data.get("abstract") or "",
        "PDF URL": data.get("pdf_url") or "",
    }
    return extraction, usage


# ---- per-DOI orchestration -------------------------------------------------

@dataclass
class TaxicabResult:
    no: int
    doi: str
    link: str
    extraction: dict[str, Any] | None = None
    tier: str = ""  # "meta_tags" | "claude" | "failed"
    duration_s: float = 0.0
    cost_usd: float | None = None
    error: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    resolved_url: str | None = None


def _approx_cost(usage: dict[str, Any], model: str) -> float:
    """Rough Anthropic pricing — Sonnet 4.6 default."""
    if not usage or "input_tokens" not in usage:
        return 0.0
    if "opus" in model:
        in_rate, out_rate = 6.0, 30.0
    elif "haiku" in model:
        in_rate, out_rate = 0.9, 5.4
    else:
        in_rate, out_rate = 3.6, 18.0
    return (
        usage["input_tokens"] / 1_000_000 * in_rate
        + usage.get("output_tokens", 0) / 1_000_000 * out_rate
    )


def run_doi(
    no: int,
    doi: str,
    link: str,
    *,
    system_prompt: str,
    model: str,
    api_key: str | None,
    use_llm_fallback: bool = True,
    skip_meta_tags: bool = False,
) -> TaxicabResult:
    start = time.perf_counter()

    html, resolved_url, fetch_err = fetch_html(doi)
    if fetch_err or html is None:
        return TaxicabResult(
            no=no, doi=doi, link=link, tier="failed", error=fetch_err or "no html",
            duration_s=round(time.perf_counter() - start, 2),
        )

    # Tier 1: meta tags. Skipped when --skip-meta-tags is on (Casey apples-to-apples mode).
    meta_extraction = None if skip_meta_tags else extract_via_meta_tags(html, doi, link)
    if meta_extraction is not None:
        return TaxicabResult(
            no=no, doi=doi, link=link, extraction=meta_extraction,
            tier="meta_tags", cost_usd=0.0, resolved_url=resolved_url,
            duration_s=round(time.perf_counter() - start, 2),
        )

    # Tier 2: Claude on full HTML.
    if not use_llm_fallback or not api_key:
        return TaxicabResult(
            no=no, doi=doi, link=link, tier="failed",
            error="no meta tags + LLM fallback disabled or missing key",
            duration_s=round(time.perf_counter() - start, 2),
            resolved_url=resolved_url,
        )

    extraction, usage = extract_via_claude(
        html, doi, link, system_prompt=system_prompt, model=model, api_key=api_key,
    )
    if extraction is None:
        return TaxicabResult(
            no=no, doi=doi, link=link, tier="failed",
            error=(usage or {}).get("error", "claude call failed"),
            duration_s=round(time.perf_counter() - start, 2),
            usage=usage or {},
            resolved_url=resolved_url,
        )

    return TaxicabResult(
        no=no, doi=doi, link=link, extraction=extraction, tier="claude",
        cost_usd=round(_approx_cost(usage or {}, model), 4),
        usage=usage or {},
        resolved_url=resolved_url,
        duration_s=round(time.perf_counter() - start, 2),
    )


# ---- CSV emit --------------------------------------------------------------

def to_gold_row(result: TaxicabResult) -> dict[str, Any]:
    """Match the GOLD_COLUMNS schema used by extract_batch_cloud + diff_goldie."""
    extraction = result.extraction or {}
    authors = extraction.get("Authors") or []
    return {
        "No": result.no,
        "DOI": result.doi,
        "Link": result.link,
        "Authors": json.dumps(authors, ensure_ascii=False),
        "Abstract": extraction.get("Abstract") or "",
        "PDF URL": extraction.get("PDF URL") or "",
        "Status": "TRUE" if extraction else "FALSE",
        "Notes": "" if extraction else (result.error or ""),
        "Has Bot Check": "FALSE",
        "Resolves To PDF": "FALSE",
        "broken_doi": "FALSE",
        "no english": "FALSE",
    }


# ---- main ------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True,
                        help="Holdout/train CSV with No, DOI, Link columns.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True,
                        help="Prompt .md (system prompt body extracted).")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--no-llm-fallback", dest="llm_fallback",
                        action="store_false", default=True)
    parser.add_argument("--skip-meta-tags", action="store_true",
                        help="Force every DOI through Claude (Casey's apples-to-apples test).")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_csv = args.output_dir / "ai-goldie-1.csv"
    log_jsonl = args.output_dir / "ai-goldie-1.tier-log.jsonl"

    version, system_prompt = load_prompt(args.prompt)
    log.info("prompt %s (version=%s, %d chars)", args.prompt, version, len(system_prompt))

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if args.llm_fallback and not api_key:
        log.warning("ANTHROPIC_API_KEY not set; LLM fallback unavailable.")

    rows: list[dict[str, str]] = []
    with args.source.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            try:
                n = int(r.get("No") or 0)
            except ValueError:
                continue
            doi = (r.get("DOI") or "").strip()
            link = (r.get("Link") or "").strip() or f"https://doi.org/{doi}"
            if doi:
                rows.append({"No": str(n), "DOI": doi, "Link": link})
    log.info("rows to process: %d", len(rows))

    results: list[TaxicabResult] = []

    async def runner():
        sem = asyncio.Semaphore(args.concurrency)

        async def one(row):
            async with sem:
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(
                    None,
                    lambda: run_doi(
                        int(row["No"]), row["DOI"], row["Link"],
                        system_prompt=system_prompt,
                        model=args.model,
                        api_key=api_key,
                        use_llm_fallback=args.llm_fallback,
                        skip_meta_tags=args.skip_meta_tags,
                    ),
                )

        coros = [one(r) for r in rows]
        for done in asyncio.as_completed(coros):
            res = await done
            results.append(res)
            log.info(
                "[%d/%d] No=%d %s tier=%s %.1fs %s",
                len(results), len(rows), res.no, res.doi, res.tier,
                res.duration_s,
                f"cost=${res.cost_usd:.3f}" if res.cost_usd else "(free)",
            )
            with log_jsonl.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "no": res.no, "doi": res.doi, "tier": res.tier,
                    "duration_s": res.duration_s, "cost_usd": res.cost_usd,
                    "error": res.error, "usage": res.usage,
                    "resolved_url": res.resolved_url,
                }) + "\n")

    asyncio.run(runner())

    # Write final CSV in `No` order.
    results.sort(key=lambda r: r.no)
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GOLD_COLUMNS)
        w.writeheader()
        for r in results:
            w.writerow(to_gold_row(r))

    n_meta = sum(1 for r in results if r.tier == "meta_tags")
    n_claude = sum(1 for r in results if r.tier == "claude")
    n_failed = sum(1 for r in results if r.tier == "failed")
    total_cost = sum(r.cost_usd or 0.0 for r in results)

    log.info(
        "DONE — meta-tag: %d / claude: %d / failed: %d / total cost: $%.2f",
        n_meta, n_claude, n_failed, total_cost,
    )
    log.info("CSV: %s", out_csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
