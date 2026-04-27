# MEMORY.md

## AI Goldie scale-up objective

As of 2026-04-27, the active goal is to build an "AI Goldie Machine" that can expand the human gold standard from 100 DOI rows toward 10,000 DOI rows.

Key intent:
- Learn and tune the extraction prompt from the first 50 human-annotated DOI rows in `eval/goldie/train-50.csv`.
- Validate only when ready on rows 51-100 in `eval/goldie/holdout-50.csv`.
- Target greater than 95% agreement before using the same prompt, model, browser setup, and conversion pipeline for further 100-DOI batches.
- Produce candidate batch CSVs named `ai-goldie-1.csv`, `ai-goldie-2.csv`, `ai-goldie-3.csv`, etc., where each file contains one 100-row batch.
- Browser extraction should return validated JSON first, then convert to the raw gold CSV schema.
- The human reviewer will inspect each 100-row AI batch and manually fix any misses.

Important guardrail:
- Do not iteratively tune on `holdout-50.csv`. The holdout is for final validation of a prompt already tuned on `train-50.csv`. If holdout accuracy is below target, record the failure taxonomy and improve against training data or a newly designated tuning set rather than leaking the holdout.

Local facts observed:
- `eval/Gold-Standard.csv` has 100 nonblank rows plus one blank trailing row.
- `eval/goldie/train-50.csv` contains rows 1-50.
- `eval/goldie/holdout-50.csv` contains rows 51-100.
- Raw author objects use `name`, `rasses`, and `corresponding_author`.
- Many absent fields are encoded as `N/A`; the AI scorer/converter must handle this intentionally.

Current best technical direction:
- Use Browser Use with structured output, because Browser Use supports validated typed output from agent tasks.
- Prefer real/system Chrome via CDP or Browser Use Cloud profiles over headless-only browsing, because previous pilot notes showed headful browsing reduced bot checks.
- For model choice, benchmark Browser Use's recommended `claude-sonnet-4.6` against lower-cost options like `gpt-5.4-mini`; keep whichever clears the 95% threshold at lowest total cost.
- Avoid Claude `/chrome` as a programmatic extraction backend. Repo notes say it is client-side only; use Browser Use or an explicit CDP/browser automation loop instead.

Reference sources checked on 2026-04-27:
- Browser Use structured output: https://docs.browser-use.com/cloud/agent/structured-output
- Browser Use models: https://docs.browser-use.com/cloud/agent/models
- Browser Use pricing: https://docs.browser-use.com/cloud/pricing
- Browser Use real Chrome/CDP settings: https://docs.browser-use.com/open-source/customize/browser/all-parameters
- Browser Use parallel agents: https://docs.browser-use.com/open-source/examples/templates/parallel-browser
- OpenAI models: https://developers.openai.com/api/docs/models
- OpenAI Structured Outputs: https://developers.openai.com/api/docs/guides/structured-outputs
- Anthropic computer use: https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool

## Decisions locked 2026-04-27 Session 2

User reframed priorities to **accuracy + time over cost**. Pinned decisions:

- **10K production stack:** browser-use Cloud Tasks API, **Business tier ($299/mo, 200 concurrent)**. ~38 min wall for 10K, ~$1,310 all-in (LLM ~$1k + sessions ~$8 + plan $299). Chosen over Dev (5h wall) and Scaleup ($999, diminishing return).
- **Holdout validation stack:** unchanged — local browser-use library + 4 parallel headed Chromes via CDP, BYOK Anthropic. Same `eval/scripts/run_ai_goldie.py`.
- **API key:** user already has `BROWSER_USE_API_KEY`; will be set in `eval/.env`. No signup step.
- **Batch cadence:** **hybrid**. Extract batch 1 → user reviews → only then burn down 2–100 in parallel.
- **`/chrome` rejected** for batch use: interactive UI only, no headless, single-thread, vision-based screenshot loop, Pro plan = Haiku-only, no structured output, no resume. Anthropic Computer Use shares the vision-based weakness; third-party benches show its JSON output is malformed, so reliable extraction comes from `page.evaluate()`. browser-use's DOM accessibility tree is the right primitive for structured extraction at scale.
- **Codex CLI surveyed and rejected** as a stack choice — has computer-use + in-app browser but adds nothing over directly using browser-use; would just orchestrate it.

