# PLAN.md

## AI Goldie Plan

### 0. Hard prerequisite — human goldie audit

The human goldie at `eval/goldie/human-goldie-v2-audited.csv` must be PERFECT before any AI run is reported. Especially CA (corresponding-author) — CDL paid $50k for that coverage; we must nail it.

**Audit workflow (USER, in browser, ~6–8 hr):**

1. Open `eval/goldie/audit-checklist.csv` (100 rows, landing pages pre-resolved by `eval/scripts/audit_helper.py`).
2. For each row: open `landing_page_url`, verify against `human-goldie-v1-pre-audit.csv`. Mark each `_ok` column Y/N with notes.
3. **Dedicated CA second sweep** over all 100 rows after the first pass — re-scan for envelope (✉), asterisk, "Corresponding author:" footnote. Mark all authors `false` if no signal exists.
4. Apply fixes to `human-goldie-v2-audited.csv` and commit per ~10 rows: `human goldie v2: rows N–M, M corrections (incl. K CA fixes)`.
5. After the audit lands, re-run `eval/scripts/split_train_holdout.py` to regenerate `train-50.csv` and `holdout-50.csv` from audited v2.

**Gate to Phase B:** all `_ok` columns filled in `audit-checklist.csv`; v2 ≠ v1.

### 1. Baseline the current files

- Treat `eval/goldie/train-50.csv` as the tuning set.
- Treat `eval/goldie/holdout-50.csv` as sealed validation until the prompt is ready.
- Preserve `eval/goldie/human-goldie-v1-pre-audit.csv` as the frozen audit trail.
- Do not hand-edit generated run JSON or canonical gold outputs.

### 2. Fix schema alignment before judging accuracy

The v1 prompt and runner now align with the raw author schema better than v0.

Completed on 2026-04-27:
- Added `eval/prompts/ai-goldie-v1.md`.
- Added `rasses` and `corresponding_author` to the runner output model.
- Converted old `affiliations` output into raw gold-style `rasses`.
- Normalized semantically absent `N/A`, blank, and null values in the diff script.
- Ensured `PDF URL` comparison treats `N/A` as absent rather than as a literal URL.

Still needed:
- Add final CSV batch export that preserves the desired raw CSV sentinels.
- Add the `ai-goldie-<n>.csv` batch naming CLI surface.

### 3. Prompt tuning loop on rows 1-50

- Start from `eval/prompts/ai-goldie-v0.md`.
- Make a v1 prompt that explicitly mirrors the raw gold schema.
- Teach the prompt to inspect metadata sources in this order:
  1. DOI resolver and final publisher landing URL.
  2. HTML head meta tags such as `citation_author`, `citation_abstract`, `citation_pdf_url`, and JSON-LD.
  3. Visible article body text, author blocks, affiliation popovers, tabs, and "show more" controls.
  4. PDF link only when visible or exposed in metadata.
- Require concise notes when the page is paywalled, bot-checked, broken, non-English, or only partially extractable.
- Run small smoke tests first, then all 50 training rows.
- Use disagreement reports to revise the prompt, not the holdout.

### 4. Holdout validation on rows 51-100

- Run `eval/scripts/run_ai_goldie.py` against `eval/goldie/holdout-50.csv` only when the prompt is ready.
- Use `--allow-holdout` only for that final validation run.
- Compare with `eval/scripts/diff_goldie.py`.
- Proceed only if overall and per-critical-field agreement clear the 95% target.
- If validation fails, document failure categories and choose a new tuning strategy without repeatedly optimizing against the same holdout.

### 5. Scale to 10,000 DOI rows

- Process 100 DOI rows per batch.
- Emit candidate files:
  - `ai-goldie-1.csv` for rows 1-100.
  - `ai-goldie-2.csv` for rows 101-200.
  - `ai-goldie-3.csv` for rows 201-300.
  - Continue until 10,000 DOI rows are covered.
- Store run metadata beside each batch: prompt version, model, browser backend, timestamp, cost, duration, error count, and per-row notes.
- Human review happens batch by batch before any candidate becomes accepted gold.

### 6. Recommended backend after internet check

Primary recommendation:
- Use Browser Use with structured output and real browser state.
- For local pilots, continue with browser-use plus real Chrome over CDP.
- For scale, evaluate Browser Use Cloud v3 because it has first-class structured output, sessions, profiles, model selection, and task metadata.

Model recommendation:
- First benchmark `claude-sonnet-4.6` because Browser Use docs currently say it is the model they optimize for most.
- Also benchmark `gpt-5.4-mini` as the cost/latency challenger.
- Use the cheaper model only if it still clears the 95% threshold.

Concurrency recommendation:
- Use separate browser sessions or browser instances per worker.
- Avoid sharing a single browser object across many concurrent agents as the production design.
- Start with 2-4 concurrent headful workers locally; increase only after bot-check rate and accuracy remain stable.

