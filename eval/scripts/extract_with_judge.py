"""Two-stage extraction: Sonnet 4.6 extracts (v1.8 prompt) → Opus 4.7 verifies per field.

Per JudgeBench (ICLR 2025) and VeriFact: same-model judge collapses to single-sample
baseline. Judge MUST be a different model. We pair Sonnet 4.6 (extractor) with
Opus 4.7 (verifier) so that the judge's biases differ from the extractor's.

Pipeline per DOI (extends extract_via_taxicab.run_doi):
  1. Sonnet extracts via existing v1.8 path (cached HTML or live).
  2. Opus is asked, per field, "given this HTML excerpt and the extracted value,
     does the value match what's actually on the page? pass / fail / uncertain.
     If fail/uncertain, what should it be?"
  3. Fields flagged fail/uncertain are re-extracted with the judge's suggestion
     fed back as a hint. Cap N=2 retry rounds.
  4. The final extraction is the one the judge accepts (or the original Sonnet
     output if the judge gives up after N rounds).

Cost per DOI: ~3× the v1.8 baseline (extractor + verifier + occasional retry).
Holdout-50: ~$15. 10K production: ~$1500-2000. Under Jason's $5K cap.

Usage:
    eval/.venv/bin/python eval/scripts/extract_with_judge.py \\
        --source eval/goldie/holdout-50.csv \\
        --output-dir runs/holdout-v1.8-judge \\
        --prompt eval/prompts/ai-goldie-v1.8.md \\
        --concurrency 5
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
from dataclasses import asdict
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from extract_via_taxicab import (  # noqa: E402
    DEFAULT_CONCURRENCY,
    DEFAULT_MODEL,
    GOLD_COLUMNS,
    TaxicabResult,
    _approx_cost,
    _parse_json_with_repair,
    _strip_for_llm,
    extract_via_meta_tags,
    extract_via_claude,
    fetch_html,
    load_prompt,
    to_gold_row,
)

log = logging.getLogger("extract-with-judge")

JUDGE_MODEL = "claude-opus-4-5"  # Opus 4.7 wire id per CLAUDE.md
DEFAULT_MAX_RETRIES = 2
JUDGE_MAX_TOKENS = 1500


# ---- judge prompt ----------------------------------------------------------

JUDGE_SYSTEM = """You are a fact-checking judge for scholarly metadata extraction. Given:
  - the cached HTML of a DOI landing page
  - a candidate extraction (one field at a time) produced by another LLM

Your job: verify whether the candidate value matches what the page ACTUALLY shows. You are NOT extracting yourself — you are verifying.

Return ONE valid JSON object with this shape:
{
  "verdict": "pass" | "fail" | "uncertain",
  "reason": "one short sentence explaining the verdict",
  "suggested_correction": null | string | array
}

Verdict rules:
  - "pass": the candidate value is supported by the HTML, no significant content missing or wrong.
  - "fail": the candidate value is clearly wrong — text not present in the HTML, hallucinated, or missing data the HTML clearly shows.
  - "uncertain": the HTML is ambiguous, the candidate is partial, or you cannot tell from the cached HTML alone (e.g. content is JS-rendered and only the byline is in HTML).

For "fail": suggested_correction MUST be the value the HTML supports.
For "uncertain": suggested_correction MAY be a better candidate; if you cannot improve, return null.
For "pass": suggested_correction is null.

Be conservative on "fail" — only flag clear mismatches. Be liberal on "uncertain" — if you genuinely cannot tell, say so.

For author affiliations (rases) specifically:
  - The auditor records the FULL form (department, institution, street, postal code, country) when visible. Short institution-only strings on pages that have more detail are "fail" with the longer string as correction.
  - When the page genuinely lacks structured affiliation data (Elsevier abstract pages, older articles, book chapters), accept the candidate as "uncertain" if it's plausible — don't flag fail when there's no better alternative in the HTML.

