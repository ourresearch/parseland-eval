# Runbook — BUX Operations

Operator-facing reference for every phase of the BUX-driven 10K production run.
Read top-to-bottom for first-time setup, then jump to "Failure modes" when
something breaks.

---

## Phase-by-phase commands

### E.0 — Deploy code to BUX

```bash
# From the parseland-eval repo root, on local Mac
bash eval/browser-use/setup/02-deploy.sh
# Or dry-run first:
bash eval/browser-use/setup/02-deploy.sh --dry-run
```

After this finishes, ssh into BUX and verify the env vars are set in `eval/.env`. See `setup/env.example`.

### E.0.5 — Smoke test (4 gates)

```bash
bash eval/browser-use/verify/smoke.sh
# Or dry-run:
bash eval/browser-use/verify/smoke.sh --dry-run
```

All four gates (Tier 1 / Tier 1.5 / Tier 2 / Telegram) must pass.

### E.0.6 — Parity-50 verification (gates 7 & 8)

```bash
bash eval/browser-use/verify/parity_50.sh
```

Re-runs the 50-DOI dry run on BUX through the new 5-tier pipeline, diffs against `runs/50-10K-test/ai-goldie-50.v2.csv`. **Gate 8 is the critical Tier-1.5 effectiveness test** — extraction-miss count must drop below the local baseline (5). See `../verify/acceptance-gates.md`.

If gate 8 fails, the 10K can still proceed (Tier 1.5 is additive), but log a note in `RESULTS.md` and reconsider.

### E.1 — Batch 1 production

```bash
ssh bux 'cd /home/bux/parseland-eval && bash eval/browser-use/runtime/run_10k_on_bux.sh --batch 1'
```

After ~3 minutes, BUX posts the batch-1 scoreboard to Telegram and waits on `/home/bux/runs/10k/.gate-released` (created when the operator replies `/go` in the channel).

While waiting, review `runs/10k/batch-1/ai-goldie-1.v2.csv` on BUX (`ssh bux 'less /home/bux/runs/10k/batch-1/ai-goldie-1.v2.csv'`). If issues are visible, **don't reply /go yet** — fix them first.

### E.2 — Batches 2-100 unattended

After replying `/go` in Telegram (or manually `touch /home/bux/runs/10k/.gate-released`):

```bash
ssh bux 'cd /home/bux/parseland-eval && bash eval/browser-use/runtime/run_10k_on_bux.sh --batches 2-100 --no-gate'
```

Runs unattended for ~5 hours. Outputs land in `/home/bux/runs/10k/batch-{2..100}/`.

### E.3 — Final audit

```bash
ssh bux 'cd /home/bux/parseland-eval && tail -10 RESULTS.md'  # confirm cycle appended
ssh bux 'cd /home/bux/parseland-eval && git status'           # see all 100 output CSVs
# Pull the final outputs back to local
rsync -av --progress bux:/home/bux/runs/10k/ runs/10k-bux/
```

Commit `runs/10k-bux/` on a feature branch + open a PR.

---

## Local ↔ BUX differences

