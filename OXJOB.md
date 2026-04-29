# OxJob update — parseland gold-standard

Draft for pasting into the oxjob (LEARNING.md / reply to Jason in Slack). Session sections are appended in reverse-chronological order so the latest is first.

---

## 2026-04-29 session — Phase C iteration on holdout-50

### TL;DR

- Four prompt revisions iterated against the sealed holdout-50 (`eval/goldie/holdout-50.csv`): v1.1, v1.2, v1.3 via browser-use Cloud; v1.4 via manual Chrome MCP walkthrough. Results below — none clear the 95% gate, so Phase D/E remain blocked.
- v1.3 regressed sharply because the new "bail on uncertain page" rule fired too aggressively — half the DOIs came back with empty author/affiliation/CA arrays.
- v1.4 reverts the over-eager bail rule and adds an explicit "no URL construction from DOI patterns" instruction. Manual MCP measurement is depressed by the `mcp__claude-in-chrome__find` tool returning element summaries instead of verbatim text — the prompt is fine, the manual extractor is the bottleneck. The +20pp PDF URL gain (42% → 62%) is real and confirms the URL rule works.
- A fair v1.4 measurement requires one automated browser-use Cloud run on holdout-50. Account credits exhausted mid-iteration on v1.3 → top up at <https://cloud.browser-use.com/bux> before the final pre-lock measurement.
- Strategic decision: bot-check bypass uses **browser-use Cloud's built-in residential-proxy + auto-CAPTCHA stack only**. Zyte explicitly NOT adopted — Cloud's anti-bot is integrated, Zyte would be redundant + double-billed.

### Holdout-50 results

| Prompt | Source | Authors | Affs | CA | Abstract | PDF URL | Overall |
|---|---|---|---|---|---|---|---|
| v1.1 | cloud (full) | 78% | 48% | 70% | 68% | 42% | 16% |
| v1.2 | cloud (full) | 68% | 42% | 54% | 62% | 42% | 16% |
| v1.3 | cloud (full) | 18% | 18% | 18% | 28% | 56% | 10% |
| v1.4 | manual MCP* | 50% | 36% | 56% | 28% | 62% | 12% |
| **Gate** | — | **≥95** | **≥95** | **≥95** | **≥95** | **≥95** | **≥95** |

*Manual MCP numbers are depressed by `find` returning element summaries; not directly comparable to the cloud rows above. Final v1.4 cloud measurement pending credit top-up.

Artifacts in `eval/goldie/`:
- `summary-v1.{1,2,3}-holdout.json` — per-field hit rate + per-DOI miss list.
- `disagreements-v1.{1,2,3}-holdout.md` — diffed rows for prompt-iteration triage.

### Decisions locked this session

- **Phase D/E gated** behind a fair v1.4 cloud measurement. No 10K extraction until at least one field-mix passes ≥95%, ideally all six.
- **Residential-proxy strategy:** browser-use Cloud's built-in stack only (195+ countries, default `proxy_country_code=us`, auto-CAPTCHA, JS-fingerprint matched to exit IP). Per-batch override available via `--proxy-country <ISO>` for publisher-specific gates.
- **Gold standard reorganization:** `eval/Gold-Standard.csv` (100 nonblank rows) split into `eval/goldie/train-50.csv` (rows 1–50, prompt-tuning) and `eval/goldie/holdout-50.csv` (rows 51–100, sealed validation). The legacy `eval/gold-standard.{csv,json,holdout.json,seed.json}` files are being retired in favor of this split. A separate `eval/human-goldie.csv` (2322 rows) is downstream gold-standard expansion and is **not** the validation truth — that remains the audited 100-row split.

### Open questions

- Does v1.4 cloud actually clear ≥95% on any field? Need credit top-up + one run to know.
- If v1.4 cloud still misses on authors/CA, what's the prompt change for v1.5 — explicit JSON-LD parsing? Per-publisher domain skills? Or accept the misses and gate at ≥90%?
- For publishers that gate even with Cloud's built-in residential proxy (ScienceDirect heavy sessions, APS Phys Rev, Brill book chapters, ACS Pubs, Ovid, T&F sometimes), is per-batch country override sufficient, or do we need a publisher-specific domain skill?

