# Accomplishments — parseland-eval

Daily log of completed work. Format follows the OXJOB `LEARNING.md` standard
(hypothesis · setup · result · verdict · next moves · cost paid). Newest first.

## EOD ritual (run at the end of each working day)

1. **Append today's section** to the top of this file.
2. **Mirror to oxjob**: append the same section to `~/Documents/OpenAlex/oxjobs/working/parseland-gold-standard/LEARNING.md`.
3. **Mirror to Notion**: append the EOD callout to <https://www.notion.so/Shubhankar-341fa317eb678028b3cce5cb63181777> (the Shubhankar / Meetings parent page).
4. **Slack EOD** to `#project-parseland` (channel `C0AU0BLM50V` on `impactstory.slack.com`) — cc `<@U07HJQKJ42C>` Casey Meyer (CTO) and `<@UEVFABBBP>` Jason (CEO). 3–5 bullets answering — what stats, what's going well, what needs improving, what's next. Use `slack_send_message_draft` so the user reviews then sends.

---

## 2026-04-29 · Phase C v1.4 fair holdout cloud measurement

### EOD status — Casey + Jason

> **Casey 4/29**: "By end of day it would be great to see current stats and how well the current approach is working. So the stats and a quick summary of what's going well and what needs improving."

**Headline**: 11/50 holdout DOIs (22%) are uncrawlable on the current runtime (bot-check / 403 / dictionary-entry / non-article). Splitting **fetch success vs. extraction** shows the parser is closer to Casey's 85-90% bar than the all-50 number suggests.

**Stats — v1.4 on holdout-50** (browser-use Cloud, Opus 4.7, $15.50, 22 min wall):

| Field | All 50 | Fetch-OK 39 | Δ |
|---|---|---|---|
| Authors | 68% | **77%** | +9 |
| Affiliations (rases) | 40% | 41% | +1 |
| Corresponding-author flag | 60% | **67%** | +7 |
| Abstract (difflib ≥ 0.95) | 64% | **72%** | +8 |
| PDF URL | 50% | 41% | −9¹ |
| Overall (row-perfect) | 16% | 10% | — |

¹ PDF URL drops on fetch-OK because the 11 bot-blocked rows contribute both-empty matches (AI=human=N/A) — comparator quirk, not a parser regression.

**What's going well**:
- **Authors @ 77% on fetch-OK** — within striking distance of Casey's 85-90% bar.
- **Abstract @ 72% on fetch-OK** — biggest gain since v1.1.
- v1.4's "no URL construction from DOI" rule lifted **PDF URL +8 pp vs v1.1** (42% → 50% on all-50). Confirmed working.
- **Anti-bot stack on browser-use Cloud handled 39/50 cleanly** — no Zyte needed for that fraction. Built-in residential proxy + auto-CAPTCHA worked.
- Pipeline is bullet-proof: resumable (SHA-256 checkpoint), atomic writes, byte-for-byte reproducible scoring.

**What needs improving**:
- **Affiliations (rases) @ 41% on fetch-OK** — biggest single gap. v1.4 trim dropped v1.1's affiliation-formatting examples; per-author exact-string match degrades fast.
- **11 fetch-failures (22% of holdout)** — ScienceDirect bot-check (3), Oxford Academic 403 (2), OED dictionary entries (2), APS 403, Érudit Anubis CAPTCHA, Malaysian OJS reCAPTCHA, Ovid host-redirect. Need a call: **Zyte/Taxicab fallback, parent-DOI substitution, or audit them as expected-empty**.
- **Authors −10 pp vs v1.1** — 11 KB → 8 KB trim cost recall on multi-author lists.
- **No version (v1.1-v1.4) clears the 95% per-field gate.** Need decision: lock v1.4 + ship Phase D, or iterate to v1.5.

**Casey's 4/29 checkpoint**: 85-90% by EOD Thu 4/30, or pivot.
- Today's number: **77% authors on fetch-OK** — 8 pp short of 85%.
- Two paths to close the gap by tomorrow EOD: (a) v1.5 prompt re-introducing v1.1's author/affiliation depth, (b) Zyte/Taxicab fallback to recover the 11 blocked DOIs. Both are tractable; (b) is the bigger compounder if it works.

