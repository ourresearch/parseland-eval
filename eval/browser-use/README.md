# eval/browser-use/ — BUX implementation packet

Everything an operator needs to **set up Browser Use Box (BUX), verify it works,
and run the 10K production extraction job** from a fresh managed-BUX provision.

This directory is the *implementation packet*. The **architectural decision
record** lives at `/Users/shubh-trips/.claude/plans/for-the-browser-automoation-swift-porcupine.md`
— that doc explains *why* managed BUX, *why* Starter tier, *why* the 5-tier
algorithm. This directory contains the artifacts you actually invoke.

## What's BUX?

Browser Use Box — a 24/7 Ubuntu VM with Claude Code, Browser Harness (stealth
Chromium with CDP-over-WSS), and a Telegram bot preinstalled as systemd services.
Provisioned in ~60 seconds at <https://cloud.browser-use.com/bux>. Driven from
the local Mac via `ssh bux ...` (no remote CLI surface).

## Read order

1. `docs/architecture.md` — understand the 5-tier extraction pipeline
2. `setup/01-provision.md` — manual click-through (~5 min)
3. `setup/02-deploy.sh` — automated code deploy from local to BUX
4. `verify/smoke.sh` — 4 gates (Tier 1 / Tier 1.5 / Tier 2 / Telegram)
5. `verify/parity_50.sh` — re-run the 50-DOI dry run on BUX; diff vs local
6. `runtime/run_10k_on_bux.sh` — the actual 10K orchestrator
7. `docs/runbook.md` — operator reference for every phase + failure modes

## Pipeline shape (5 tiers per 100-DOI batch)

```
Tier 1    extract_via_taxicab.py     cached HTML + Claude       ~75% hit
Tier 1.5  taxicab_reharvest.py       POST harvester + re-extract  captures stale
Tier 2    live_fetch_empty.py        Browser Harness via CDP    ~10-15%
Merge     orchestrator inline        baseline-empty + delta-has = fill
Iter-R    comparator-style labels    paywalled / bot-check / pdf-redirect
Score     diff_goldie.py             batch 1 only, vs human-goldie subset
```

Tier 1.5 is **new in this packet** (not in the umbrella plan). It uses the
Taxicab harvester's POST endpoint (discovered 2026-05-12) to trigger a fresh
re-scrape of stale cached HTML. See `docs/taxicab-reharvest.md`.

## File map

```
eval/browser-use/
├── README.md                 ← this file
├── setup/
│   ├── 01-provision.md       click-through to provision BUX + SSH config + Telegram + self-register option
│   ├── 02-deploy.sh          local-Mac deploy: git clone, scp .env, install venv on BUX  [+--dry-run]
│   └── env.example           required env-var template
├── verify/
│   ├── acceptance-gates.md   8 gates (7 from umbrella plan + new gate 8 for Tier 1.5 effectiveness)
│   ├── smoke.sh              4-gate smoke (1-DOI through each tier + Telegram /ping)  [+--dry-run]
│   ├── parity_50.sh          re-run 50-DOI dry run on BUX, diff vs local v2 CSV       [+--dry-run]
│   └── fixtures/             1-DOI smoke inputs (tier1, tier1.5, tier2)
├── runtime/
│   ├── run_10k_on_bux.sh     main 10K orchestrator: 5-tier pipeline                    [--batch N or --batches N-M]
│   ├── taxicab_reharvest.py  Tier 1.5 — POST harvester + poll for refresh             [+--dry-run]
│   ├── checkpoint.py         DOI-keyed .partial.jsonl + atomic .tmp+rename helpers
│   └── telegram_ping.py      scoreboard pinger + /go gate sentinel
└── docs/
    ├── architecture.md       5-tier pipeline diagram + cross-ref umbrella plan
    ├── runbook.md            phase-by-phase ops + failure modes + local↔BUX diffs
    └── taxicab-reharvest.md  the new Tier 1.5 endpoint + when to use + rate limits
```

## What's NOT in this packet

- Not a BUX provision — manual click at <https://cloud.browser-use.com/bux> per `setup/01-provision.md`
- Not the 10K execution — that's a sequence of separate operator commands after this directory exists
- Not changes to existing `eval/scripts/` — only consumes them, doesn't modify them
- Not changes to `eval/prompts/ai-goldie-v1.9.x.md` — stays as locked
- Not touches to `eval/human-goldie.csv` — read-only forever per deny-rule
- Not Codex Stage C validators (mojibake / cross-DOI dedup / etc.) — deferred per umbrella plan
- Not the `--no-gold-guardrail` flag on `merge_livefetch.py` — separate prior follow-on

## Bottom line

After this directory exists, going from "I just provisioned BUX" to "the 10K is running" is:

```bash
bash eval/browser-use/setup/02-deploy.sh
bash eval/browser-use/verify/smoke.sh
bash eval/browser-use/verify/parity_50.sh    # if Tier 1.5 effective per gate 8
ssh bux 'cd /home/bux/parseland-eval && bash eval/browser-use/runtime/run_10k_on_bux.sh --batch 1'
# → review batch 1 → reply /go in Telegram →
ssh bux 'cd /home/bux/parseland-eval && bash eval/browser-use/runtime/run_10k_on_bux.sh --batches 2-100 --no-gate'
```

That's the entire flow. Everything else is troubleshooting via `docs/runbook.md`.
