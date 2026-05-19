# OpenAlex /works Baseline Snapshot

A **frozen point-in-time capture** of every OpenAlex `/works/doi:{DOI}`
response for the 10,000 DOIs in our gold-standard population. Captured
before any future change to the OpenAlex parser, schema, or the gold
standard itself — so that future regressions on any specific row are
falsifiable against this baseline.

## How it was built

- **Source DOIs:** `eval/eval_local_taxicab_zyte/runs/merged/merged-prod-20260517-152047/merged-FINAL.csv` (10,000 unique DOIs, sha256 in `manifest.json`).
- **Endpoint:** `GET https://api.openalex.org/works/doi:{DOI}?mailto=reach2shubhankar@gmail.com`.
- **Fetcher:** `eval/scripts/snapshot_openalex_baseline.py` (concurrency 10, polite-pool throttle ~10 req/s).
- **404s** are recorded in `fetch-log.jsonl` only — preserves the "wasn't in OpenAlex at baseline" signal vs. "OpenAlex dropped it later."

## Layout

```
openalex-baseline/
├── README.md           # this file
├── manifest.json       # source CSV sha256, counts, per-shard line counts
├── fetch-log.jsonl     # one line per DOI: status / http_code / duration / attempts
└── shards/
    ├── 10.1016.ndjson.gz       (Elsevier — biggest publisher in our set)
    ├── 10.1007.ndjson.gz       (Springer)
    ├── 10.1109.ndjson.gz       (IEEE)
    ├── 10.1002.ndjson.gz       (Wiley)
    ├── ... (other registrants with ≥10 DOIs)
    └── _other.ndjson.gz        (long-tail prefixes with <10 DOIs)
```

Each line in a shard is a JSON object:

```json
{
  "doi": "10.1016/j.jpainsymman.2017.06.006",
  "fetched_at": "2026-05-19T07:55:00Z",
  "http_status": 200,
  "openalex_response": { ... full Works payload as returned by OpenAlex ... }
}
```

## Reading the snapshot

```bash
# Iterate every captured Works record:
for f in eval/data/openalex-baseline/shards/*.ndjson.gz; do
  gunzip -c "$f" | jq '.openalex_response.title'
done

# Look up a specific DOI:
PREFIX=$(echo "10.1016/j.jpainsymman.2017.06.006" | cut -d/ -f1)
gunzip -c "eval/data/openalex-baseline/shards/${PREFIX}.ndjson.gz" \
  | jq 'select(.doi == "10.1016/j.jpainsymman.2017.06.006")'

# Find DOIs OpenAlex didn't have at baseline time:
grep '"status":"not_in_openalex"' eval/data/openalex-baseline/fetch-log.jsonl | jq -r .doi
```

## Re-running / extending

The fetcher is resumable via `.checkpoint/openalex-baseline/done.partial.jsonl`
(gitignored). Re-running the same command picks up where it left off and
will not duplicate records. To capture additional DOIs, append them to a
copy of the source CSV and re-run with `--source <new-csv>` and a fresh
`--out` directory.

To capture a *fresh* baseline at a later date (e.g., to diff drift), use
a new output directory like `eval/data/openalex-baseline-YYYYMMDD/` —
don't overwrite this one.

## What's intentionally out of scope

- No diff tool against future OpenAlex responses (separate work).
- No recurring refresh / cron — this snapshot is meant to be frozen.
- No dashboard upload — this is a developer-side reference asset.
