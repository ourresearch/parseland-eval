# Goldie Learnings

## Current Baselines

The latest full random-100 run is:

```text
runs/goldie-random-100-20260605T151604Z-20260605T151616Z
```

The latest named quality benchmark run is:

```text
runs/goldie-quality-benchmark-100-20260605T180319Z-20260605T180324Z
```

Observed result comparison:

| Metric | Random-100 baseline | Quality benchmark-100 |
|---|---:|---:|
| Landed rows | 100/100 | 100/100 |
| Fetch OK | 99/100 | 98/100 |
| Failed rows | 1 | 3 |
| Authors present | 89/100 | 87/100 |
| RASES present | 67/100 | 66/100 |
| Corresponding author present | 39/100 | 36/100 |
| Abstract present | 83/100 | 71/100 |
| PDF URL present | 66/100 | 74/100 |
| All-core-empty rows | 6 | 4 |
| Unresolved all-core-empty rows | 0 | 0 |
| Extraction-miss rows | 15 | 23 |
| Cloud fallback attempted | 82 | 77 |
| Cloud fallback returned | 82 | 77 |
| Cloud fallback used | 32 | 24 |
| Total cost | $38.27 | $38.35 |

The second independent random-100 benchmark increases confidence in operator workflow
stability, cost stability, and empty-row triage, but it is not a uniform extraction-quality
improvement. PDF URL and all-core-empty handling improved; abstract, authors, RASES, and
corresponding-author presence dipped on the new random sample.

The prepared 10K source is:

```text
runs/goldie-10k-20260605T160114Z/source.csv
```

The 10K extraction was not launched because the random-100 rate projects about `$3.8K`
and a long cloud retry tail. Launch needs explicit operator acceptance of that cost/runtime
profile or a quality-preserving change that lowers fallback pressure.

## What Improved

- `goldie sample --target N` now works without hidden holdout flags.
- Sampling writes the full Goldie CSV schema expected by extraction/reporting.
- Sampling keeps partial DOI state so large samples can resume.
- `goldie run --resume RUN_DIR` reopens existing run directories and checkpoint state.
- `goldie prepare --count N --name NAME` gives operators a simple sample-only command.
- `goldie random --count N --name NAME` gives operators a simple sample/run/report command.
- `goldie resume --run RUN_DIR` discovers source, corpus, tier, and fallback from the manifest.
- `goldie report --operator` writes a GitHub/oxjob-readable Markdown report.

## Quality Lessons

- Crossref is sampling-only. Do not use Crossref metadata to fill field values.
- DOI.org-resolved publisher pages, Taxicab/cache HTML, and rendered-browser evidence are the
  allowed evidence stack.
- Cloud fallback materially improves extraction, but both random-100 runs show a high enough
  fallback attempt rate to make 10K cost/runtime a real launch decision.
- All-core-empty rows must be split into terminal explanations, bot checks, and unresolved
  infrastructure misses.
- Corresponding-author gaps require conservative triage; absence on a landing page is not the
  same thing as audited absence.
- Random extraction reports show completeness and failure modes, not verified accuracy.
- A second independent random run is confidence evidence, not an apples-to-apples parser
  improvement test. Same-source reruns or audited holdouts are required to prove extraction
  improvement.

## 98% Accuracy Gate

Do not claim 98% absolute accuracy from random extraction alone.

Use one of these validation paths:

- score against an existing audited holdout matching the current schema and workflow;
- manually audit a subset from random-100 before making accuracy claims;
- create a separate validation sample before prompt/code tuning and score against it.

The report should always separate extraction completeness, field presence, quality queues,
verified accuracy, and unresolved rows requiring human or publisher-specific follow-up.

## Launch Recommendation

The workflow is operator-ready. The 10K extraction itself remains gated on explicit acceptance
of observed cost/runtime or a quality-preserving reduction in fallback pressure.