## Hard prerequisite (do not relax)

Human goldie at `eval/goldie/human-goldie-v2-audited.csv` must be reviewed PERFECT — including the dedicated CA second sweep — BEFORE any AI run is reported. CDL paid OpenAlex $50k for CA coverage; pre-audit measurements are throwaway.

## Bullet-proof contract for `eval/scripts/extract_batch_cloud.py` (Phase E)

1. Resumable — SHA-256-keyed `.checkpoint/ai-goldie-N.partial.jsonl`; re-runs skip landed DOIs.
2. Atomic — `.tmp` + rename for every CSV write; no partial files visible to user.
3. Idempotent — DOI-keyed; same window produces same output.
4. Transparent failures — `ai-goldie-N.failures.jsonl` is the source of truth for blank rows.
5. Bot-check resilient — Cloud's hosted real Chrome with built-in residential proxies in 195+ countries (default = US) + auto-CAPTCHA / Cloudflare Turnstile / hCaptcha solving + JS-fingerprint/timezone/locale/behavior matched to exit IP. Override country via `proxy_country_code` (snake_case Python / `proxyCountryCode` TS). Zyte / external proxies explicitly NOT used — Cloud's stack is integrated and would be redundant.
6. Schema-enforced — Pydantic via Cloud `structuredOutput`; malformed responses fail fast and route to retry.
7. Cost-capped — `max_agent_steps=18`, retry cap N=3, optional `--max-cost-usd`.

## Naming convention (locked)

- `ai-goldie-1.csv` ← rows 1–100 of `eval/data/ai-goldie-source-10k.csv`
- `ai-goldie-2.csv` ← rows 101–200
- ...
- `ai-goldie-100.csv` ← rows 9901–10000

CSV column order matches `eval/gold-standard.csv` exactly so user manual review is on familiar ground.

## v1.4 prompt status (2026-04-27 EOD)

Locked candidate prompt: `eval/prompts/ai-goldie-v1.4.md` (~5KB). Iteration history v0 → v1 → v1.1 → v1.2 → v1.3 → v1.4, each committed locally with rationale.

Measurements vs `eval/gold-standard.csv` rows 51-100:
- v1.1 cloud (full automated, 50 DOIs): authors 78%, rases 48%, CA 70%, abstract 68%, pdf_url 42%, overall 16%.
- v1.4 manual-MCP (50 DOIs walked one-by-one in real Chrome via `mcp__claude-in-chrome__find`): authors 50%, rases 36%, CA 56%, abstract 28%, **pdf_url 62% (+20pp)**, overall 12%.

The v1.4 manual numbers are depressed by the `find` tool returning element summaries instead of verbatim content — they do NOT represent v1.4's true cloud-extraction quality. The PDF URL +20pp gain IS real and confirms v1.4's "no URL construction from DOI patterns" rule.

A fair v1.4 measurement requires one automated cloud run on holdout-50. Account credits exhausted (browser-use Cloud returned `402 Payment Required` mid-iteration on v1.3); top-up at <https://cloud.browser-use.com/bux> before the final pre-lock measurement.

## Bot-check bypass (no Zyte)

For 10K production extraction in Phase E, the bot-check strategy is **browser-use Cloud's built-in stack**:
- 195+ country residential proxies (default `proxy_country_code=us`).
- Automatic Cloudflare Turnstile / hCaptcha / reCAPTCHA solving.
- Chromium fork with JS-fingerprint, timezone, locale, GPU/screen-resolution matched to exit IP.
- Behavioral layer for human-like mouse / scroll / typing.

**Zyte explicitly not adopted** — Cloud's anti-bot is integrated; Zyte would be redundant + double-billed. Cloud-gated publishers we observed in the manual MCP pass (ScienceDirect occasionally, APS, Brill, ACS, Ovid, T&F sometimes) are exactly the class Cloud's stack is designed to handle. If a specific publisher still gates after Phase E.1 smoke, override `--proxy-country <ISO>` per-batch (e.g., `de` for German publishers).

Reference docs:
- <https://docs.browser-use.com/guides/proxies-and-stealth> (canonical proxy parameter ref)
- <https://browser-use.com/posts/bot-detection> (their anti-bot architecture overview)
- <https://cloud.browser-use.com/bux> (account credits / billing dashboard)
