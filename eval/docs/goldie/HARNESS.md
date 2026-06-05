# Goldie Harness

Goldie CLI is a separate extraction harness inside `parseland-eval`. It is not the deployed
dashboard runner and it does not mutate Parseland production parser code.

## Data Flow

```text
Crossref sample API
  -> DOI strings only
  -> source.csv with DOI.org resolver links
  -> cached Taxicab extraction
  -> Taxicab live reharvest when cached HTML is thin
  -> rendered-browser fallback for still-empty rows
  -> merged.csv
  -> report.json
  -> OPERATOR_REPORT.md
```

Crossref metadata is forbidden as field evidence. The source CSV intentionally starts with
blank extraction fields.

## Source Schema

Goldie source and output CSVs use the full Goldie schema from `eval/goldie_cli/schema.py`.
Minimum operator inputs are:

| Column | Meaning |
|---|---|
| `No` | 1-based row number |
| `DOI` | Lowercase DOI |
| `Link` | `https://doi.org/<doi>` resolver URL |

All extraction fields are blank in sampled sources and filled only by extraction evidence.

## Tiers

| Tier | Role |
|---|---|
| `cached` | Default entry tier: Taxicab cached HTML plus Taxicab live reharvest |
| `cloud` | Browser-use Cloud rendered-browser fallback |
| `local_cdp` | Local Chrome/CDP rendered-browser fallback |
| `none` | Explicit fallback opt-out only |

`goldie run` and `goldie random` default to `--tier cached --fallback-tier cloud`.
Fallback is built at startup so missing credentials fail before the paid/long run starts.

## Resume Contract

Sampling resume:

- `<source.csv>.partial.jsonl` stores accepted DOI state.
- Re-run the same `goldie sample` or `goldie prepare` command to continue.
- Use `--force` only to discard prior sample state.

Extraction resume:

- `checkpoints/batch-*.partial.jsonl` stores landed DOI rows.
- `goldie run --resume RUN_DIR` reopens the run directory.
- `goldie resume --run RUN_DIR` reads source/corpus/tier/fallback from `manifest.json`.
- If an old manifest lacks `source_csv`, the CLI prints the primitive `goldie run --source ... --resume` form.

## Manifest Contract

New run manifests include:

| Key | Meaning |
|---|---|
| `source_csv` | Source CSV used for extraction |
| `source_csv_abs` | Absolute source CSV path for local recovery |
| `corpus` | Corpus name used in run directory naming |
| `tier` | Entry tier |
| `fallback_tier` | Requested fallback tier |
| `prompt_version` | Prompt version loaded at run start |
| `started_at_utc` / `completed_at_utc` | Run timing |
| `rows`, `landed`, `failed` | Row counts |
| `tier1_landed`, `tier1_failed` | Pre-fallback result counts |
| `fallback` | Fallback tier, attempted/returned/used, filled fields, cost |
| `cost_usd`, `total_cost_usd` | Tier-1 and all-tier costs |
| `report_json`, `operator_report` | Report artifacts |
| `events`, `live_html`, `merged_csv` | Run artifacts |

Older manifests may not include every key. Reports must remain backward compatible.

## Report Contract

`report.json` is structured data for automation. `OPERATOR_REPORT.md` is generated UI for
GitHub and oxjobs.

Important report concepts:

| Concept | Meaning |
|---|---|
| `field_presence` | Non-empty field counts |
| `field_missing` | Missing field counts |
| `quality_focus` | Bounded DOI queues for follow-up |
| `all_core_empty` | Authors, abstract, and PDF URL all empty |
| `unresolved_all_core_empty` | Empty rows without terminal explanation |
| `extraction_miss` | Rows that still look like extraction misses |
| `fallback` | Attempted/returned/used counts and cost |
| `total_cost_usd` | Tier-1 plus fallback cost |

The Markdown operator report maps these values into status, metrics, quality queues,
10K projection, exact follow-up commands, and the accuracy boundary.

## Validation Boundary

Unscored random runs are not accuracy evaluations. They answer:

- did extraction complete;
- which fields are present;
- where did fallback help;
- what quality queues remain;
- what cost/runtime should a larger run expect.

Verified accuracy requires audited truth and scored comparison.
