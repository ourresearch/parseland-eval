# CLAUDE.md

Conventions for contributors — human or AI — working in this repo.

## What this repo is

`parseland-eval` is the **sole deployment source** for the Parseland evaluation dashboard. The Heroku app at <https://openalex-parseland-dashboard-fe36c419013c.herokuapp.com/> auto-deploys from `main`. Do not re-route deployment elsewhere without explicit sign-off from Casey.

## What this repo is not

- It is **not** a copy of `parseland-lib/eval/` or `parseland-lib/dashboard/`. Those directories still exist inside the `parseland-lib` repo but their GitHub Actions deploy workflow was renamed to `deploy-dashboard.yml.disabled` on 2026-04-20. Edits there will not ship.
- It does **not** modify `parseland-lib` production parser code. If a failure mode surfaces during eval, log it for a separate job (currently oxjob #132) — do not "fix it quickly" in the parser.

## Non-negotiables

1. **No silent fallbacks.** The runner calls the live Parseland service via Taxicab. There is no in-process `parseland-lib` path. If the live service is unreachable, the eval must fail loudly with a visible error — not quietly score the wrong thing.

2. **Thresholds are data-tuned, not eyeballed.** `ABSTRACT_MATCH_THRESHOLD` in `eval/parseland_eval/score/abstract.py` is produced by `scripts/tune_abstract_threshold.py` against the most recent baseline run. If you change it, re-run the tuner first and paste the output into your commit message.

3. **Back-compat on the dashboard side.** Every new summary key must land in the Zod schema as `.optional()`. Older run JSONs predate the current schema and must still render.

4. **One run JSON per eval.** Do not hand-edit `runs/*.json` or `runs/index.json`. They are write-only outputs of `python -m parseland_eval run` — regenerate them.

## How the runner works

1. Load gold rows from `eval/gold-standard.json`.
2. For each DOI: `Taxicab /taxicab/doi/<DOI>` → first `html[].id` (harvest UUID) → `Parseland /parseland/<UUID>` → extracted metadata.
3. Score per-field (authors, affiliations, abstract, PDF URL).
4. Aggregate: overall, per-publisher, per-failure-mode.
5. Write `runs/<label>-<timestamp>.json` and rebuild `runs/index.json`.

Base URLs for the two APIs are in `eval/parseland_eval/api.py`, overridable via `TAXICAB_URL` / `PARSELAND_URL` env vars for staging.

## When to run which command

| Task                                              | Command                                                                 |
|---------------------------------------------------|-------------------------------------------------------------------------|
| Score against live Parseland, full 100 gold rows  | `python -m parseland_eval run --label <name>`                           |
| Quick smoke (5 rows, ~5 s)                        | `python -m parseland_eval run --label smoke --limit 5`                  |
| Re-tune the abstract threshold                    | `python scripts/tune_abstract_threshold.py`                             |
| Python tests                                      | `pytest`                                                                |
| Dashboard dev server                              | `cd dashboard && npm run dev`                                           |
| Dashboard production build (type-check + bundle)  | `cd dashboard && npm run build`                                         |

## Metric conventions

- **Authors / Affiliations precision/recall**: macro-averaged across rows (mean of per-row P, mean of per-row R). Matches how F1 was averaged historically.
- **PDF URL precision/recall**: **micro-aggregated** — `TP = strict_match`, `FP = present & ¬strict_match`, `FN = expected_present & (¬present | ¬strict_match)`. Rows where the gold has no PDF URL and parseland returned none are true negatives and are excluded from both the P and R denominators. This is deliberate; macro would over-weight those rows.
- **Abstract match rate**: mean of per-row `match_at_threshold` where `match_at_threshold = fuzzy_ratio >= ABSTRACT_MATCH_THRESHOLD` (with "both empty = match" and "asymmetric empty = miss" short-circuits).

If you add a new field, mirror this structure in `score/aggregate.py::summarize()` and keep the legacy F1 / ratio keys alongside for back-compat.

## File layout cheat-sheet

- Change scoring logic → `eval/parseland_eval/score/<field>.py`
- Change aggregation → `eval/parseland_eval/score/aggregate.py::summarize`
- Change API calls → `eval/parseland_eval/api.py`
- Change CLI surface → `eval/parseland_eval/cli.py`
- Change dashboard schema → `dashboard/src/lib/schema.ts` (make new keys optional)
- Change dashboard components → `dashboard/src/components/*.tsx`
- Gold standard edits → `eval/gold-standard.json`; document any new quirk in `eval/parseland_eval/gold.py`.

## Gold-standard quirks already handled

(See `eval/parseland_eval/gold.py` for the adapter.)

- `"N/A"` or `"N/A\`"` in `Authors` → `authors=[]` (expected-empty).
- Row 5 journal title leaked into Authors → `gold_quality="journal-title-leaked"`, authors scoring skipped.
- Row 51 unparsed JSON string → retry; if still broken, `gold_quality="broken-json"`, authors scoring skipped.
- `rasses` key accepted as alias for `affiliations`.

Source JSON is never mutated — mutations live in the adapter.

## Known DOIs that Taxicab hasn't harvested

As of 2026-04-21: `10.36838/v4i6.14`, `10.1371/journal.pone.0192138.t002`. These surface as `error: "taxicab: taxicab-no-html"` in the run JSON. Do not drop them from the gold set quietly — harvest fixes are tracked under a separate job (oxjob #133).

## Commit hygiene

- Commit messages mention the oxjob number when relevant (e.g., `#130`).
- Schema / metric changes go with a representative run JSON so the dashboard has something to render.
- Never commit secrets — the Anthropic / OpenAI keys live in `.env` (symlinked from `parseland-lib/eval`), which is `.gitignore`'d.

---

## Gold-standard scale-up (oxjob #122)

Adds a separate sprint on top of the existing eval harness to grow gold from 100 → 10,000 Crossref DOIs inside a $1–2k budget. Scripts live under `eval/scripts/` — these are experiments, distinct from the core `parseland_eval` runner above. Source of truth for the sprint is `~/Documents/OpenAlex/oxjobs/working/parseland-gold-standard/`. Draft per-session updates go into `parseland-eval/OXJOB.md` + `NEXT-TO-DO.md` at the repo root.

### New scripts (all under `eval/scripts/`)

| Script | Purpose |
|---|---|
| `sample_50_random_dois.py` | Pulls 50 random DOIs from Crossref `/works?sample=50`, de-duped against manual gold |
| `extract_with_agent_browser.py` | **Pass A** — Python subprocess drives `agent-browser` (headed Chromium), single Claude call per DOI with tool-use schema |
| `extract_with_agent_claude.py` | **Pass B** — Claude-driven agent loop over `agent-browser` tools (7 browser tools + `record_extraction`, 15-turn cap, 40K-input-token budget) |
| `extract_with_real_chrome.py` | Pass C fallback — `agent-browser --auto-connect` attaching to user's running Chrome |
| `extract_with_browser_use.py` | **Pass C** — `browser-use` library + real Chrome over CDP (Profile 2), pre-built agent loop |
| `compare_passive_vs_agentic.py` | Field-by-field diff between extracted CSVs |
| `gpt_review.py` | OpenAI GPT-4o Structured Outputs reviewer over extracted CSVs |
| `pilot_report.py` | Summary + auto-append to oxjob `LEARNING.md` |

### New module

- `eval/parseland_eval/pricing.py` — Anthropic + OpenAI rate tables with prompt-caching multipliers (write 1.25x, read 0.10x).

### External deps added for this sprint

- **Global npm:** `agent-browser` (Vercel's Rust+Node CLI). Install: `npm install -g agent-browser && agent-browser install`.
- **Python:** `openai~=1.50`, `browser-use`. `browser-use` pulls transitive `openai 2.16.0` which violates the pin — not a blocker today, but use a separate venv if Pass A/B need reruns.

### Dotenv gotcha — critical

Use `load_dotenv(..., override=True)` in *every* pilot script. Without `override=True`, a stale shell-exported `ANTHROPIC_API_KEY` silently shadows the clean value in `eval/.env` and produces `APIConnectionError` / `LocalProtocolError: Illegal header value` that looks like an SSL bug but isn't. Burned ~45 min on this 2026-04-21.

### Claude-in-Chrome (`/chrome`) — client-side only

The `/chrome` slash command activates the Anthropic Chrome extension in the Claude Code *client UI*. It does **not** inject browser-control tools into the API session of an assistant driving work programmatically. Assistants cannot invoke `/chrome` via the Skill tool — the runtime explicitly rejects it ("chrome is a UI command, not a skill"). For programmatic real-Chrome automation, use `agent-browser --auto-connect` or `browser-use` (both connect via CDP to a Chrome launched with `--remote-debugging-port=9222`).

### Launching real Chrome for Pass C

```bash
# Quit Chrome fully (Cmd-Q) first.
open -a "Google Chrome" --args \
  --remote-debugging-port=9222 \
  --profile-directory="Profile 2"
```

`--profile-directory` name varies per user. Get list via `ls -1 "$HOME/Library/Application Support/Google/Chrome/" | grep -E "^(Default|Profile)"`.

### Pilot findings (2026-04-21)

| Metric | Pass A (passive, headed) | Pass B (Claude+agent-browser) | Pass C (browser-use+real Chrome) |
|---|---|---|---|
| rows OK | 50/50 | 28/50 (22 token-budget failures) | pending |
| cost | **$1.10** | $5.09 | pending |
| wall | 460s | 1040s | pending |
| authors hit | **46%** | 38% | pending |
| abstract hit | **36%** | 26% | pending |
| pdf_url hit | **28%** | 26% | pending |
| bot-check rate | 38% | 16% | pending |

Headless (Pass A earlier run) saw 60% bot-checks and 28% authors — headful cuts bot-checks by more than a third and nearly doubles author extraction. Keep headful.

Pass B is **worse than Pass A** as-configured because 40K-input-token budget per DOI is too tight given cumulative tool-output re-sending. Either raise the budget to ~100K or use `browser-use`'s built-in agent loop (Pass C).

### Proxy / Zyte position

Zyte residential proxies are detectable via `is_residential_proxy` flags by commercial bot-detection APIs. If already running from a clean home/residential IP, Zyte adds nothing for anti-bot and costs money. Keep Zyte as a per-publisher fallback if specific publishers block our production IPs; don't use by default.

### Parallelization at 10K scale (open, see `OXJOB.md`)

Three paths evaluated:
- **Mac Mini local** — 2–4 concurrent headful Chrome → 10K in ~2 days, $600 one-time
- **Linux cloud VM with Xvfb** — 8+ concurrent, 10K in ~6–12 h, ~$30/mo
- **Browserbase / browser-use Cloud** — 50+ concurrent, blows budget at sustained scale ($72/day)

Recommendation: **Mac Mini for one-shot 10K build**. Keep Xvfb as scalable fallback. Full writeup in `OXJOB.md`.