Fallbacks:
- Use deterministic HTTP/HTML metadata extraction before agentic browsing when citation meta tags are complete.
- Use full browser agents only when deterministic extraction is incomplete or ambiguous.
- Keep proxies as publisher-specific fallbacks, not default behavior.

### 7. Commands already present

Smoke prompt extraction:

```bash
eval/.venv/bin/python eval/scripts/run_ai_goldie.py \
  --prompt eval/prompts/ai-goldie-v1.md \
  --input eval/goldie/train-50.csv \
  --limit 5
```

Training run:

```bash
eval/.venv/bin/python eval/scripts/run_ai_goldie.py \
  --prompt eval/prompts/ai-goldie-v1.md \
  --input eval/goldie/train-50.csv
```

Final holdout validation:

```bash
eval/.venv/bin/python eval/scripts/run_ai_goldie.py \
  --prompt eval/prompts/ai-goldie-v1.md \
  --input eval/goldie/holdout-50.csv \
  --allow-holdout
```

Diff:

```bash
eval/.venv/bin/python eval/scripts/diff_goldie.py \
  --human eval/goldie/human-goldie-v2-audited.csv \
  --ai runs/<ai-goldie-output>.json \
  --output-md eval/goldie/disagreements-<run>.md \
  --output-summary eval/goldie/summary-<run>.json
```

### 8. 10K production extraction (Phase D + E)

**Locked stack:** browser-use **Cloud Tasks API, Business tier** ($299/mo, 200 concurrent). 10K wall ~38 min, ~$1,310 all-in. Local 4-Chrome path is the iteration tool, not the production tool.

**Cadence:** hybrid — extract batch 1, gate on user review, then burn down 2–100 in parallel.

#### Phase D — Sample 10K Crossref DOIs

`eval/scripts/sample_10k_dois.py` (extends `sample_50_random_dois.py`):

```bash
eval/.venv/bin/python eval/scripts/sample_10k_dois.py \
  --output eval/data/ai-goldie-source-10k.csv \
  --target 10000
```

- Pulls from Crossref `/works?sample=N` in batches of 1,000 (Crossref's max sample size).
- De-dupes against `eval/goldie/human-goldie-v1-pre-audit.csv` DOIs.
- Filters out non-article types (`book`, `dataset`, `journal-issue`, `report-component`).
- Output schema matches gold-standard: `No, DOI, Link` (Authors / Abstract / etc. blank, populated by Phase E).
- Idempotent. `--force` to rebuild.

**Gate to Phase E:** `wc -l` = 10001 (header + 10000 rows); zero overlap with the 100-row gold.

#### Phase E — Cloud batch extraction

`eval/scripts/extract_batch_cloud.py` (NEW):

Smoke (Phase E.1) — single batch, then user review gate:

```bash
eval/.venv/bin/python eval/scripts/extract_batch_cloud.py \
  --prompt eval/prompts/ai-goldie-vLOCK.md \
  --source eval/data/ai-goldie-source-10k.csv \
  --output-dir eval/data \
  --batches 1 \
  --concurrency 100
```

Burn-down (Phase E.2) — batches 2–100:

```bash
eval/.venv/bin/python eval/scripts/extract_batch_cloud.py \
  --prompt eval/prompts/ai-goldie-vLOCK.md \
  --source eval/data/ai-goldie-source-10k.csv \
  --output-dir eval/data \
  --start-batch 2 --batches 99 \
  --concurrency 200
```

Behavior:
- Reads `ai-goldie-source-10k.csv` in 100-DOI windows; batch N covers rows `(N-1)*100 + 1 .. N*100`.
- POSTs each DOI to `https://api.browser-use.com/api/v2/tasks` with `task=` (locked prompt + DOI URL) and `structuredOutput=` (v1 record_extraction JSON Schema as a string).
- Polls task status until terminal (4h cap per task).
- Writes `eval/data/ai-goldie-N.csv` in gold-standard column order; Authors is JSON-encoded string.
- Resumable: `eval/data/.checkpoint/ai-goldie-N.partial.jsonl` is appended per completed DOI; re-runs skip already-landed DOIs.
- Failure log: `eval/data/ai-goldie-N.failures.jsonl` with task_id + DOI + error + retry count. Blank rows in the CSV are exactly the rows in the failures log.
- Retry: N=3 retries with exponential backoff (10s, 60s, 300s). After exhaustion, leave row blank.
- Anti-bot: on `has_bot_check=true`, retry once with `--proxy residential` (Cloud option, $5/GB).
- Atomic CSV writes: `.tmp` + rename.

**Gate per batch:** ai-goldie-N.csv has 100 rows in correct schema; every blank row appears in failures.jsonl; `failures.jsonl size + filled rows = 100`.

#### Phase F — User review (per batch)

For each `ai-goldie-N.csv`: open in editor, scan for missing/wrong fields, edit in place, commit:
```
ai-goldie batch N: review pass — <K corrections>
```
