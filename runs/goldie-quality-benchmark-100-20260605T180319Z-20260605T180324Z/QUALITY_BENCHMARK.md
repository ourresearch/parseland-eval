# Goldie Quality Benchmark - Random 100

Run date: 2026-06-05
Benchmark name: `goldie-quality-benchmark-100`
Corpus: `goldie-quality-benchmark-100-20260605T180319Z`
Source directory: `runs/goldie-quality-benchmark-100-20260605T180319Z`
Run directory: `runs/goldie-quality-benchmark-100-20260605T180319Z-20260605T180324Z`
Comparison baseline: `runs/goldie-random-100-20260605T151604Z-20260605T151616Z`

## Command

```bash
uv run --project eval goldie random \
  --count 100 \
  --name goldie-quality-benchmark-100 \
  --tier cached \
  --fallback-tier cloud
```

The command completed the full cached plus cloud fallback workflow and generated
`manifest.json`, `report.json`, and `OPERATOR_REPORT.md`. The shell wrapper printed
`GOLDIE_RANDOM_EXIT:1` because Goldie reports quality failures when rows fail or remain
in quality queues; the artifacts were still written successfully.

## Headline

This benchmark increases confidence that the operator workflow is stable across a second
independent random-100 sample. It does not prove that extraction quality improved across
all fields. PDF URL presence improved and all-core-empty handling improved versus the
earlier random-100 baseline, while authors, RASES, corresponding author, and abstract
presence dipped on this independent sample.

The correct interpretation is `needs review`: the pipeline is working, but the second run
shows sample variance and remaining publisher/page-specific extraction gaps that need
targeted follow-up before any 10K or 98% accuracy claim.

## Benchmark Delta

| Metric | Earlier random-100 | Quality benchmark-100 | Delta |
|---|---:|---:|---:|
| Landed rows | 100/100 | 100/100 | 0 |
| Fetch OK rows | 99/100 | 98/100 | -1 |
| Failed rows | 1 | 3 | +2 |
| Authors present | 89/100 | 87/100 | -2 |
| RASES present | 67/100 | 66/100 | -1 |
| Corresponding author present | 39/100 | 36/100 | -3 |
| Abstract present | 83/100 | 71/100 | -12 |
| PDF URL present | 66/100 | 74/100 | +8 |
| All-core-empty rows | 6 | 4 | -2 |
| Terminal-flagged empty rows | 5 | 2 | -3 |
| Unresolved all-core-empty rows | 0 | 0 | 0 |
| Bot-check empty rows | 0 | 1 | +1 |
| Extraction-miss rows | 15 | 23 | +8 |
| Multi-priority missing rows | 27 | 28 | +1 |
| CA evidence-audit queue | 18 | 17 | -1 |
| Fallback attempted | 82 | 77 | -5 |
| Fallback returned | 82 | 77 | -5 |
| Fallback used | 32 | 24 | -8 |
| Tier-1 cached cost | $14.90 | $16.21 | +$1.31 |
| Cloud fallback cost | $23.37 | $22.13 | -$1.24 |
| Total cost | $38.27 | $38.35 | +$0.07 |

## Confidence Signals

- The simplified `goldie random` command handled sampling, extraction, reporting, and
  operator report generation end to end.
- The run landed all 100 rows and produced compact report artifacts suitable for GitHub
  and oxjobs.
- `unresolved_all_core_empty` stayed at zero across both random-100 runs.
- All-core-empty rows dropped from 6 to 4, and terminal-flagged empty rows dropped from
  5 to 2.
- PDF URL presence improved from 66 to 74, and cloud fallback filled 10 PDF URL values
  in this run.
- Cost remained effectively unchanged: `$38.27` to `$38.35` per 100.

## Quality Gaps

- Abstract presence fell from 83 to 71, which is the clearest negative signal in this
  benchmark.
- Extraction-miss rows increased from 15 to 23.
- Fetch OK fell from 99 to 98, with 3 final `false` rows in the new run.
- One all-core-empty row carried a bot-check label:
  `10.1515/agph.1979.61.2.245`.
