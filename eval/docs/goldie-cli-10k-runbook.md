# Goldie CLI 10K Runbook

This is the operator guide for running Goldie CLI from a fresh clone. It covers
sampling random Crossref DOIs, extracting page-presented metadata, monitoring long
runs, resuming interrupted runs, and generating reports.

Goldie is quality-first. Cost is recorded in manifests and reports, but do not
disable quality tiers only to save money. Crossref is used only to choose random
DOIs. Field values must come from DOI.org-resolved pages, Taxicab/cache HTML, or
rendered-browser evidence.

## Quickstart

### Clone And Install

```bash
git clone https://github.com/ourresearch/parseland-eval.git
cd parseland-eval
git checkout feat/goldie-cli

uv run --project eval goldie --help
```

If `uv` is not installed, install it first or use a Python 3.11 virtualenv with
the `eval` package installed editable:

```bash
python3.11 -m venv eval/.venv
eval/.venv/bin/pip install -e "eval[dev]"
eval/.venv/bin/goldie --help
```

### Configure Credentials

Create `eval/.env`. Do not commit it.

```bash
cat > eval/.env <<'EOF'
ANTHROPIC_API_KEY=
BROWSER_USE_API_KEY=
BROWSERBASE_API_KEY=
EOF
```

Required keys by tier:

| Command | Required key |
|---|---|
| `goldie run --tier cached` | `ANTHROPIC_API_KEY` |
| `goldie run --fallback-tier cloud` | `BROWSER_USE_API_KEY` |
| `goldie run --fallback-tier local_cdp` | `ANTHROPIC_API_KEY` plus a Chrome CDP session |

`goldie run` defaults to cached Taxicab extraction plus cloud fallback. If the
cloud key or SDK setup is missing, the run fails at startup instead of silently
downgrading.

## Run 100 Random DOIs

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
SOURCE_DIR="runs/goldie-random-100-${STAMP}"
SOURCE_CSV="${SOURCE_DIR}/source.csv"
CORPUS="goldie-random-100-${STAMP}"

mkdir -p "$SOURCE_DIR"

uv run --project eval goldie sample \
  --target 100 \
  --out "$SOURCE_CSV" \
  --gold eval/human-goldie.csv

uv run --project eval goldie run \
  --source "$SOURCE_CSV" \
  --corpus "$CORPUS" \
  --tier cached \
  --fallback-tier cloud
```

`goldie run` prints the generated run directory. It will look like:

```text
runs/goldie-random-100-<sample-stamp>-<run-stamp>/
```

Capture it for monitoring and reporting:

```bash
RUN_DIR=$(ls -td "runs/${CORPUS}-"* | head -1)
uv run --project eval goldie report --run "$RUN_DIR"
```

## Run 10K Random DOIs

Run random-100 first and review its report before starting 10K. When there is no
unresolved infrastructure blocker, use the same CLI with a larger target:

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
SOURCE_DIR="runs/goldie-10k-${STAMP}"
SOURCE_CSV="${SOURCE_DIR}/source.csv"
CORPUS="goldie-10k-${STAMP}"

mkdir -p "$SOURCE_DIR"

uv run --project eval goldie sample \
  --target 10000 \
  --out "$SOURCE_CSV" \
  --gold eval/human-goldie.csv

uv run --project eval goldie run \
  --source "$SOURCE_CSV" \
  --corpus "$CORPUS" \
  --tier cached \
  --fallback-tier cloud
```

Monitor in another terminal:

```bash
RUN_DIR=$(ls -td "runs/${CORPUS}-"* | head -1)
uv run --project eval goldie monitor --run "$RUN_DIR"
uv run --project eval goldie monitor --run "$RUN_DIR" --watch
```

Generate the final report:

```bash
uv run --project eval goldie report --run "$RUN_DIR"
```

## Resume A Stopped Run

Sampling is resumable through `<source>.partial.jsonl`. Re-run the same sample
command to continue accepting DOIs. Pass `--force` only when you intentionally
want to discard the previous sample state and rebuild the source CSV.

```bash
uv run --project eval goldie sample \
  --target 10000 \
  --out "$SOURCE_CSV" \
  --gold eval/human-goldie.csv
```

Extraction is resumable through per-batch checkpoints inside the generated run
directory. Re-run with the same source and corpus, plus `--resume <run-dir>`:

```bash
uv run --project eval goldie run \
  --source "$SOURCE_CSV" \
  --corpus "$CORPUS" \
  --tier cached \
  --fallback-tier cloud \
  --resume "$RUN_DIR"
```

Goldie reopens the run directory, reads `checkpoints/batch-*.partial.jsonl`, and
skips DOIs already landed in those checkpoints.

## Artifacts

Each extraction run writes:

```text
runs/<corpus>-<UTC>/
  manifest.json
  report.json
  merged.csv
  batches/batch-001/ai-goldie.csv
  checkpoints/batch-001.partial.jsonl
  failures/batch-001.failures.jsonl
  logs/live-agent-events.ndjson
  live.html
```

Artifact meanings:

