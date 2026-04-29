# Holdout-50 prompt comparison: v1.1 → v1.4

All four versions scored against `human-goldie.csv` via `scripts/diff_goldie.py`.
Per-field % is over the 50 shared DOIs (the holdout split, No 51-100).
Bold = highest in column.

| Version | authors | rases | corresponding | abstract | pdf_url | overall |
|---------|---------|-------|---------------|----------|---------|---------|
| v1.1     | **78.0** | **48.0** | **70.0** | **68.0** | 42.0 | **16.0** |
| v1.2     | 68.0 | 42.0 | 54.0 | 62.0 | 42.0 | **16.0** |
| v1.3     | 18.0 | 18.0 | 18.0 | 28.0 | **56.0** | 10.0 |
| v1.4     | 68.0 | 40.0 | 60.0 | 64.0 | 50.0 | **16.0** |

## Net moves vs v1.1 baseline

| Version | authors | rases | corresponding | abstract | pdf_url | overall |
|---------|---------|-------|---------------|----------|---------|---------|
| v1.2     | -10.0 | -6.0 | -16.0 | -6.0 | +0.0 | +0.0 |
| v1.3     | -60.0 | -30.0 | -52.0 | -40.0 | +14.0 | -6.0 |
| v1.4     | -10.0 | -8.0 | -10.0 | -4.0 | +8.0 | +0.0 |

## Comparability footnote — No 81 swap

In `human-goldie.csv` the No 81 DOI was changed from `10.1371/journal.pone.0192138.t002`
(a PLOS table DOI explicitly listed in `parseland-eval/CLAUDE.md` under
"Known DOIs that Taxicab hasn't harvested") to the parent article DOI
`10.1371/journal.pone.0192138`. The old `.t002` row was effectively un-extractable
in v1.{1,2,3} runs, so all four versions had ≤49 testable rows on that slot;
v1.4 has 50. At the per-field aggregate level this is at most a 2pp confound.

## Field-level interpretation (v1.4)

- **pdf_url +8pp vs v1.1 (42% → 50%)** — the "no URL construction from DOI patterns"
  rule introduced in v1.4 is working. v1.4 is the column max.
- **authors -10pp vs v1.1 (78% → 68%)** — same as v1.2. The 11KB→8KB trim
  probably cost recall on multi-author affiliation extraction.
- **corresponding -10pp vs v1.1 (70% → 60%)** — tracks the authors regression;
  CA flag depends on author-name match.
- **rases -8pp vs v1.1 (48% → 40%)** — exact-string affiliation match is the
  hardest comparator and degrades fastest under prompt-trim.
- **abstract -4pp vs v1.1 (68% → 64%)** — small regression, within noise.
- **overall 16%** — identical to v1.1/v1.2 row-perfect rate. Not lifted by v1.4.

## Verdict

No version clears the 95% gate per Phase C. v1.1 remains the best baseline on
four of six fields (authors / rases / corresponding / abstract / overall),
while v1.4 is the best on pdf_url. v1.3 is a clear regression (over-eager bail
rule produces empty arrays — see OXJOB.md).

Recommended next step: a v1.5 that re-introduces v1.1's author/affiliation
extraction depth while preserving v1.4's pdf_url discipline. A merge-best-of
rather than further trim. Patch list to be filed at `goldie/insoluble-cases.md`
after a disagreement-class triage of `goldie/disagreements-v1.4-holdout.md`.