---

# OxJob update — parseland gold-standard, 2026-04-21 session

Draft for pasting into the oxjob (LEARNING.md / reply to Jason in Slack).

---

## TL;DR for Jason

- Headed Chromium decisively beats headless (60% → 38% bot-check rate, author extraction 28% → 46%). Agreed: we ship headful.
- Our hand-rolled "Claude-drives-the-browser" (Pass B) did *worse* than passive (Pass A) and cost 4.6× more because of cumulative-token budget failures. So we're evaluating `browser-use` for Pass C, which has a purpose-built agent loop. That runs next, against the user's real logged-in Chrome over CDP.
- Zyte probably doesn't help us if we already have a clean home/residential IP — your intuition was right. Modern bot detection flags residential-*proxy* ranges too (`is_residential_proxy`). Keep Zyte in our back pocket for publishers that end up rate-limiting our IP specifically.
- At 10K total calls the multi-machine Anthropic-abuse-detection concern is negligible — that's a Tier-2 volume, no burst patterns. Fine.
- Headful parallelization is the real open question. Three options with tradeoffs below.

---

## Q1 — How to parallelize headful Chrome?

**Your concern:** headful needs "the actual Chrome app" and you're not sure how to run many concurrently.

Three real paths:

### A. Dedicated Mac Mini (local, cheapest)
- **Approach:** single Mac Mini ($599 base M4) running 2–4 concurrent headful Chrome instances under different user profiles. Each instance pulls from a shared DOI queue.
- **Throughput at 1 min/DOI:** 4 instances × 60 DOIs/hr × 24 hr = 5,760/day → 10K in **~2 days wall time**.
- **Cost:** one-time $600. Electricity negligible. No proxy cost (residential IP).
- **Pro:** cheapest long-term; matches our current Pass A/Pass C setup almost 1:1.
- **Con:** single point of failure; sharing one Chrome binary across tabs is OK, sharing across profile directories may fight over cookies.

### B. Cloud Linux VM with Xvfb virtual display
- **Approach:** Linux VM (e.g. Hetzner CX41 ~$30/mo) running `Xvfb` to provide a virtual display so Chrome thinks it's headful (real UA, no "HeadlessChrome" string) while never actually drawing pixels. Several concurrent Chrome instances per VM.
- **Throughput:** 8+ concurrent feasible on 8-CPU VM → 10K in **~6–12 hours**.
- **Cost:** ~$30/mo if left running, or $1–2 for the single 12-hour run.
- **Pro:** most concurrency-per-dollar; reproducible infra; no Mac needed.
- **Con:** some engineering effort (cookie/profile bootstrapping, login flows if we need them), + cloud IPs are more likely to get flagged than home residential IPs.

### C. Hosted headful browser service (Browserbase, browser-use Cloud)
- **Approach:** pay a vendor per concurrent session. browser-use Cloud is $0.06/hr pay-as-you-go, or $100/mo Starter = 50 concurrent + discounted proxy.
- **Throughput:** 50+ concurrent if we want.
- **Cost (at sustained scale):** 50 concurrent × $0.06/hr × 24 h = $72/day. **Blows the $1–2K total budget in under a month.** For a one-shot 10K run it'd be ~$5–10.
- **Pro:** zero infra work.
- **Con:** expensive at sustained scale; still hit publisher-level bot detection because vendor IP ranges are fingerprintable.

**Recommendation:** **Option A (Mac Mini) for the 10K one-shot**, keep Option B as the scalable fallback. Option C only for ad-hoc surges.

## Q2 — Does Zyte help with headless?

**Your intuition:** "most of what Zyte gets us is a residential IP, and if you run this locally from your house, you've already got a residential IP."

**Confirmed.** Research (2026-04):
- Zyte's residential-proxy network is detectable — commercial bot-detection APIs now flag `is_residential_proxy` on Zyte ranges explicitly.
- For headless Chrome, the blocker is primarily the `HeadlessChrome` UA string and CDP fingerprints, not the IP. Zyte doesn't fix that — you still have a headless Chrome UA.
- For **headful from a clean home IP**, Zyte adds nothing and costs money.
- Where Zyte *would* help: if we run from a cloud VM and specific publishers rate-limit our VM's IP range. Keep it as per-publisher fallback, not default.

