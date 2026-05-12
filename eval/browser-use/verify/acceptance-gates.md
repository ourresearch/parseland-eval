# BUX Acceptance Gates

Runnable checklist. The umbrella plan (`.claude/plans/for-the-browser-automoation-swift-porcupine.md` lines 99-107) defines gates 1-7; **gate 8 is new for this implementation packet** to validate Tier 1.5.

All gates must pass before the 10K production run gets authorized.

---

## Gate 1 — Elsevier CA rule spot-check

Per the umbrella plan, the v1.9.2 prompt must correctly identify CA on Elsevier landing pages using either the email/message icon or `*` (unless legend overrides). Spot-check on a known Elsevier row from the 50-DOI test.

```bash
# Locally (no BUX needed)
eval/.venv/bin/python eval/scripts/extract_via_taxicab.py \
  --source eval/browser-use/verify/fixtures/tier1-1doi.csv \
  --output-dir /tmp/elsevier-ca-check \
  --prompt eval/prompts/ai-goldie-v1.9.2.md \
  --concurrency 1
```

Wait — `tier1-1doi.csv` is the Routledge fixture, not Elsevier. Substitute an Elsevier DOI from `runs/50-10K-test/ai-goldie-50.merged.csv` (e.g. `10.1016/s1098-3015(10)73112-0`) and inspect the resulting Authors JSON: at least one author should have `corresponding_author=true` if the page has the icon or `*` marker.

**Pass:** Elsevier CA correctly extracted.
**Fail:** see umbrella plan's pre-flight to fix v1.9.2.

---

## Gate 2 — BUX provision smoke (systemd green)

```bash
ssh bux 'systemctl status claude-code browser-harness telegram-bot --no-pager'
```

**Pass:** all three units show `active (running)`.
**Fail:** see `../docs/runbook.md#ssh-bux-times-out` or `01-provision.md`.

---

## Gate 3 — 1-DOI Tier 1 smoke

```bash
ssh bux 'cd /home/bux/parseland-eval && eval/.venv/bin/python eval/scripts/extract_via_taxicab.py \
    --source eval/browser-use/verify/fixtures/tier1-1doi.csv \
    --output-dir /tmp/bux-smoke \
    --prompt eval/prompts/ai-goldie-v1.9.1.md \
    --concurrency 1'
```

**Pass:** exit 0; `/tmp/bux-smoke/ai-goldie-1.csv` contains 1 row with non-empty Authors and Abstract for `10.4324/9781003331100-8` (the Routledge known-good fixture).
**Fail:** see `../docs/runbook.md#tier-1-returns-0-successes`.

---

## Gate 4 — 1-DOI Tier 1.5 smoke (re-harvest + re-extract)

```bash
ssh bux 'cd /home/bux/parseland-eval && eval/.venv/bin/python eval/browser-use/runtime/taxicab_reharvest.py \
    --dois 10.5840/zfs19354333 \
    --output /tmp/bux-smoke/reharvest.jsonl'
```

Then re-extract the refreshed DOI:

```bash
ssh bux 'cd /home/bux/parseland-eval && eval/.venv/bin/python eval/scripts/extract_via_taxicab.py \
    --source eval/browser-use/verify/fixtures/tier15-1doi.csv \
    --output-dir /tmp/bux-smoke/post-reharvest \
    --prompt eval/prompts/ai-goldie-v1.9.1.md \
    --concurrency 1'
```

**Pass:** exit 0; `reharvest.jsonl` shows `"status": "refreshed"`; post-reharvest CSV has SOME non-empty content (Authors OR Abstract OR PDF URL) for `10.5840/zfs19354333` that wasn't there in the pre-reharvest 50-DOI baseline. If still all-empty, that's fine — the fixture row is a 1935 German journal and the harvester may not have a fresh source either, in which case **gate 8** is the harder test.
**Fail:** harvester returned non-200; see `../docs/taxicab-reharvest.md` for diagnostics.

