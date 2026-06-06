# Goldie Latest Parseland 10K Source Readiness

Generated: 2026-06-06T01:56:50Z
Completed: 2026-06-06T02:47:00Z
Purpose: fresh random DOI source for testing the latest Parseland/Goldie extraction path.

## Artifact

```text
runs/goldie-latest-parseland-10k-20260606T015650Z-20260606T015904Z/source.csv
```

Corpus name for extraction:

```text
goldie-latest-parseland-10k-20260606T015650Z-20260606T015904Z
```

## Sampling Command

The final successful resume command was:

```bash
uv run --project eval goldie prepare \
  --count 10000 \
  --name goldie-latest-parseland-10k-20260606T015650Z \
  --out runs/goldie-latest-parseland-10k-20260606T015650Z-20260606T015904Z/source.csv \
  --gold eval/human-goldie.csv
```

Crossref had repeated read timeouts during the run. The sampler now retries transient
Crossref fetch failures and preserves accepted DOI state in:

```text
runs/goldie-latest-parseland-10k-20260606T015650Z-20260606T015904Z/source.csv.partial.jsonl
```

## Verification

| Check | Result |
|---|---:|
| Source CSV lines | 10001 |
| Corpus DOI rows | 10000 |
| Partial resume rows | 10000 |
| Full Goldie schema | true |
| Duplicate DOIs | 0 |
| Human-gold overlap | 0 |
| DOI.org resolver links | true |
| First DOI | `10.2495/itie20131432` |
| Last DOI | `10.1007/springerreference_88625` |

Protected file check:

```text
b33dfd256fddf44b32c5543e11d6997256efcb24deaf9dc9323188bd22adcc43  eval/data/merged-FINAL.csv
```

## Checksums

```text
8acb9b56c06fae4fb4b9fa337f4d91c2ef8e518d1adea49a3e073b0d7cdcb70b  runs/goldie-latest-parseland-10k-20260606T015650Z-20260606T015904Z/source.csv
441526a1297a3c02888e912757ec666f74c1192d45ca4145a8b58ddbb7ebf3c9  runs/goldie-latest-parseland-10k-20260606T015650Z-20260606T015904Z/source.csv.partial.jsonl
```

## Best Extraction Path

Use the quality-first cascade: cached Taxicab extraction, Taxicab live reharvest inside the
cached tier, then rendered-browser cloud fallback over empty-field rows.

Start extraction:

```bash
uv run --project eval goldie run \
  --source runs/goldie-latest-parseland-10k-20260606T015650Z-20260606T015904Z/source.csv \
  --corpus goldie-latest-parseland-10k-20260606T015650Z-20260606T015904Z \
  --tier cached \
  --fallback-tier cloud \
  --concurrency 200 \
  --batch-concurrency 4
```

Monitor separately:

```bash
uv run --project eval goldie monitor \
  --run runs/goldie-latest-parseland-10k-20260606T015650Z-20260606T015904Z-<run-stamp> \
  --watch
```

Resume if interrupted:

```bash
uv run --project eval goldie resume \
  --run runs/goldie-latest-parseland-10k-20260606T015650Z-20260606T015904Z-<run-stamp>
```

Generate reports:

```bash
uv run --project eval goldie report \
  --run runs/goldie-latest-parseland-10k-20260606T015650Z-20260606T015904Z-<run-stamp> \
  --operator \
  --out runs/goldie-latest-parseland-10k-20260606T015650Z-20260606T015904Z-<run-stamp>/OPERATOR_REPORT.md
```

## Separation Of Concerns

- Crossref is sampling-only. Do not use Crossref metadata as field evidence.
- DOI.org resolver links are the only links written into the source corpus.
- Field values come from DOI.org-resolved publisher pages, Taxicab/cache HTML, Taxicab
  reharvest output, or rendered-browser fallback evidence.
- `source.csv` is the input corpus, not an extraction result.
- `manifest.json`, `report.json`, `merged.csv`, `logs/`, `failures/`, and
  `OPERATOR_REPORT.md` are produced only after `goldie run` and `goldie report`.
- Random 10K extraction measures completeness, quality queues, and failure modes; audited
  accuracy still requires scored validation against truth.

## Launch Status

Source is ready. Full 10K extraction was not launched in this step because recent random-100
runs project roughly `$3.8K` for cached plus cloud fallback. Launch requires explicit
operator acceptance of that cost/runtime profile.