- Corresponding author presence remains the weakest core field at 36/100 and still
  requires conservative evidence audit before accuracy claims.

## Current Quality Queues

| Queue | Count | Sample DOI evidence |
|---|---:|---|
| all_core_empty | 4 | `10.2307/jj.23056124.2`, `10.1515/agph.1979.61.2.245`, `10.2307/jj.4418179.20`, `10.4028/0-87849-971-7.7` |
| terminal_flagged_empty | 2 | `10.1515/agph.1979.61.2.245`, `10.4028/0-87849-971-7.7` |
| bot_check_empty | 1 | `10.1515/agph.1979.61.2.245` |
| unresolved_all_core_empty | 0 | - |
| extraction_miss | 23 | `10.1086/280556`, `10.1071/9781486301904.ch04`, `10.1183/13993003.congress-2025.pa2642`, `10.1016/j.optcom.2016.11.078`, `10.1021/jp5080169` |
| ca_needs_evidence_audit | 17 | `10.1007/bf01025813`, `10.17159/2413-3051/2020/v31i2a6166`, `10.35334/bjbe.v3i2.2327`, `10.1002/jhet.5570200663`, `10.24297/ijmit.v6i2.734` |
| multi_priority_missing | 28 | `10.1086/280556`, `10.1071/9781486301904.ch04`, `10.1177/0968344515575852h`, `10.1177/089033448900500420`, `10.3917/popu.p1959.14n1.0175` |

## Cloud Fallback

| Fallback field filled | Count |
|---|---:|
| RASES | 13 |
| Corresponding author | 5 |
| Abstract | 4 |
| PDF URL | 10 |
| Authors | 5 |
| Bot-check label | 1 |
| Broken DOI label | 1 |

Cloud fallback attempted 77 rows, returned 77, and materially improved 24 rows. The
fallback queue was stable but still had a long tail, so 10K remains a launch decision,
not an unattended default.

## 10K Projection

| Projection | Value |
|---|---:|
| Cost per 100 | $38.35 |
| Projected cost per 10K | $3,834.55 |
| Fallback attempt rate | 77.0% |
| Fallback utilization rate | 24.0% |

Recommendation: do not launch 10K unattended from this benchmark alone. Launch is reasonable
only after explicit operator acceptance of the roughly `$3.8K` cost profile and the long
fallback tail, or after targeted changes reduce fallback pressure without lowering quality.

## Accuracy Boundary

Crossref is sampling-only. Field values must come from DOI.org-resolved publisher pages,
Taxicab/cache HTML, or rendered-browser evidence.

This report measures extraction completeness, field presence, quality queues, fallback
behavior, and cost. It does not measure absolute accuracy. A 98% accuracy claim requires
scored validation against audited truth, such as an audited holdout or manually audited
subset from the random outputs.

## Checksums

```text
883e621ac90e3c66fb1a09e614e3380bb9cbd18fbb62806717c557c831307119  runs/goldie-quality-benchmark-100-20260605T180319Z/source.csv
b96bdf8b30a9a5807e1e3fc07c2ffdd070acc81d75095d75bbbc6663d8af6eac  runs/goldie-quality-benchmark-100-20260605T180319Z-20260605T180324Z/manifest.json
109f04f764914e2d0578232f0f99bfdb3bbe339f8840d324436178788e689b48  runs/goldie-quality-benchmark-100-20260605T180319Z-20260605T180324Z/report.json
7b91825d91b2443496726609cdb543e5cf5914df27c43d2f6780d6c8e72f4952  runs/goldie-quality-benchmark-100-20260605T180319Z-20260605T180324Z/OPERATOR_REPORT.md
a6f841c02a2df178626245027d404db59757cd96013064b1ea8c17a217731d1f  runs/goldie-quality-benchmark-100-20260605T180319Z-20260605T180324Z/merged.csv
```