---

## Gate 5 — 1-DOI Tier 2 smoke (Browser Harness CDP)

```bash
ssh bux 'cd /home/bux/parseland-eval && eval/.venv/bin/python eval/scripts/live_fetch_empty.py \
    --targets eval/browser-use/verify/fixtures/tier2-1doi.json \
    --prompt eval/prompts/ai-goldie-v1.9.1.md \
    --output /tmp/bux-smoke/livefetch-delta.csv \
    --concurrency 1'
```

Note: relies on `CDP_URL` env var being set on BUX (script reads `os.environ.get("CDP_URL")` at line 35 of `live_fetch_empty.py`).

**Pass:** exit 0; `/tmp/bux-smoke/livefetch-delta.csv` has 1 row with Authors and/or Abstract populated for `10.1063/pt.5.6117`.
**Fail:** CDP-over-WSS to Browser Harness not reachable; see `../docs/runbook.md#tier-2-hangs`.

---

## Gate 6 — Telegram /ping smoke

```bash
ssh bux 'cd /home/bux/parseland-eval && eval/.venv/bin/python eval/browser-use/runtime/telegram_ping.py --test'
```

**Pass:** the configured Telegram channel receives a message `BUX smoke test from <hostname> at <iso-timestamp>`.
**Fail:** verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`; bot must be admin in channel.

---

## Gate 7 — Parity_50 produces ≥ 88% filled-or-explained

```bash
ssh bux 'cd /home/bux/parseland-eval && bash eval/browser-use/verify/parity_50.sh'
```

Pass criteria (encoded in `parity_50.sh`):
- Final filled-or-explained ≥ 88% (matching local 2026-05-11 baseline)
- iter-R `paywalled` and `bot-check` rates within ±5pp of local
- Zero unlabelled empty PDF URLs

**Pass:** `parity-report.md` shows green on all 3 sub-criteria.
**Fail:** see `../docs/runbook.md#iter-r-distribution-outside-5pp`.

---

## Gate 8 — Tier 1.5 demonstrably moves the needle (NEW)

This is the test that justifies adding Tier 1.5 at all. After `parity_50.sh` runs, inspect `parity-report.md`:

> **The "extraction-miss" count in the 5-tier BUX run must be STRICTLY LESS than the 5 from the local baseline.**

In other words: at least one of the 5 extraction-miss DOIs from the 50-DOI dry run (rows 1, 2, 9, 17, 45 — Routledge, Radiopaedia, Copernicus EGU, pdcnet 1935 German, De Gruyter Brill 1990 journal) must transition to either `complete` or to a different iter-R label (e.g. `iter-R:reharvest-recovered`) after Tier 1.5 runs.

**Pass:** extraction-miss count is 4 or fewer (was 5 locally).
**Fail:** Tier 1.5 isn't recovering anything → either the harvester doesn't have fresher content for these old/specialized DOIs, OR the orchestrator isn't wiring Tier 1.5 correctly. Inspect `parity-report.md`'s per-tier histogram and the corresponding `runs/50-10K-bux-parity/.checkpoint/` artifacts.

If gate 8 fails, the 10K production run can still proceed (Tier 1.5 is additive, not destructive), but the new tier provided no measurable value on this sample. Note in `RESULTS.md` and consider whether the harvester endpoint is doing what we think it is.

---

## All-clear sequence

After all 8 gates pass:

1. Commit `runs/50-10K-bux-parity/` artifacts to a feature branch
2. Append a "BUX parity validation" cycle to `RESULTS.md`
3. Authorize `runtime/run_10k_on_bux.sh` for the 10K production run via:
   ```
   ssh bux 'cd /home/bux/parseland-eval && bash eval/browser-use/runtime/run_10k_on_bux.sh --batch 1'
   ```
4. After batch 1 finishes, review `runs/10k/ai-goldie-1.csv` on BUX, reply `/go` in Telegram to unblock batches 2-100.