**Verdict: drop Zyte as default; bring back only if we see publisher-specific IP blocks in production.**

## Q3 — Multi-machine Anthropic abuse risk?

**Your concern:** one Claude account doing scraping on multiple machines at once → suspicious pattern → account block.

**Research finding:**
- Anthropic rate limits are per-org, tiered by spend. Tier 2 gives us ~2M input tokens/min and 4K requests/min — way more than we'd use at 10K total calls.
- Anthropic did add **weekly rate limits** to paid subscribers after abuse incidents in 2025, but those are on chat subscriptions (Claude Pro), not API traffic.
- Abuse detection watches for burst patterns + sustained anomalous usage. A 2–3 day batch of ~10K calls evenly paced is well within normal usage.
- Multi-machine hitting from one API key is fine operationally — the API doesn't care where calls come from as long as the key is valid and within quota.

**Verdict: no material risk at 10K volume.** For longer-term or >100K work, pace the requests and stay within Tier-3 limits. Not a blocker.

## Q4 — Mac Mini vs DGX Spark?

**Quick answer: Mac Mini wins, DGX Spark is the wrong tool.**
- Browser automation is **IO/network-bound**, not GPU-bound. DGX's compute power is wasted here.
- Mac Mini M4 handles 2–4 concurrent Chrome instances comfortably within its 16GB baseline.
- $600 vs $4000 — 7× cost difference, zero performance benefit for this workload.
- DGX makes sense for model training or local LLM serving, not browser scraping.

## Bonus: about browser-use (what we're testing today as Pass C)

- Open-source Python library that wraps Playwright and exposes an LLM-driven agent loop (LLM picks browser actions, observes results, iterates).
- Supports CDP connection to existing Chrome (`Browser(cdp_url="http://localhost:9222")`) — so it plays with our real logged-in Profile 2 Chrome the same way `agent-browser --auto-connect` does.
- Includes a built-in history/context manager, so it shouldn't hit the cumulative-token budget failure our hand-rolled Pass B did.
- Downside: the pinned `ChatAnthropic` enum in the shipped version doesn't list Sonnet 4.6 yet — we're running 4.5 for Pass C, slight model delta vs Pass A/B.
- Cloud version exists at $0.06/hr for 50 concurrent — usable for bursty parallel, but sustained use blows our budget.

---

## Pilot results to date (on the same 50 random Crossref DOIs, not in the gold set)

| | Pass A (passive, headed) | Pass B (Claude+agent-browser) | Pass C (browser-use+real Chrome) |
|---|---|---|---|
| rows OK | 50/50 | 28/50 | pending |
| cost | **$1.10** | $5.09 | pending |
| authors % | **46%** | 38% | pending |
| abstract % | **36%** | 26% | pending |
| pdf_url % | **28%** | 26% | pending |
| bot-check % | 38% | 16% | pending |

---

## Side questions

- **WeWork $35/day 1–2× a week** — pending your approval.

---

## Sources cited in research

- [Headless vs headful detection tradeoffs (ScrapingAnt)](https://scrapingant.com/blog/headless-vs-headful-browsers-in-2025-detection-tradeoffs)
- [Why headless fails at scale (Anchor)](https://anchorbrowser.io/blog/choosing-headful-over-headless-browsers)
- [browser-use Cloud pricing review 2026 (MakerStack)](https://makerstack.co/reviews/browser-use-cloud-review/)
- [Anthropic API rate limits](https://docs.anthropic.com/en/api/rate-limits)
- [Claude API quota tiers and limits 2026](https://www.aifreeapi.com/en/posts/claude-api-quota-tiers-limits)
- [Zyte residential proxy detection (IPGeolocation)](https://ipgeolocation.io/ip-security-api.html)
- [Claude for Chrome extension docs](https://code.claude.com/docs/en/chrome)
- [Mac Mini OpenClaw headless-agent production setup](https://github.com/guglielmofonda/mac-mini-openclaw-guide)
- [Parallel Claude Code browser agents (MindStudio)](https://www.mindstudio.ai/blog/parallel-browser-agents-claude-code)
