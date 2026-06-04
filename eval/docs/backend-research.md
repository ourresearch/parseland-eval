# Backend research — extraction engine options (research + recommendation)

Status: **research only** this pass (no bake-off built). Default stays browser-use Cloud +
local-CDP; `cached_html` (Taxicab + Claude) is the locked primary generator. A future swap
only implements the `Backend` protocol (`goldie_cli/backends/base.py`) and registers in
`get_backend` — no tier/orchestrator/transform changes.

## Constraints
- Gold evidence is page-only (DOI.org landing / Taxicab cache / rendered DOM / browser
  session / Browserbase raw Fetch during the spike). No external metadata API as evidence.
- Accuracy bar 85% (Casey) / 95% aspirational (Jason). Bot-checks make ~22% of the holdout
  uncrawlable on a live fetch.
- Cost: up to ~$5K for a 10K run is in-budget; speed matters.

## Incumbent baseline (measured, RESULTS.md / memory 2026-04-30)
- **cached_html (Taxicab + direct Claude Sonnet, JSON-retry)** — LOCKED primary. 44× faster
  / 4× cheaper than browser automation; the cache predates bot-checks so it covers pages a
  live fetch can't. Holdout-50: authors 88, abstract 78, ca 80, rases 58, pdf 54 (pre-relaxed
  comparator). The agentic loop adds noise on static pre-fetched HTML.
- **browser-use Cloud v3** — reserved for live JS-rendered pages. ~22% fetch failures on the
  bot-gated long tail even with residential proxy.
- **local CDP (browser-use over Chrome)** — tier-2 live fallback for JS-only pages.

## Candidates (qualitative — verify with a 30-DOI bake-off before any swap)
| Engine | Structured output | Anti-bot | Concurrency | $/1k (approx) | Fit |
|---|---|---|---|---|---|
| Stagehand (Browserbase, TS) | `extract()` + schema, action caching | Browserbase stealth | Cloud sessions | ~$? + LLM | Strong for repeat-same-publisher templates; TS is a stack mismatch |
| Skyvern | vision (screenshots) | vision-robust | self-host/cloud | higher (vision) | Good for legacy/JS-only/visual pages where DOM is unreliable |
| OpenAI Codex computer-use | via JS sandbox | n/a | per-agent | n/a | Coding agent w/ computer-use; not a batch-extraction engine |
| Firecrawl | schema extract / clean markdown | some anti-bot | API | ~$? | Clean HTML→fields for the long tail; close to the cached path |
| Browserbase Fetch (spike) | raw HTML only | stealth | API | ~$1/1k (blog; verify) | Raw-HTML preflight beside Taxicab — measured by `goldie spike browserbase-fetch` |

## Recommendation
Keep browser-use Cloud + local-CDP as the live tiers and `cached_html` as primary. Run the
**Browserbase raw-Fetch spike** to decide whether Browserbase Fetch beats Taxicab on
block-rate / useful-HTML on the bot-gated tail. Swap triggers: (a) the spike shows materially
more useful HTML on currently-blocked DOIs, or (b) a candidate clears the 95% bar on a 30-DOI
bake-off at acceptable cost. Until then, no swap.

## Migration / shims note
The old `eval/scripts/*` extractors are NOT converted to shims in this pass: several
(`diff_goldie.py`, `extract_with_judge.py`, `tui_progress.py`, …) carry uncommitted user
changes, and `extract_via_taxicab.py` is imported live by `goldie_cli` (transforms +
cached_html bridge). Shimming them would clobber WIP and break the bridge. Deprecation is
deferred until those functions are internalised into `goldie_cli` with fixtures.
