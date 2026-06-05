# Goldie Operator Report - goldie-quality-benchmark-100-20260605T180319Z

**Status:** `needs review`
**Reason:** quality queues remain before audited accuracy claims

This report is generated from `manifest.json` and `report.json`. It measures extraction completeness and quality queues; it does not prove absolute field accuracy without audited truth.

## Run Metrics

| Metric | Value |
|---|---:|
| Rows | 100 |
| Fetch OK | 98/100 (98.0%) |
| Failed rows | 3 |
| All-core-empty | 4 |
| Unresolved all-core-empty | 0 |
| Extraction-miss rows | 23 |
| Bot-check empty rows | 1 |
| Fallback attempted | 77 |
| Fallback returned | 77 |
| Fallback used | 24 |
| Tier-1 cost | $16.21 |
| Fallback cost | $22.13 |
| Total cost | $38.35 |

## Field Presence

| Field | Present | Missing | Presence |
|---|---:|---:|---:|
| authors | 87/100 | 13 | 87.0% |
| rases | 66/100 | 34 | 66.0% |
| ca | 36/100 | 64 | 36.0% |
| abstract | 71/100 | 29 | 71.0% |
| pdf_url | 74/100 | 26 | 74.0% |

## Quality Queues

| Queue | Count | Sample DOIs |
|---|---:|---|
| unresolved_all_core_empty | 0 | - |
| all_core_empty | 4 | 10.2307/jj.23056124.2, 10.1515/agph.1979.61.2.245, 10.2307/jj.4418179.20, 10.4028/0-87849-971-7.7 |
| terminal_flagged_empty | 2 | 10.1515/agph.1979.61.2.245, 10.4028/0-87849-971-7.7 |
| bot_check_empty | 1 | 10.1515/agph.1979.61.2.245 |
| extraction_miss | 23 | 10.1086/280556, 10.1071/9781486301904.ch04, 10.1183/13993003.congress-2025.pa2642, 10.1016/j.optcom.2016.11.078, 10.1021/jp5080169, 10.23947/interagro.2020.1.326-328, 10.1109/indicon.2015.7443496, 10.1044/sasd15.1.2-a, 10.1163/9789004282360_013, 10.1145/330534.330555 |
| ca_needs_evidence_audit | 17 | 10.1007/bf01025813, 10.17159/2413-3051/2020/v31i2a6166, 10.35334/bjbe.v3i2.2327, 10.1002/jhet.5570200663, 10.24297/ijmit.v6i2.734, 10.1002/hlca.19830660228, 10.12677/ds.2021.74022, 10.59807/jlsar.v4i2.89, 10.1159/000222025, 10.1051/lhb/1988029 |
| multi_priority_missing | 28 | 10.1086/280556, 10.1071/9781486301904.ch04, 10.1177/0968344515575852h, 10.1177/089033448900500420, 10.3917/popu.p1959.14n1.0175, 10.1515/9783110725025-004, 10.1016/b978-3-437-21029-7.00091-7, 10.2307/jj.23056124.2, 10.2307/jj.36233905.8, 10.23947/interagro.2020.1.326-328 |

## 10K Projection

| Projection | Value |
|---|---:|
| Cost per 100 | $38.35 |
| Cost per 10K | $3834.55 |
| Fallback attempt rate | 77.0% |
| Fallback utilization rate | 24.0% |

## Operator Commands

- `uv run --project eval goldie monitor --run /Users/shubh-trips/Documents/OpenAlex/parseland-eval/runs/goldie-quality-benchmark-100-20260605T180319Z-20260605T180324Z`
- `uv run --project eval goldie resume --run /Users/shubh-trips/Documents/OpenAlex/parseland-eval/runs/goldie-quality-benchmark-100-20260605T180319Z-20260605T180324Z`
- `uv run --project eval goldie report --run /Users/shubh-trips/Documents/OpenAlex/parseland-eval/runs/goldie-quality-benchmark-100-20260605T180319Z-20260605T180324Z --operator --out /Users/shubh-trips/Documents/OpenAlex/parseland-eval/runs/goldie-quality-benchmark-100-20260605T180319Z-20260605T180324Z/OPERATOR_REPORT.md`
- `uv run --project eval goldie run --source /Users/shubh-trips/Documents/OpenAlex/parseland-eval/runs/goldie-quality-benchmark-100-20260605T180319Z/source.csv --corpus goldie-quality-benchmark-100-20260605T180319Z --tier cached --fallback-tier cloud --resume /Users/shubh-trips/Documents/OpenAlex/parseland-eval/runs/goldie-quality-benchmark-100-20260605T180319Z-20260605T180324Z`

## Accuracy Boundary

Crossref is sampling-only. Field values must come from DOI.org-resolved publisher pages, Taxicab/cache HTML, or rendered-browser evidence.

Do not claim 98% absolute accuracy from this report alone. A 98% claim requires scored validation against audited truth, such as an existing holdout or a manually audited subset.
