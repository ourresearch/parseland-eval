# Goldie Random-100 Operator Report

Run date: 2026-06-05
Corpus: `goldie-random-100-20260605T151604Z`
Source directory: `runs/goldie-random-100-20260605T151604Z`
Run directory: `runs/goldie-random-100-20260605T151604Z-20260605T151616Z`

## Commands

```bash
uv run --project eval goldie sample \
  --target 100 \
  --out runs/goldie-random-100-20260605T151604Z/source.csv \
  --gold eval/human-goldie.csv

uv run --project eval goldie run \
  --source runs/goldie-random-100-20260605T151604Z/source.csv \
  --corpus goldie-random-100-20260605T151604Z \
  --tier cached \
  --fallback-tier cloud

uv run --project eval goldie report \
  --run runs/goldie-random-100-20260605T151604Z-20260605T151616Z
```

## Summary Metrics

- Rows sampled: 100
- Rows landed in `merged.csv`: 100
- Fetch OK rows: 99/100
- Failed DOI rows: 1
- Tier-1 cached cost: $14.90
- Cloud fallback cost: $23.37
- Total cost: $38.27
- Cloud fallback attempted: 82
- Cloud fallback returned: 82
- Cloud fallback used: 32
- Extraction-miss rows: 15

Field presence:

| Field | Present | Missing |
|---|---:|---:|
| Authors | 89 | 11 |
| RASES/affiliations | 67 | 33 |
| Corresponding author | 39 | 61 |
| Abstract | 83 | 17 |
| PDF URL | 66 | 34 |

Quality queues:

| Queue | Count |
|---|---:|
| all_core_empty | 6 |
| unresolved_all_core_empty | 0 |
| terminal_flagged_empty | 5 |
| bot_check_empty | 0 |
| extraction_miss | 15 |
| multi_priority_missing | 27 |
| ca_needs_evidence_audit | 18 |

Top missing-field combinations:

| Missing fields | Rows |
|---|---:|
| ca | 23 |
| rases,pdf_url,ca | 10 |
| pdf_url | 9 |
| rases,pdf_url,ca,abstract,authors | 6 |
| rases,ca | 5 |

## Failure And Quality Queues

Single failed DOI:

- `10.7476/9788578794866.0013`: `taxicab: no harvested html`

All-core-empty rows:

- `10.4324/9781315684840-28`: terminal broken DOI
- `10.1016/s1569-9048(21)00072-0`: Elsevier paywall label
- `10.22533/at.ed.0011809122`: PDF redirect label
- `10.5117/9789053563083`: terminal broken DOI
- `10.2478/vzoo-2013-0010`: terminal broken DOI
- `10.58749/skd.ps.2024.rpc.c1.126031`: terminal broken DOI

First extraction-miss rows for follow-up:

- `10.4324/9780429030772-16`
- `10.1097/rhu.0b013e318258b725`
- `10.1109/glocom.1999.830134`
- `10.1525/9780520318212-014`
- `10.1183/13993003.congress-2021.pa1735`
- `10.1016/s0262-4079(17)30640-1`
- `10.70675/633795e0z2e97z4b33z97ecz148423a66bfc`
- `10.59646/sm/277`
- `10.3138/guthrie.51.1.007`
- `10.1016/j.optlastec.2016.01.039`

Taxicab reharvest telemetry:

- refreshed: 60
- post-timeout: 2
- unchanged: 9

Cloud fallback filled fields:

- rases: 19
- abstract: 5
- corresponding author: 7
- authors: 5
- broken_doi: 5
- pdf_url: 3

## Comparison To Prior Pilot-100 Runs

Prior pilot comparison is pipeline signal only. These are random or historical 100-row runs, not audited accuracy validation.

| Run | Fetch OK | Authors | RASES | CA | Abstract | PDF URL | Extraction miss | Total cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| new random-100 `20260605T151604Z` | 99 | 89 | 67 | 39 | 83 | 66 | 15 | $38.27 |
| pilot quality-v2 `20260604T172901Z` | 90 | 89 | 60 | 31 | 72 | 59 | 53 | $43.86 |
| pilot taxicab-live `20260604T163123Z` | 100 | 93 | 53 | 32 | 77 | 68 | 42 | $41.29 |

The new random-100 run improved RASES, CA, abstract, extraction-miss, and all-core-empty handling versus the quality-v2 pilot, and it kept total cost below both prior full-cascade pilot totals. The pilot source CSV was not present as a separate source artifact, so no same-source apples-to-apples rerun was started in this session.

## 10K Recommendation

Do not claim 98% absolute accuracy from this random-100 run. This run measures extraction completeness, failure modes, and quality queues. Verified accuracy still requires an audited truth set.

The pipeline is functional for random DOI sampling and end-to-end extraction, but I do not recommend launching the full 10K extraction unattended from this session yet. The blocker is scale risk from the quality fallback: this 100-row run attempted cloud fallback on 82 rows, used fallback on 32 rows, and spent $38.27 total. A same-rate 10K run projects to roughly $3,827 before any manual audit, and the cloud phase had a long retry tail. The 10K source has been prepared separately for a controlled launch window.

## Checksums

```text
700fc2239f8911aeea09035006b21c911f0716c1368d37df453d29953cf82398  runs/goldie-random-100-20260605T151604Z/source.csv
e0112f2cc7635a35c7faac5d0d76ef23a2420017da07bd4d65c3f81f575037f8  runs/goldie-random-100-20260605T151604Z-20260605T151616Z/manifest.json
fef3bc98410955d8664568b8d60411ee230da8d26542fc570b98868dbd617520  runs/goldie-random-100-20260605T151604Z-20260605T151616Z/report.json
47c2a706d77674ce7904b5e6bd27efe457e7252507f6a45c33ca9d51b36292a8  runs/goldie-random-100-20260605T151604Z-20260605T151616Z/merged.csv
```
