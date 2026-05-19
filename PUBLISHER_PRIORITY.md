# Publisher Priority for the Diff Harness

Priority order for which publisher's slice of the 10K gold gets first
attention from the Parseland diff harness (Step 4/5). This list is
derived from **gold composition**, not from cascade recovery volume.

## Why this changed

The original priority order (IEEE / LCMJ / Elsevier / T&F / SAGE) came
out of the Tier 2 cascade in `eval_local_taxicab_zyte/`, which counted
**escalation-row recovery volume** — how many DOIs each publisher
contributed to the 5,374 rows that needed re-harvesting. That ordering
makes sense when you're asking "where did the cascade do the most
work."

The diff harness asks a different question: **"where does the gold have
enough records to make a per-publisher diff statistically meaningful?"**
The 10K OpenAlex baseline gives the answer:

| Source | What it measures | Best for |
|---|---|---|
| Cascade recovery volume | Where re-harvest closed gaps | Operational triage |
| **Gold composition (this file)** | Where the 10K actually has records | **Diff-harness statistical power** |

Switching the diff harness to IEEE on this basis would have meant
running comparisons on **85 rows** — not enough signal to distinguish
parser improvement from noise.

## Priority order (revised)

Anchor on gold count. Run the diff loop against the top three first
and stabilize each before adding the next tier.

| Rank | Publisher (`host_organization_name`) | Gold count | Cascade rank (prior) |
|---:|---|---:|---:|
| 1 | **Elsevier BV** | 1,459 | 3 |
| 2 | **Wiley** | 620 | (lower) |
| 3 | **Springer Science+Business Media** | 465 | (lower) |
| 4 | Taylor & Francis | 296 | 5 |
| 5 | Oxford University Press | 292 | (lower) |
| 6 | SAGE Publishing | 214 | 4 |
| 7 | Lippincott Williams & Wilkins (LCMJ) | 188 | 2 |
| 8 | Cambridge University Press | 166 | (lower) |
| 9 | Springer Nature | 159 | (lower) |
| 10 | MDPI | 129 | (lower) |
| 11 | American Chemical Society | 128 | (lower) |
| 12 | **IEEE** | **85** | **1 (prior)** |

Source: `running_stats.md` § "Publisher distribution," derived from
`eval/data/openalex-baseline/shards/*.ndjson.gz` at commit `f1fc06b`.

## What "stabilize" means

For each publisher in priority order:
1. Run the diff harness on that slice.
2. Triage disagreements into match / Parseland-win / Parseland-regression (see § three-way pdf_url classification in `running_stats.md`).
3. Lift the matching rate above an explicit per-field threshold (TBD —
   propose 90% for authors / 85% for affiliations as starting bars).
4. Then move to the next publisher.

Do not parallelize across publishers in early rounds — each one teaches
the diff comparator something the next one needs.

## Casey-facing one-liner

> We picked publisher #1 by 10K gold coverage, not by cascade-recovery
> volume, because the diff harness needs statistical power per
> publisher. Elsevier has 1,459 rows in the gold; IEEE has 85. You
> can't run a diff loop on 85 rows.

## Revisit triggers

Re-evaluate this list when any of the following happen:

- The 10K gold population changes materially (new merge, new dedup pass).
- A publisher in the top 12 drops out of OpenAlex coverage at a future snapshot.
- Cascade volume diverges sharply from gold count in a way that suggests
  a per-publisher OpenAlex indexing issue (e.g., IEEE conference papers
  that exist in `merged-FINAL.csv` but get filed under
  `<no host_organization_name>`).