| Artifact | Meaning |
|---|---|
| `manifest.json` | Run metadata: rows, landed count, failed count, tier cost, fallback cost, total cost, Taxicab reharvest stats |
| `report.json` | Operator report: field presence, quality queues, fallback telemetry, extraction completeness |
| `merged.csv` | Final Goldie CSV across all batches |
| `batches/*/ai-goldie.csv` | Per-batch extraction output |
| `checkpoints/*.partial.jsonl` | DOI-keyed landed rows used for resume |
| `failures/*.failures.jsonl` | Transparent failure records |
| `logs/live-agent-events.ndjson` | Append-only live event stream with DOI/tier/status telemetry |
| `live.html` | Local self-refreshing monitor page for the latest run events |

## How To Interpret Reports

Random DOI runs without a holdout are unscored summaries. They measure extraction
coverage and failure modes. They do not prove absolute accuracy.

Key fields:

| Report field | Meaning |
|---|---|
| `field_presence` | Per-field non-empty counts for authors, rases, corresponding author, abstract, and PDF URL |
| `field_missing` | Per-field missing counts |
| `missing_field_combinations` | Common missing-field patterns |
| `quality_focus.counts.all_core_empty` | Rows with authors, abstract, and PDF URL all empty |
| `quality_focus.counts.terminal_flagged_empty` | Empty rows explained by terminal flags such as broken DOI or no article metadata |
| `quality_focus.counts.bot_check_empty` | Empty rows that hit bot checks |
| `quality_focus.counts.unresolved_all_core_empty` | Empty rows without a terminal explanation; these block 10K confidence |
| `quality_focus.counts.extraction_miss` | Rows that still look like extraction misses after available tiers |
| `fallback` | Fallback tier, attempted count, returned count, used count, fields filled, and fallback cost |
| `total_cost_usd` | Tier-1 cost plus fallback cost |

Use the report to decide whether the next step is a 10K run, a targeted rerun,
or a publisher-specific quality fix.

## Accuracy Gate

Do not call a random extraction run "98% accurate" by itself. A random run can
show that fields are present and quality queues are small, but verified accuracy
requires audited truth.

The 98% gate must be one of:

- scored against an existing audited holdout that matches the current schema;
- scored against a manually audited subset from the random-100 output;
- scored against a separate validation sample created before tuning against it.

Every operator report should separate:

- extraction completeness;
- field presence;
- quality queue triage;
- verified field accuracy;
- unresolved rows needing human or publisher-specific follow-up.

## What To Send To Shubh Or Jason

After a random-100 or 10K run, send:

- run directory path;
- `manifest.json` summary: rows, landed, failed, tier costs, fallback costs, total cost;
- `report.json` summary: field presence and quality queue counts;
- top unresolved DOI list from `quality_focus`;
- whether the output is unscored coverage or scored accuracy;
- any credential, Taxicab, bot-check, or fallback blocker.

## Troubleshooting

### Missing `ANTHROPIC_API_KEY`

Symptom: cached tier fails startup with a missing credential error.

Fix: add `ANTHROPIC_API_KEY` to `eval/.env`.

### Missing `BROWSER_USE_API_KEY`

Symptom: `--fallback-tier cloud` fails startup.

Fix: add `BROWSER_USE_API_KEY` to `eval/.env`, or explicitly opt out with
`--fallback-tier none` only when you mean to run without rendered-browser fallback.

### Cloud Fallback Init Failure

Symptom: the cloud backend cannot initialize even though a key exists.

Fix: check browser-use SDK installation through `uv run --project eval python -c
"import browser_use_sdk"` and rerun. Do not ignore this: the fail-loud behavior
prevents a misleading green tier-1-only run.

### Taxicab No Harvested HTML

Symptom: notes or failures mention `taxicab: no harvested html`.

Fix: review the DOI in the report. If cached HTML is unavailable, cloud fallback
may still recover the row. If both fail, keep it in the unresolved queue instead
of silently dropping the DOI.

### Bot-Check Rows

Symptom: report counts `bot_check_empty`.

Fix: inspect `live.html` and `logs/live-agent-events.ndjson` for browser-use live
URLs or screenshot URLs. Treat bot-check rows as infrastructure/fetch blockers,
not parser wins.

### Unresolved All-Core-Empty Rows

Symptom: report counts `unresolved_all_core_empty`.

Fix: these are the rows to triage before scaling. Rerun with cloud fallback,
inspect DOI.org/publisher evidence, and add terminal flags only when the page
really lacks article metadata or is truly broken.

### `uv` Lockfile Churn

Symptom: `eval/uv.lock` appears untracked after a command.

Fix: remove it unless the project intentionally adopts it:

```bash
rm -f eval/uv.lock
```

### Protect `merged-FINAL.csv`

`eval/data/merged-FINAL.csv` is a protected prior artifact. Do not sample into it,
do not overwrite it, and do not use it as the random-100 or 10K output path.
Use `runs/<corpus>/source.csv` for new source files and `runs/<corpus>-<UTC>/`
for extraction artifacts.