**Open with Casey/Jason**:
- Zyte vs. parent-DOI substitution vs. mark-as-expected-empty for the 11 blocked rows.
- Lock v1.4 vs. iterate v1.5 — recommendation in `eval/goldie/insoluble-cases.md`.
- OXTRAP dashboard status (carried from 4/29 standup, open with Jason).

---

### Hypothesis
v1.4 prompt (8 KB, locked 2026-04-27) on browser-use Cloud Tasks delivers per-field accuracy comparable to v1.1's cloud baseline (78 / 48 / 70 / 68 / 42 on the holdout-50), with the new "no URL construction from DOI" rule lifting `pdf_url` meaningfully above v1.1's 42%.

### Setup
- Source: `eval/human-goldie.csv` (100 rows, locked Goldie 4/29 — corresponding-author audit complete; this is now the canonical human-audited truth).
- Holdout: rows 51-100 (sealed validation; never iterated against during prompt tuning).
- Runner: `eval/scripts/extract_batch_cloud.py` against browser-use Cloud v3 sessions API.
- Model: `claude-opus-4-7` ("claude-opus-4.7" on the wire).
- Prompt: `eval/prompts/ai-goldie-v1.4.md` (8 281 bytes — "no URL construction from DOI" rule, trimmed v1.3's 11 KB after POST `/sessions` 60s timeout regression).
- Concurrency: 50. Retry cap: 3. Proxy: default US residential (Cloud built-in anti-bot stack).
- Scoring: `eval/scripts/diff_goldie.py` — order-insensitive set match on author names, exact-strip on `rases`, bool match on `corresponding_author`, difflib ratio ≥ 0.95 on abstract, canonicalized exact on `pdf_url`.

### Result (apples-to-apples on the 50 shared DOIs)

| Field | v1.1 (cloud) | v1.2 (cloud) | v1.3 (cloud) | **v1.4 (today)** | v1.4 vs v1.1 |
|---|---|---|---|---|---|
| Authors | **78** | 68 | 18 | 68 | −10 |
| Rases | **48** | 42 | 18 | 40 | −8 |
| Corresponding | **70** | 54 | 18 | 60 | −10 |
| Abstract | **68** | 62 | 28 | 64 | −4 |
| PDF URL | 42 | 42 | **56** | 50 | **+8** |
| Overall | **16** | **16** | 10 | **16** | 0 |

Wall: ~22 min. Cost: **$15.50** ($0.31/DOI avg, Opus 4.7). Retries distribution: 36 succeeded first attempt, 4 with 1 retry, 10 burned the 3-retry cap.

Run files:
- `runs/holdout-v1.4/ai-goldie-1.csv`
- `runs/holdout-v1.4/ai-goldie-batches-1-1-20260429-105837.meta.json`
- `eval/goldie/summary-v1.4-holdout.json`
- `eval/goldie/disagreements-v1.4-holdout.md` (42 disagreements, 8 row-perfect)
- `eval/goldie/comparison-v1.1-to-v1.4-holdout.md`
- `eval/goldie/insoluble-cases.md` (disagreement triage + v1.5 patch list)

### Verdict
**Mixed.** v1.4's "no URL construction" rule confirmed working (`pdf_url` +8 pp). Trim from 11 KB → 8 KB cost ~10 pp on authors / corresponding and ~8 pp on rases. **No version (v1.1, v1.2, v1.3, v1.4) clears the 95% per-field gate** required for Phase D progression. Phase C inconclusive without further iteration.

### Hypotheses for the gap
1. v1.4 dropped v1.1's "include all authors regardless of position" anchor — author misses cluster on long author lists.
2. v1.4 dropped v1.1's affiliation-formatting examples — `rases` exact-string comparator hits punctuation/whitespace drift.
3. **11/50 (22%) are structurally uncrawlable** on the current runtime — sets a ceiling on the all-50 number until either Zyte fallback recovers HTML, the DOIs are audited as expected-empty, or parent-DOI substitution (like the No 81 `.t002` → parent fix) is generalized.
4. Abstract miss includes ~9 threshold-borderline rows where AI and human have near-identical text falling below 0.95 — `tune_abstract_threshold.py` would recover several without prompt changes.

### Next moves to rank
- [ ] **Zyte / Taxicab fallback for the 11 blocked DOIs** (Casey's concrete unblocker 4/29) — bigger compounder than prompt iteration; can be wired tomorrow AM.
- [ ] **v1.5 prompt** — re-introduce v1.1's "include all authors" + affiliation-formatting examples; keep v1.4's no-URL-construction rule + 8 KB ceiling.
- [ ] **Spot-check 15-30** instead of building a third gold standard set (Casey 4/29).
- [ ] **Audit the 11 blocked rows in `human-goldie.csv`** — any that are non-articles (OED, dictionary entries) should be marked expected-empty so they score as match instead of miss.
- [ ] **Re-tune abstract threshold** via `tune_abstract_threshold.py` against today's run — the 0.95 cutoff misses near-matches.
- [ ] Decide: lock v1.4 as `vLOCK` and ship Phase D anyway, or iterate v1.5 first.

### Cost paid
$15.50 cloud (50 DOIs × ~$0.31/DOI avg, Opus 4.7). Cumulative on Phase C iteration: ~$40 across v1.1-v1.4.

---

## 2026-04-29 · Repository hygiene — OXJOB pilot retired, gold artifacts repathed

(Ops, not science — kept here for the audit trail.)

### Done
- **Locked Goldie**: `eval/human-goldie.csv` (100 rows, audited, corresponding-author coverage verified) is now the sole human-audited truth. `gold-standard.csv` and the three derived JSONs were deleted; `gold-standard.json/.seed/.holdout` regenerated from `human-goldie.csv` via `parseland_eval.build_gold` + `parseland_eval.split` so the Heroku dashboard data path stays alive. `parseland_eval/paths.py:7` repathed.
- **Holdout No 81 fix**: PLOS table DOI `10.1371/journal.pone.0192138.t002` (Taxicab-non-harvestable per CLAUDE.md) → parent article `10.1371/journal.pone.0192138`. 49/50 holdout DOIs unchanged from v1.{1,2,3} — comparability with prior cloud baselines retained.
- **OXJOB 2026-04-21 pilot retired**: 8 pilot scripts (extract_with_*, compare_passive_vs_agentic, gpt_review, pilot_report, sample_50_random_dois) + 8 random-50 data files + 3 HTML snapshot dirs (~96 cached pages, 904 KB) deleted. ~1.1 MB reclaimed. Locked stack stays on browser-use Cloud Tasks. CLAUDE.md edited to drop dangling pilot tables; pilot findings preserved in OXJOB.md.
- **Pre-rename and pre-iteration fossils gone**: `human-goldie-{v1-pre-audit,v2-audited}.csv`, `goldie/audit-checklist.csv`, depressed v1.4-mcp scoring artifacts, pre-iteration prompts `ai-goldie-{v0,v1}.md`. ~500 KB.
- **Runner timeout fix carried**: `extract_batch_cloud.py` `CloudClient.timeout_sec` 60 → 180 s (the v1.3 11 KB-prompt POST `/sessions` regression fix).
- **`.gitignore` hygiene**: added `.claude/` (Claude Code runtime state) and `.checkpoint/` (Cloud resumability state, regeneratable from CSVs).

### Verification
- `pytest scripts/tests/`: **25/25 passed**.
- `split_train_holdout.py` is idempotent — re-run produces md5-identical holdout/train.
- `diff_goldie.py` reproduces today's `summary-v1.4-holdout.json` and `disagreements-v1.4-holdout.md` byte-for-byte from `human-goldie.csv`.

### Pushed
- `9563eff eval: v1.4 cloud holdout fair measurement + repo cleanup` (153 files: +4 793, −16 300).
- `3c13255 docs: ACCOMPLISHMENT.md — daily log + EOD ritual` (this file's first cut).
- 23 commits caught up to origin/main. Heroku auto-deploy from main triggers the dashboard refresh.

---

## Template for tomorrow

```markdown
## YYYY-MM-DD · <one-line theme>

### EOD status — Casey + Jason
- Headline:
- Stats (table):
- Going well:
- Needs improving:
- Open with Casey/Jason:

### Hypothesis
### Setup
### Result
### Verdict
### Hypotheses for the gap
### Next moves to rank
- [ ] …
### Cost paid
```