| Aspect | Local Mac | BUX |
|---|---|---|
| `CDP_URL` | `http://localhost:9222` (Chrome launched with `--remote-debugging-port=9222`) | `wss://<browser-harness-host>:<port>` (set in BUX env from dashboard) |
| Tier 2 concurrency | 2 (Chrome processes) | 50 (Browser Harness sessions on Starter tier) |
| Where `.env` lives | `eval/.env` | `/home/bux/parseland-eval/eval/.env` |
| Where outputs land | `runs/<test-name>/` | `/home/bux/runs/10k/batch-<n>/` |
| How to read logs | stdout | `journalctl -u claude-code` / `journalctl -u browser-harness` |
| Telegram | not used | `/go` gate after batch 1 |
| Re-harvest endpoint | `http://harvester-load-balancer-...elb.amazonaws.com` (works from anywhere) | same |
| Anthropic API key | from `~/.zshrc` or `eval/.env` | from `eval/.env` (scp'd by 02-deploy.sh) |

---

## Failure modes and fixes

### ssh-bux-times-out

**Symptom:** `ssh bux echo connected` hangs or returns `Connection timed out`.

**Cause:** BUX is likely sleeping or the managed instance has cycled.

**Fix:**
1. Open <https://cloud.browser-use.com/bux>, check instance state
2. If sleeping, wake via dashboard
3. If cycled (rare), re-run `setup/02-deploy.sh` — it's idempotent

### tier-1-returns-0-successes

**Symptom:** `extract_via_taxicab.py` finishes in seconds with all rows showing `tier=failed` and cost=$0.

**Cause:** `ANTHROPIC_API_KEY` not set in BUX's runtime env. The script does NOT call `load_dotenv()` (see CLAUDE.md "Dotenv gotcha"), so the env must be exported before the script runs.

**Fix:**
```bash
ssh bux 'cd /home/bux/parseland-eval && set -a && source eval/.env && set +a && env | grep ANTHROPIC'
# Then re-run the failing command with the env exported in the same shell:
ssh bux 'cd /home/bux/parseland-eval && set -a && source eval/.env && set +a && eval/.venv/bin/python eval/scripts/extract_via_taxicab.py ...'
```

### tier-1-5-hangs-on-post

**Symptom:** `taxicab_reharvest.py` blocks on the POST or times out.

**Cause:** Harvester load balancer is rate-limited (returned 429), down (5xx), or `TAXICAB_HARVESTER_URL` is unreachable from BUX.

**Fix:**
1. Manually probe: `curl -X POST "$TAXICAB_HARVESTER_URL/taxicab" -H 'Content-Type: application/json' -d '{"native_id":"10.4324/9781003331100-8","native_id_namespace":"doi","url":"https://doi.org/10.4324/9781003331100-8"}'`
2. If 5xx, the harvester is down — skip Tier 1.5 for now (run with `--skip-tier15` flag — *not implemented yet, manual workaround: comment out the Tier 1.5 block in the orchestrator*)
3. If 429, lower `--concurrency` on `taxicab_reharvest.py` from 4 to 2

### tier-1-5-returns-same-content

**Symptom:** `reharvest.jsonl` shows `status: unchanged` for most DOIs.

**Cause:** The harvester's source for these DOIs hasn't changed — its cache is already current per its sources. Not a bug; just means Tier 1.5 won't help this batch.

**Fix:** none needed. Those rows will fall through to Tier 2 (live-fetch) which can sometimes recover content via real-browser rendering.

### tier-2-hangs

**Symptom:** `live_fetch_empty.py` is alive but not making progress; Browser Harness sessions appear stuck.

**Cause:** Most likely BUX's browser-harness systemd unit cycled (240-min refresh boundary). The script's checkpoint file in `runs/10k/batch-N/.checkpoint/` should preserve progress across the cycle.

**Fix:**
```bash
# Check the systemd unit
ssh bux 'systemctl status browser-harness --no-pager'
# Restart if needed (loses in-flight sessions, but checkpoint will resume from after-recovery)
ssh bux 'sudo systemctl restart browser-harness'
# Re-run the same batch — orchestrator will skip completed DOIs via checkpoint
ssh bux 'cd /home/bux/parseland-eval && bash eval/browser-use/runtime/run_10k_on_bux.sh --batch <N>'
```

### telegram-go-not-received

**Symptom:** Posted `/go` in the channel but `telegram_ping.py --gate` doesn't release.

**Cause:** Bot isn't admin in the channel (can't read messages); or `TELEGRAM_CHAT_ID` is wrong.

**Fix:**
1. Verify bot admin status: in Telegram, channel settings → administrators → bot must be present
2. Verify chat ID:
   ```bash
   curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates" | jq '.result[-1].message.chat.id'
   ```
3. As an emergency manual release: `ssh bux 'touch /home/bux/runs/10k/.gate-released'`

### iter-r-distribution-outside-5pp

**Symptom:** `parity_50.sh` reports iter-R label deltas larger than ±5pp.

**Cause:** Either the algorithm changed (Tier 1.5 added) is shifting labels (expected — that's gate 8), OR something in the pipeline regressed.

**Fix:**
1. Read `parity-report.md` per-row diff — identify which DOIs changed labels
2. If most changes are `extraction-miss → reharvest-recovered` → that's gate 8 working as intended. Update parity baseline.
3. If changes are random/unexplained (e.g. `complete → bot-check` on rows that were previously fine) → regression. Check the v1.9.1 prompt for accidental changes; run `git log eval/prompts/`.

### parity_50-cannot-find-input

**Symptom:** `parity_50.sh` fails with "local baseline runs/50-10K-test/ai-goldie-50.v2.csv not found".

**Cause:** You're running parity_50 from a fresh repo clone without the 50-DOI dry-run artifacts.

**Fix:** Either re-run the 50-DOI dry run (see prior plan in `.claude/plans/`), or pull the artifacts from the previous workspace.

---

## Cost watch

- Tier 1: ~$0.075/DOI × 10K = ~$750
- Tier 1.5: ~$0 per POST + ~$0.05–0.15 per re-extract × ~10% empty rate = ~$75
- Tier 2: ~$0.30–0.50 × ~10-15% × 10K = ~$420–750
- **Total ~$1245–1575 for 10K** (within OXJOB budget envelope; the umbrella plan estimated $1700 without Tier 1.5)

Tier 1.5 should *reduce* overall cost slightly by displacing some Tier 2 escalations.

---

## When in doubt

- Don't fail silently. If a tier reports an unexpected status, **stop and surface it**. Memory rule `feedback_no_silent_failures` is explicit on this.
- Don't touch `eval/human-goldie.csv` or any gold derivative — they're read-only forever (deny-rule + memory).
- Don't push to main without explicit operator approval. Local commits only.
