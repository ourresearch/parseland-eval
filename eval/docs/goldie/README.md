# Goldie CLI

Goldie CLI is the operator tool for sampling random DOIs, extracting page-presented
metadata, monitoring long runs, resuming interrupted work, and generating stakeholder-ready
reports.

Goldie is quality-first. Cost is recorded, projected, and reviewed, but extraction quality
is not reduced just to save money. Crossref is used only to choose random DOI strings.
Field values must come from DOI.org-resolved publisher pages, Taxicab/cache HTML, or
rendered-browser evidence.

## Clone And Setup

```bash
git clone https://github.com/ourresearch/parseland-eval.git
cd parseland-eval
git checkout feat/goldie-cli
uv run --project eval goldie --help
```

If `uv` is unavailable:

```bash
python3.11 -m venv eval/.venv
eval/.venv/bin/pip install -e "eval[dev]"
eval/.venv/bin/goldie --help
```

Create `eval/.env`. Do not commit it.

```bash
ANTHROPIC_API_KEY=
BROWSER_USE_API_KEY=
BROWSERBASE_API_KEY=
CDP_URL=http://localhost:9222
```

Required keys:

| Tier or command | Required key |
|---|---|
| `--tier cached` | `ANTHROPIC_API_KEY` |
| `--fallback-tier cloud` | `BROWSER_USE_API_KEY` |
| `--fallback-tier local_cdp` | `ANTHROPIC_API_KEY` and a local CDP Chrome session |
| `spike browserbase-fetch` | `BROWSERBASE_API_KEY` |

`goldie run` and `goldie random` default to cached Taxicab extraction plus cloud fallback.
If fallback credentials or SDK setup are missing, startup fails loudly. Use
`--fallback-tier none` or `--no-fallback` only as an explicit quality tradeoff.

## Quickstart

Run 100 random DOIs:

```bash
uv run --project eval goldie random --count 100 --name goldie-random-100
```

Prepare a 10K random DOI source without launching extraction:

```bash
uv run --project eval goldie prepare --count 10000 --name goldie-10k
```

Run extraction from a prepared source:

```bash
uv run --project eval goldie run \
  --source runs/goldie-10k-<sample-stamp>/source.csv \
  --corpus goldie-10k-<sample-stamp> \
  --tier cached \
  --fallback-tier cloud
```

Resume a stopped run:

```bash
uv run --project eval goldie resume --run runs/<corpus>-<run-stamp>
```

Monitor a run:

```bash
uv run --project eval goldie monitor --run runs/<corpus>-<run-stamp>
uv run --project eval goldie monitor --run runs/<corpus>-<run-stamp> --watch
```

Rebuild JSON and Markdown reports:

```bash
uv run --project eval goldie report \
  --run runs/<corpus>-<run-stamp> \
  --operator \
  --out runs/<corpus>-<run-stamp>/OPERATOR_REPORT.md
```

## Command Matrix

| Command | Purpose |
|---|---|
| `goldie sample --target N --out source.csv` | Primitive Crossref random DOI sampler |
| `goldie prepare --count N --name NAME` | Operator alias for sampling only |
| `goldie run --source source.csv --corpus NAME` | Primitive extraction run |
| `goldie random --count N --name NAME` | Operator alias for sample, run, and report |
| `goldie resume --run RUN_DIR` | Resume from manifest and checkpoints |
| `goldie monitor --run RUN_DIR --watch` | Live terminal monitor |
| `goldie report --run RUN_DIR --operator` | Rebuild `report.json` and Markdown operator report |
| `goldie bestof --run A --run B --out merged.csv` | Merge strongest evidence from completed runs |
| `goldie clean --dry-run` | Guarded clutter audit |

## Artifact Map

Extraction runs write to repo-root `runs/`:

```text
runs/<corpus>-<UTC>/
  manifest.json
  report.json
  OPERATOR_REPORT.md
  merged.csv
  batches/batch-001/ai-goldie.csv
  checkpoints/batch-001.partial.jsonl
  failures/batch-001.failures.jsonl
  logs/live-agent-events.ndjson
  live.html
```

| Artifact | Operator meaning |
|---|---|
| `manifest.json` | Rows, landed/failed counts, source CSV, tiers, fallback, costs, event paths |
| `report.json` | Structured completeness report and quality queues |
| `OPERATOR_REPORT.md` | GitHub/oxjob-readable UI report generated from manifest and report JSON |
| `merged.csv` | Final extraction CSV across all batches |
| `checkpoints/*.partial.jsonl` | Resume state; landed DOI rows are skipped on resume |
| `failures/*.failures.jsonl` | Failure records |
| `logs/live-agent-events.ndjson` | Append-only per-DOI events, live URLs, fallback/reharvest details |
| `live.html` | Local self-refreshing monitor page |

## What To Send After A Run

Send these to Shubh/Jason:

- run directory path;
- `OPERATOR_REPORT.md`;
- `manifest.json`;
- `report.json`;
- source CSV checksum if the source is not committed;
- clear launch recommendation: `ready`, `needs review`, or `blocked`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Missing `ANTHROPIC_API_KEY` | Add it to gitignored `eval/.env`; cached extraction needs it |
| Missing `BROWSER_USE_API_KEY` | Add it to `eval/.env`; cloud fallback needs it |
| Cloud fallback init failure | Fix credentials/SDK setup or explicitly pass `--fallback-tier none` |
| Taxicab no harvested HTML | Row is a failure-mode signal; do not backfill from metadata APIs |
| Bot-check rows | Inspect quality queues and live events; use browser evidence follow-up |
| Unresolved all-core-empty rows | Treat as a 10K blocker until explained or rerun |
| `uv` lockfile churn | Do not commit generated `eval/uv.lock` unless dependency policy changes |
| Protected `merged-FINAL.csv` changed | Stop and inspect; do not commit unless a gold-data update is approved |

## Accuracy Boundary

Random runs measure extraction completeness and failure modes. They do not prove absolute
accuracy. A 98% accuracy claim requires scored validation against audited truth, such as a
held-out gold set or a manually audited random subset.
