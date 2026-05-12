# Architecture — BUX 5-tier extraction pipeline

## High-level diagram

```
local Mac (Claude Code + Bash → ssh bux)
   │
   └── SSH ──▶ BUX VM (managed at cloud.browser-use.com/bux)
                  │
                  ├── systemd: claude-code        (long-running orchestrator)
                  ├── systemd: browser-harness    (Chromium with CDP-over-WSS;
                  │                                routes to Browser Use Cloud
                  │                                Starter — 50 concurrent)
                  ├── systemd: telegram-bot       (scoreboard pings + /go gate)
                  ├── /home/bux/parseland-eval    (git clone of this repo)
                  └── /home/bux/runs/10k          (per-batch outputs + checkpoints)
                  
   Telegram bot (in your private channel) ──▶ scoreboard posts, /go handoff
```

The local Mac never executes the pipeline — it only triggers BUX via
`ssh bux 'bash .../run_10k_on_bux.sh ...'`. BUX runs unattended for ~5 hours
between batch-1 review and final batch.

## The 5-tier pipeline (per 100-DOI batch)

```
Tier 1   — extract_via_taxicab.py against cached HTML + Claude API     ~75% hit
            ↓ (rows where Authors AND Abstract AND PDF URL all empty)
Tier 1.5 — taxicab_reharvest.py POSTs each empty DOI to the harvester
            load balancer; polls until refreshed HTML lands; then
            re-runs extract_via_taxicab.py on those refreshed rows     captures
                                                                       stale-cache
                                                                       misses
            ↓ (rows still all-3-empty after Tier 1.5)
Tier 2   — live_fetch_empty.py via Browser Harness (CDP-over-WSS,
            50 concurrent on Starter tier)                              ~10-15%
            ↓
Merge    — orchestrator inline merge: baseline-empty + delta-has → fill;
            label `iter-R:reharvest-recovered` on Tier 1.5 fills
            ↓
Iter-R   — comparator rules #10 / #14 / #15 (paywalled / bot-check /
            pdf-redirect) label remaining empties so the downstream
            score doesn't penalize structurally-correct N/A cells
            ↓
Score    — diff_goldie.py against human-goldie.csv subset (batch 1 only)
```

### Why Tier 1.5 exists

The 50-DOI dry run on 2026-05-11 produced **5 rows labelled `iter-R:extraction-miss`** — rows where the resolved URL pointed at a reachable, non-bot-walled page but Tier 1's cached HTML had nothing useful, and Tier 2's live-fetch via Chrome also failed to recover content. The most plausible explanation for that pattern is a stale or thin cached HTML capture, not a structural extraction problem.

Tier 1.5 addresses exactly this bucket. The Taxicab harvester accepts a POST at `/taxicab` that triggers a fresh re-scrape of the underlying DOI; the refreshed page becomes readable at the standard `GET /taxicab/doi/<DOI>`. Cost per re-harvest is essentially zero (HTTP POST only — no LLM call until the subsequent re-extract).

This **directly supersedes memory entry `6918`** (2026-05-08) which audited the public-facing API surface and concluded "Taxicab API Is Read-Only Cache with No Re-Harvest Capability." That audit walked `eval/parseland_eval/api.py` and `eval/scripts/extract_via_taxicab.py` — both of which only know about the GET endpoints. The harvester load balancer's POST endpoint isn't referenced in the codebase. Future audits of external system capabilities should consult user-provided docs in addition to code references. See `taxicab-reharvest.md` for the full re-harvest endpoint reference.

### What Tier 1.5 does NOT do

Tier 1.5 will not bypass bot walls. The harvester gets the same Cloudflare / login-wall response that we'd get directly. Bot-walled rows (~14-16% per the 50-DOI baseline) still require Tier 2's real browser. The iter-R comparator labels those correctly as `iter-R:bot-check` rather than trying to "recover" them.

## Concurrency knobs

| Tier | Concurrency | Why |
|---|---|---|
| Tier 1 | 10 parallel Anthropic calls | not concurrency-gated |
| Tier 1.5 | 4 parallel re-harvests | conservative until rate-limits characterized |
| Tier 2 | 50 parallel Browser Harness sessions | Starter tier max; Tier 2 wall ≈ 48 min across all 10K |

Single tunable: "Tier 2 on BUX, 50 parallel" — everything else follows.

## Cross-references

- **Umbrella plan** (the *why*): `/Users/shubh-trips/.claude/plans/for-the-browser-automoation-swift-porcupine.md`
- **Setup** (the *how to provision*): `../setup/01-provision.md`
- **Deploy** (the *how to push code*): `../setup/02-deploy.sh`
- **Verify** (the *gates*): `../verify/acceptance-gates.md`
- **Runtime** (the *orchestrator*): `../runtime/run_10k_on_bux.sh`
- **Tier 1.5 endpoint detail**: `taxicab-reharvest.md`
- **50-DOI dry run baseline**: `../../../runs/50-10K-test/`
- **Codex Stage C review** (deferred validators): `../../../runs/50-10K-test/codex-review/comparison.md`
