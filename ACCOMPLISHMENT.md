# Accomplishments — parseland-eval

Reverse-chronological log of completed work. Each day gets a `## YYYY-MM-DD` section,
with timestamped sub-entries (newest first within the day). Links to commits, files,
and metrics where useful.

## EOD ritual (run at the end of each working day)

1. **Append**: add timestamped entries to the top of today's date section (or open a new date section).
2. **Push to oxjob**: mirror today's section into `~/Documents/OpenAlex/oxjobs/working/parseland-gold-standard/LEARNING.md` (or its successor) so the oxjob log stays current.
3. **Mirror to Notion**: paste the day's section into the project Notion page (TBD — fill page ID once known).
4. **Slack EOD**: send a 3–5 bullet summary of the day to Casey (CTO) and Jason (CEO) via Slack DM or shared channel.

The Slack EOD should answer three questions:
- What landed today? (1–2 bullets, link to commit hash)
- What's blocked or waiting on input? (1 bullet, with the ask)
- What's next? (1 bullet, the immediate next move)

---

## 2026-04-29 — Phase C closeout + repo cleanup

### 12:30 CDT — Started the daily-log ritual
- Created `parseland-eval/ACCOMPLISHMENT.md` (this file). Going forward, every working day appends a section here, then mirrors to oxjob + Notion + Slack EOD per the ritual above.

### 11:43 CDT — Pushed Phase C closeout to origin/main
- Commit: `9563eff eval: v1.4 cloud holdout fair measurement + repo cleanup`
- 23 local commits caught up to origin (was 23 ahead, now 0/0).
- Heroku auto-deploy from `main` will pick up the regenerated `gold-standard{.json,.seed.json,.holdout.json}` and the four `summary-v1.{1,2,3,4}-holdout.json` files.

### 11:30 CDT — Repository cleanup landed (5 buckets)
Net: 153 files changed, 4 793 insertions, 16 300 deletions (~3.6 MB reclaimed).
- **Bucket A (340 KB)**: removed 6 stale `goldie/` artifacts (pre-audit CSV, audit checklist, v1.4-mcp summaries + disagreements) + `runs/holdout-v1.4-mcp/`.
- **Bucket B (150 KB)**: deleted `goldie/human-goldie-v2-audited.csv`; repointed `scripts/split_train_holdout.py` default and `scripts/run_ai_goldie.py` example to `eval/human-goldie.csv`.
- **Bucket C (1.1 MB)**: retired the OXJOB 2026-04-21 pilot — 8 pilot scripts, 8 random-50 data files, 3 snapshot dirs (~96 cached HTML files). Edited `CLAUDE.md` to drop the now-dangling "New scripts" + "Pilot findings" tables and the "Launching real Chrome for Pass C" section; kept the dotenv gotcha and `/chrome` UI-only guidance.
- **Bucket D (dashboard preserve)**: edited `parseland_eval/paths.py:7` `GOLD_CSV` → `human-goldie.csv`. Regenerated `gold-standard.json` (100 rows), `.seed.json` (50), `.holdout.json` (50) via `parseland_eval.build_gold` + `parseland_eval.split`. Heroku dashboard data path preserved.
- **Bucket E (7 KB)**: deleted pre-iteration prompts `ai-goldie-v0.md` and `ai-goldie-v1.md`. Kept v1.1–v1.4 (referenced by today's comparison report).
- **`.gitignore`**: added `.claude/` (Claude Code runtime state) and `.checkpoint/` (Cloud resumability state, regeneratable from CSVs).

### 11:00 CDT — Verification clean
- `pytest scripts/tests/`: **25/25 passed**.
- `split_train_holdout.py` re-run: holdout/train md5s identical (idempotent).
- `diff_goldie.py` re-run reproduces `summary-v1.4-holdout.json` and `disagreements-v1.4-holdout.md` **byte-for-byte** from `human-goldie.csv`.

### 10:58 CDT — v1.4 fair holdout cloud measurement (the missing piece)
- 50/50 DOIs extracted via browser-use Cloud v3 sessions API, Opus 4.7, concurrency=50, US residential proxy.
- Wall: ~22 min. Cost: $15.50 ($0.31/DOI). 36/50 first-attempt success, 4 with 1 retry, 10 with 3 retries (mostly bot-checked publishers).
- Per-field on the 50 shared DOIs: **authors 68% / rases 40% / corresponding 60% / abstract 64% / pdf_url 50% / overall 16%**.
- vs v1.1 baseline: **pdf_url +8 pp** (the "no URL construction from DOI" rule works); authors / corresponding / rases each **regressed ~10 pp** (11 KB → 8 KB trim cost recall on multi-author affiliation extraction).
- No version (v1.1, v1.2, v1.3, v1.4) clears the 95% per-field gate.
- Artifacts: `goldie/summary-v1.4-holdout.json`, `goldie/disagreements-v1.4-holdout.md`, `goldie/comparison-v1.1-to-v1.4-holdout.md`, `runs/holdout-v1.4/ai-goldie-1.csv`.

### 10:42 CDT — Gold-standard split regenerated from `human-goldie.csv`
- 100 rows, No 1–100. Train/holdout splits idempotent.
- **No 81 fix**: PLOS table DOI `10.1371/journal.pone.0192138.t002` (Taxicab-non-harvestable per CLAUDE.md) → parent article `10.1371/journal.pone.0192138`. 49/50 holdout DOIs unchanged from v1.{1,2,3} runs — comparability with prior baselines retained.

### 10:30 CDT — Verified `extract_batch_cloud.py` accepts the new schema
- `human-goldie.csv` adds a `resolved_links` column. The runner's `read_window` projects only `{No, DOI, Link}` so the extra column is silently ignored. No runner change needed.
- Smoke (2 DOIs): Russian Nota Bene (clean extraction, $0.45) + ScienceDirect (bot-checked, diagnostic Notes preserved). Confirms the runner end-to-end.

### Status at EOD
- **Phase C**: closed. v1.5 patch list filed in `goldie/insoluble-cases.md` (re-introduce v1.1's author/affiliation depth, keep v1.4's pdf_url discipline). Hard gate before Phase D — no auto-progression.
- **Blocker for Phase D**: user decision on whether to (a) lock v1.4 as-is and ship to 10K despite sub-95%, (b) iterate to v1.5, or (c) audit `human-goldie.csv` to mark the ~11 systematically-uncrawlable rows as expected-empty (would lift several fields meaningfully).
- **Cost burned today on Cloud**: $15.50 (50-DOI holdout). Remaining credit balance unread; check at <https://cloud.browser-use.com/bux> before Phase D.

---

## Template for tomorrow

```markdown
## YYYY-MM-DD — <theme of the day>

### HH:MM TZ — <accomplishment in active voice>
- bullet
- bullet

### Status at EOD
- What landed:
- Blocked on:
- Next:
```
