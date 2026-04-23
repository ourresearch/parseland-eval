# NEXT-TO-DO — parseland gold-standard scale-up

Live worklist for OxJob #122. Organized by priority. Edit as items close.

## Immediate (today / tomorrow)

- [ ] **Run Pass C end-to-end** — browser-use + user's real Chrome Profile 2 + CDP
  - Prereq: user quits Chrome fully, relaunches with `open -a "Google Chrome" --args --remote-debugging-port=9222 --profile-directory="Profile 2"`
  - Smoke on 1 DOI → full 50 → `random-50-chrome.csv` + `.meta.json`
  - Capture cost, wall time, per-field hit rates, step count per DOI
- [ ] **3-way compare + GPT review**
  - `compare_passive_vs_agentic.py` with all three passes
  - `gpt_review.py` over all three CSVs (needs `OPENAI_API_KEY` in `eval/.env`)
  - `pilot_report.py` → appends to oxjob `LEARNING.md`
- [ ] **Commit Pass C + review artefacts to `parseland-eval`**
- [ ] **Append 3-way verdict to oxjob LEARNING.md + push to `ourresearch/oxjobs`**

## Open questions surfaced by Jason (2026-04-21 Slack)

- [ ] **How to parallelize headful Chrome** — Jason's core blocker. Options documented in `OXJOB.md`:
  - **Local**: dedicated Mac Mini (2-4 concurrent headful instances, 2-3 days wall time for 10K at ~1 min/DOI)
  - **Xvfb on Linux cloud VM**: headful UA fingerprint without a display — best concurrency/cost ratio, needs engineering
  - **Browserbase / browser-use Cloud**: pay-per-hour hosted headful — budget-breaking at sustained scale ($0.06/hr × 50 × 24h ≈ $72/day)
  - Decide before 500-DOI gate.
- [ ] **Zyte — keep or drop?** Jason's intuition is right: residential proxy from a cloud VM helps; from home IP it's marginal. Keep planned as backup for publishers that specifically block our Mac Mini's IP range; don't use by default.
- [ ] **Anthropic API multi-machine risk** — at 10K total calls we're well within Tier 2 rate limits. No burst / sustained pattern needed. **Negligible risk of abuse flagging** at this volume. Document once we confirm.
- [ ] **Cloud vs Mac Mini hardware decision** — Mac Mini $600 wins over DGX Spark $4000 (workload is IO-bound, not GPU-bound). Confirm with Jason whether to procure.
- [ ] **WeWork approval** — user asked Jason on 2026-04-21 (pending response).

## Quick fixes / hygiene

- [ ] Rotate `ANTHROPIC_API_KEY` (it was shared via screenshot earlier in the session — small exposure risk).
- [ ] Pin a `parseland-eval/CLAUDE.md` in this repo (done ✅ 2026-04-21).
- [ ] Consider creating a dedicated `parseland-eval/.env.example` with required keys listed + chmod 600 note.
- [ ] Resolve pyproject conflict: browser-use pulled `openai 2.16.0` which violates pinned `openai~=1.50`. Either widen pin to `openai>=1.50` or use separate venvs per pass.
- [ ] `claude-sonnet-4-5` vs `claude-sonnet-4-6` gap in Pass C — browser-use's `ChatAnthropic` Literal needs updating (either upstream PR or local monkey-patch).

## Mid-term (week-level)

- [ ] **Implement fetcher strategy refactor** (Plan Phase 1) — `fetch.py` to `BaseFetcher` ABC with `HttpFetcher`, `AgentBrowserFetcher`, `ZyteFetcher`, `BrowserUseFetcher`, `WebSearchFetcher`.
- [ ] **Multi-threading** (Plan Phase 3) — reference pattern from OxJob 43.1 (corresponding-authors) — user still to share Notion link.
- [ ] **Cost budget guard rail** — abort before exceeding `--cost-limit` default $2000.
- [ ] **500-DOI scale test** — only after the winning Pass is validated and the parallelization story is settled.

## Decisions already locked

- Vercel `agent-browser` over Puppeteer for programmatic headed/headless.
- OpenAI GPT-4o Structured Outputs for review (not Codex).
- Crossref `/works?sample` (no type filter) for fresh DOI sampling.
- $1–2k total budget envelope across Anthropic + OpenAI + (optional) Zyte.
- Phased rollout ends at 500 DOIs; going bigger is a separate meeting.

## Out of scope (parked)

- Parallel browser-use Cloud tenancy (cost blows budget).
- Databricks-based scraping pipeline (overkill at 10K DOIs — Jason confirmed Databricks is for "when we do things at that level of parallelization").
- DGX Spark procurement (GPU useless for this IO-bound workload).