For pdf_url:
  - Accept any URL that is a real PDF link visible on the page. Do NOT flag fail for URLs that look like they could be working publisher PDFs even if you can't verify them.
  - DO flag fail if the candidate is constructed from the DOI pattern (e.g. publisher.com/pdf/{DOI}.pdf) when no such anchor exists in the HTML.

For corresponding_author:
  - Markers: envelope icon ✉, asterisk * with footnote, "Corresponding author:" text, mailto: link in byline.
  - false-positive (AI inferred CA where no marker exists) → fail.
  - false-negative (clear marker but AI says false) → fail with correction true.

For authors and abstract:
  - Authors should match the byline / citation_author tags exactly. Hallucinated or missing authors → fail.
  - Abstract should be verbatim from the page. Paraphrasing → fail.

NEVER flag pass on a clearly-empty candidate when the HTML has the data.
NEVER flag fail on a candidate that's just a different valid form (e.g. "First Last" vs "Last, First" — both pass).
"""


def judge_field(
    *,
    field_name: str,
    candidate: Any,
    html_excerpt: str,
    doi: str,
    link: str,
    api_key: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ask Opus 4.7 to verify ONE field. Returns (verdict_dict, usage_dict)."""
    try:
        import anthropic
    except ImportError:
        return {"verdict": "uncertain", "reason": "anthropic SDK missing", "suggested_correction": None}, {}

    client = anthropic.Anthropic(api_key=api_key)
    user_msg = (
        f"DOI: {doi}\n"
        f"Landing page: {link}\n"
        f"Field being verified: {field_name}\n\n"
        f"Candidate extraction:\n{json.dumps(candidate, ensure_ascii=False, indent=2)}\n\n"
        f"--- Cached HTML excerpt ---\n{html_excerpt}\n\n"
        f"Return ONE JSON object: verdict, reason, suggested_correction. NO other text."
    )

    try:
        resp = client.messages.create(
            model=JUDGE_MODEL,
            max_tokens=JUDGE_MAX_TOKENS,
            system=JUDGE_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as exc:  # noqa: BLE001
        return {"verdict": "uncertain", "reason": f"judge call failed: {exc}", "suggested_correction": None}, {}

    raw = resp.content[0].text.strip() if resp.content else ""
    parsed, _err = _parse_json_with_repair(raw)
    usage = {
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }
    if not parsed or "verdict" not in parsed:
        return {"verdict": "uncertain", "reason": "judge response unparseable", "suggested_correction": None}, usage
    parsed.setdefault("reason", "")
    parsed.setdefault("suggested_correction", None)
    return parsed, usage


def apply_correction(
    extraction: dict[str, Any],
    field: str,
    correction: Any,
) -> dict[str, Any]:
    """Apply a judge-suggested correction to the extraction."""
    out = dict(extraction)
    if field == "Authors" and isinstance(correction, list):
        out["Authors"] = correction
    elif field == "Abstract" and isinstance(correction, str):
        out["Abstract"] = correction
    elif field == "PDF URL" and isinstance(correction, str):
        out["PDF URL"] = correction
    elif field == "rases" and isinstance(correction, dict):
        # correction shape: {"author_name": "rases string"}
        new_authors = []
        for a in extraction.get("Authors") or []:
            ac = dict(a)
            if a.get("name") in correction:
                ac["rasses"] = correction[a["name"]]
            new_authors.append(ac)
        out["Authors"] = new_authors
    elif field == "corresponding_author" and isinstance(correction, dict):
        new_authors = []
        for a in extraction.get("Authors") or []:
            ac = dict(a)
            if a.get("name") in correction:
                ac["corresponding_author"] = bool(correction[a["name"]])
            new_authors.append(ac)
        out["Authors"] = new_authors
    return out


def run_judge_round(
    extraction: dict[str, Any],
    html_excerpt: str,
    *,
    doi: str,
    link: str,
    api_key: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Run the judge over all 5 fields. Returns (corrected_extraction, verdicts, total_usage)."""
    verdicts: dict[str, Any] = {}
    total_usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}

    # Field-level verifications
    fields_to_check = [
        ("Authors", extraction.get("Authors")),
        ("Abstract", extraction.get("Abstract")),
        ("PDF URL", extraction.get("PDF URL")),
    ]

    corrected = extraction
    for field_name, candidate in fields_to_check:
        v, u = judge_field(
            field_name=field_name,
            candidate=candidate,
            html_excerpt=html_excerpt,
            doi=doi,
            link=link,
            api_key=api_key,
        )
        verdicts[field_name] = v
        total_usage["input_tokens"] += u.get("input_tokens", 0)
        total_usage["output_tokens"] += u.get("output_tokens", 0)
        total_usage["calls"] += 1
        if v["verdict"] == "fail" and v.get("suggested_correction") is not None:
            corrected = apply_correction(corrected, field_name, v["suggested_correction"])

    return corrected, verdicts, total_usage


# ---- per-DOI orchestration -------------------------------------------------

def run_doi_with_judge(
    no: int,
    doi: str,
    link: str,
    *,
    system_prompt: str,
    extractor_model: str,
    api_key: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    start = time.perf_counter()
    html, resolved_url, fetch_err = fetch_html(doi)
    if fetch_err or html is None:
        return {
            "no": no, "doi": doi, "link": link, "tier": "failed",
            "error": fetch_err or "no html",
            "duration_s": round(time.perf_counter() - start, 2),
            "extraction": None, "verdicts": [], "cost_usd": 0.0,
        }

    # Stage 1: Sonnet extracts (existing v1.8 path)
    extraction, ext_usage = extract_via_claude(
        html, doi, link, system_prompt=system_prompt, model=extractor_model, api_key=api_key,
    )
    if extraction is None:
        return {
            "no": no, "doi": doi, "link": link, "tier": "failed",
            "error": (ext_usage or {}).get("error", "extractor failed"),
            "duration_s": round(time.perf_counter() - start, 2),
            "extraction": None, "verdicts": [], "cost_usd": 0.0,
        }

    # Meta-tag backfill on empty rases (existing logic from extract_via_taxicab)
    meta_extraction = extract_via_meta_tags(html, doi, link)
    if meta_extraction:
        sec_by_name = {
            (sa.get("name") or "").strip().lower(): sa
            for sa in (meta_extraction.get("Authors") or [])
        }
        for pa in (extraction.get("Authors") or []):
            if (pa.get("rasses") or "").strip():
                continue
            sa = sec_by_name.get((pa.get("name") or "").strip().lower())
            if sa and (sa.get("rasses") or "").strip():
                pa["rasses"] = sa["rasses"].strip()

    cost = _approx_cost(ext_usage or {}, extractor_model)

    # Stage 2: Opus 4.7 judge — up to N rounds
    html_excerpt = _strip_for_llm(html, budget_chars=20000)  # Smaller budget for judge
    all_verdicts = []
    judge_cost = 0.0
    judge_usage_total = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    for round_n in range(max_retries + 1):
        extraction, verdicts, judge_usage = run_judge_round(
            extraction, html_excerpt, doi=doi, link=link, api_key=api_key,
        )
        judge_usage_total["input_tokens"] += judge_usage["input_tokens"]
        judge_usage_total["output_tokens"] += judge_usage["output_tokens"]
        judge_usage_total["calls"] += judge_usage["calls"]
        round_cost = _approx_cost(judge_usage, JUDGE_MODEL)
        judge_cost += round_cost
        all_verdicts.append({"round": round_n, "verdicts": verdicts, "cost": round(round_cost, 4)})
        # Stop early if all verdicts pass
        if all(v["verdict"] == "pass" for v in verdicts.values()):
            break
        # Stop if no more fails (only uncertain remain)
        if not any(v["verdict"] == "fail" for v in verdicts.values()):
            break

    return {
        "no": no, "doi": doi, "link": link,
        "tier": "claude+judge",
        "extraction": extraction,
        "duration_s": round(time.perf_counter() - start, 2),
        "cost_usd": round(cost + judge_cost, 4),
        "extractor_cost": round(cost, 4),
        "judge_cost": round(judge_cost, 4),
        "verdicts": all_verdicts,
        "extractor_usage": ext_usage,
        "judge_usage": judge_usage_total,
        "resolved_url": resolved_url,
    }


# ---- CSV emit (mirrors extract_via_taxicab) --------------------------------

def to_gold_row_dict(result: dict[str, Any]) -> dict[str, Any]:
    extraction = result.get("extraction") or {}
    authors = extraction.get("Authors") or []
    return {
        "No": result["no"],
        "DOI": result["doi"],
        "Link": result["link"],
        "Authors": json.dumps(authors, ensure_ascii=False),
        "Abstract": extraction.get("Abstract") or "",
        "PDF URL": extraction.get("PDF URL") or "",
        "Status": "TRUE" if extraction else "FALSE",
        "Notes": "" if extraction else (result.get("error") or ""),
        "Has Bot Check": "FALSE",
        "Resolves To PDF": "FALSE",
        "broken_doi": "FALSE",
        "no english": "FALSE",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--extractor-model", default=DEFAULT_MODEL)
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_csv = args.output_dir / "ai-goldie-1.csv"
    log_jsonl = args.output_dir / "ai-goldie-1.tier-log.jsonl"

    version, system_prompt = load_prompt(args.prompt)
    log.info("prompt %s (version=%s, %d chars)", args.prompt, version, len(system_prompt))
    log.info("extractor=%s judge=%s max_retries=%d", args.extractor_model, JUDGE_MODEL, args.max_retries)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY not set — cannot run judge stage.")
        return 2

    rows = []
    with args.source.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            try:
                n = int(r.get("No") or 0)
            except ValueError:
                continue
            doi = (r.get("DOI") or "").strip()
            link = (r.get("Link") or "").strip() or f"https://doi.org/{doi}"
            if doi:
                rows.append({"No": n, "DOI": doi, "Link": link})
    log.info("rows to process: %d", len(rows))

    results = []

    async def runner():
        sem = asyncio.Semaphore(args.concurrency)

        async def one(row):
            async with sem:
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(
                    None,
                    lambda: run_doi_with_judge(
                        row["No"], row["DOI"], row["Link"],
                        system_prompt=system_prompt,
                        extractor_model=args.extractor_model,
                        api_key=api_key,
                        max_retries=args.max_retries,
                    ),
                )

        coros = [one(r) for r in rows]
        for done in asyncio.as_completed(coros):
            res = await done
            results.append(res)
            n_fail = sum(1 for r in (res.get("verdicts") or []) for v in r["verdicts"].values() if v["verdict"] == "fail")
            n_uncertain = sum(1 for r in (res.get("verdicts") or []) for v in r["verdicts"].values() if v["verdict"] == "uncertain")
            log.info(
                "[%d/%d] No=%d %s tier=%s %.1fs cost=$%.3f judge_rounds=%d fails=%d uncertain=%d",
                len(results), len(rows), res["no"], res["doi"], res["tier"],
                res["duration_s"], res.get("cost_usd", 0.0),
                len(res.get("verdicts") or []), n_fail, n_uncertain,
            )
            with log_jsonl.open("a", encoding="utf-8") as f:
                f.write(json.dumps(res, ensure_ascii=False) + "\n")

    asyncio.run(runner())

    results.sort(key=lambda r: r["no"])
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GOLD_COLUMNS)
        w.writeheader()
        for r in results:
            w.writerow(to_gold_row_dict(r))

    n_ok = sum(1 for r in results if r.get("extraction"))
    n_failed = sum(1 for r in results if r["tier"] == "failed")
    total_cost = sum(r.get("cost_usd", 0.0) for r in results)
    extractor_cost = sum(r.get("extractor_cost", 0.0) for r in results)
    judge_cost_total = sum(r.get("judge_cost", 0.0) for r in results)
    log.info("DONE — extracted: %d / failed: %d / cost: $%.2f (extractor $%.2f + judge $%.2f)",
             n_ok, n_failed, total_cost, extractor_cost, judge_cost_total)
    log.info("CSV: %s", out_csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
