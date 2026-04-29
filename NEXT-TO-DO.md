# NEXT-TO-DO — parseland gold-standard scale-up

Live worklist for OxJob #122. Organized by priority. Edit as items close.

## Immediate (blocks Phase D/E)

- [ ] **Top up browser-use Cloud credits** — <https://cloud.browser-use.com/bux>. Iteration burned out balance mid-v1.3; final fair v1.4 cloud measurement on holdout-50 cannot run until credits are restored.
- [ ] **Fair v1.4 cloud measurement on holdout-50** — one automated browser-use Cloud run with `eval/prompts/ai-goldie-v1.4.md`, scored against `eval/goldie/holdout-50.csv`. Outputs `eval/goldie/summary-v1.4-holdout.json` + `disagreements-v1.4-holdout.md`. This is the only number that gates Phase D.
- [ ] **Decide v1.5 vs lock-and-ship** — if v1.4 cloud clears ≥95% on all six fields, lock and proceed to Phase D. If not, write v1.5 patches against the v1.4 disagreements (NOT against holdout — pull learning into a fresh tuning subset to preserve holdout sealing).

## Phase C status (2026-04-29)

Holdout-50 cloud iteration measured but no prompt yet clears the 95% gate:

| Prompt | Authors | Affs | CA | Abstract | PDF URL | Overall |
|---|---|---|---|---|---|---|
| v1.1 | 78% | 48% | 70% | 68% | 42% | 16% |
| v1.2 | 68% | 42% | 54% | 62% | 42% | 16% |
| v1.3 | 18% | 18% | 18% | 28% | 56% | 10% |
| v1.4* | 50% | 36% | 56% | 28% | 62% | 12% |

*v1.4 numbers are from manual Chrome MCP walkthrough, depressed by `find` returning element summaries. The PDF URL +20pp gain over v1.3 is real (URL-construction-from-DOI rule works). Cloud measurement still pending.

Iteration takeaways:
- v1.3's "bail on uncertain page" rule fires too aggressively (empty arrays for ~half the DOIs). v1.4 reverts.
- v1.4 adds an explicit "do NOT construct PDF URL from DOI patterns" rule — confirmed via the +20pp gain.
- Authors / affiliations / CA all stuck in the 50–80% band across v1.1–v1.4. Likely needs structured JSON-LD parsing or per-publisher selectors, not generic prompt patches.

## Phase D/E (gated, do not start until v1.4 cloud passes)

- [ ] **Phase D — Sample 10K DOIs** — `eval/scripts/sample_10k_dois.py` produces `eval/data/ai-goldie-source-10k.csv`. Crossref `/works?sample`, dedup vs current goldie split.
- [ ] **Phase E.1 — Extract batch 1 only** — `eval/scripts/extract_batch_cloud.py --batch 1` → `eval/data/ai-goldie-1.csv`. User reviews before any further batch runs.
- [ ] **Phase E.2 — Extract batches 2–100 in parallel** — only after batch-1 review is clean. Business tier ($299/mo, 200 concurrent) → ~38 min wall, ~$1.3K all-in.
- [ ] **Phase F — Per-batch user audit** — user reviews each `ai-goldie-N.csv`, edits in place, commits.

## Open infrastructure questions

- [ ] **Per-publisher gate strategy** — for publishers that bot-check even with Cloud's built-in residential proxy (ScienceDirect heavy sessions, APS Phys Rev, Brill book chapters, ACS Pubs, Ovid, T&F sometimes), is `--proxy-country <ISO>` override sufficient or do we need per-publisher domain skills?
- [ ] **Account-credit guard rail** — `extract_batch_cloud.py` should hard-stop and emit a clear error before draining the account if balance falls below a configured floor (e.g., $50). Currently fails noisily mid-batch.

## Quick fixes / hygiene

- [ ] **Retire legacy gold-standard files** — `eval/gold-standard.{csv,json,holdout.json,seed.json}` are deleted in working tree but not yet committed. Confirm no scripts still reference them (`scripts/`, `parseland_eval/` both have refs per the most recent git status), update those refs to the goldie/ split, then commit the deletion as a single migration commit.
- [ ] **`human-goldie.csv` (2322 rows) — clarify role** — separate from the 100-row audited split that gates Phase C. Document whether this is downstream Phase D output or an independent expansion track.
- [ ] **Pin browser-use's `ChatAnthropic` Sonnet 4.6 enum** — fix or local monkey-patch so we run on the same model end-to-end.
- [ ] **Resolve pyproject conflict** — browser-use pulls `openai 2.16.0` vs pinned `openai~=1.50`. Either widen pin or use separate venvs per pass.

## Decisions already locked

- **10K production stack:** browser-use Cloud Tasks API, Business tier ($299/mo, 200 concurrent).
- **Holdout validation stack:** local browser-use library + 4 parallel headed Chromes via CDP, BYOK Anthropic.
- **Bot-check bypass:** browser-use Cloud's built-in stack — 195+ country residential proxies + auto-CAPTCHA + JS-fingerprint matched to exit IP. Zyte explicitly **not** adopted.
- **Models:** claude-sonnet-4-6 default; Opus 4.7 only if Sonnet underperforms. `/chrome` and Codex CLI both rejected for batch use.
- **Crossref `/works?sample`** for DOI sampling (no type filter).
- **Phased rollout:** Phase E.1 batch-1 review gates Phase E.2 parallel run.
- **Hard prerequisite:** audited 100-row goldie split (`eval/goldie/train-50.csv` + `holdout-50.csv`) is the only validation truth; pre-audit measurements are throwaway.

## Out of scope (parked)

- Zyte residential proxies — redundant with Cloud's built-in stack.
- Databricks scraping pipeline — overkill at 10K.
- DGX Spark procurement — GPU useless for IO-bound workload.
- Repeated tuning against `holdout-50.csv` — leaks the seal; insoluble cases go to `eval/goldie/insoluble-cases.md` instead.
