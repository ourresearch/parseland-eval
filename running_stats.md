# OpenAlex Baseline — Running Stats

Snapshot of the gold-baseline numbers every future Parseland diff round
should be compared against. Initial entry below; append a new row per
round, don't edit prior rows.

## Artifact

- **Commit:** `f1fc06b` (parseland-eval main)
- **Captured:** 2026-05-19, 07:46–07:52Z (5.6 min wall, 29 req/s effective)
- **Source DOIs:** `eval/eval_local_taxicab_zyte/runs/merged/merged-prod-20260517-152047/merged-FINAL.csv`
  - sha256 `01d933811f1d04feb1e61c359e34f4b870d14e4ccb65a129a0331f713f688204`
  - 10,000 unique DOIs
- **Storage:** `eval/data/openalex-baseline/` — 95 gzipped NDJSON shards by DOI registrant prefix, 24 MB
- **Record shape per shard line:**
  ```json
  {"doi": "...", "fetched_at": "...", "http_status": 200,
   "openalex_response": { ...full /works payload... }}
  ```

## Conservation check

| Count | Value |
|---|---|
| Source DOIs | 10,000 |
| HTTP 200 (in shards) | 9,996 |
| HTTP 404 (in fetch-log only) | 4 |
| Errors | 0 |
| Sum | **10,000 ✅** |

NDJSON validity: 9,996 / 9,996 lines parse cleanly across all 95 shards (no
parse errors, no trailing-newline issues).

## Gold-baseline fill rates (n=9,996 OpenAlex 200s)

These are the "what OpenAlex thinks it has" numbers. Every future Parseland
extraction round gets diffed against these — the gap closes (or doesn't)
as the parser improves.

| Field | Coverage | Rate |
|---|---:|---:|
| `openalex_id` | 9,996 | 100.0% |
| `publication_year` | 9,996 | 100.0% |
| `title` | 9,964 | 99.7% |
| `>=1 author` | 9,032 | 90.4% |
| corresponding-author flag set | 9,018 | 90.2% |
| venue name (`primary_location.source.display_name`) | 8,653 | 86.6% |
| `>=1 affiliation (institution)` | 6,228 | 62.3% |
| `>=1 author ORCID` | 5,750 | 57.5% |
| abstract (inverted index) | 5,586 | 55.9% |
| pdf_url (primary or best_oa) | 2,555 | 25.6% |

## Publisher distribution — top 15 by `host_organization_name`

⚠️ **IEEE is only 85 rows, rank #13** — not viable as the publisher #1
slice. Swap to Elsevier (1,459), Wiley (620), or Springer SBM (465) for
Step 4/5. Also note: **2,721 records (27%) have no `host_organization_name`**
at all — these are largely conference proceedings, book chapters, and
preprints. They'll need a fallback resolution path in the diff harness.

| Rank | Publisher | Count |
|---:|---|---:|
| — | *(no host_organization_name)* | 2,721 |
| 1 | Elsevier BV | 1,459 |
| 2 | Wiley | 620 |
| 3 | Springer Science+Business Media | 465 |
| 4 | Taylor & Francis | 296 |
| 5 | Oxford University Press | 292 |
| 6 | SAGE Publishing | 214 |
| 7 | Lippincott Williams & Wilkins (LWW) | 188 |
| 8 | Cambridge University Press | 166 |
| 9 | Springer Nature | 159 |
| 10 | MDPI | 129 |
| 11 | American Chemical Society | 128 |
| 12 | IEEE | 85 |
| 13 | RELX Group (Netherlands) | 77 |
| 14 | IOP Publishing | 71 |
| 15 | Nature Portfolio | 70 |

## Known gold gaps

4 DOIs not in OpenAlex at baseline time (recorded in `fetch-log.jsonl` as
`status: not_in_openalex`):

- `10.1016/s0140-6736(01)11129-3` — legacy Lancet ID
- `10.1093/oso/9780198262329.003.0001` — OUP Scholarship Online book chapter
- `10.1016/s0167-8140(25)01310-6` — very recent 2025 mint, may index later
- `10.17487/rfc256` — RFC stub

Don't block on these. Future re-fetches will surface any that get added
to OpenAlex as new rows; the diff harness should treat any new
non-baseline DOI as a "newly indexed" event rather than a parser
regression.

## Verification commands

See `eval/data/openalex-baseline/README.md` for the standalone checklist.
The seven baseline-integrity blocks (commit sync, manifest counts,
shards+404=10000, no-duplicate-DOIs, example-DOI spot check,
404 list, source CSV sha256 match) all pass as of `f1fc06b`.

## pdf_url is the gold *floor*, not a ceiling — three-way classification

The 25.6% pdf_url fill rate is **what OpenAlex itself found**, on a
population we've designated as gold. Parseland's job is to match or
exceed that from the publisher landing-page HTML. A flat
"match-or-mismatch" scorer would mislabel Parseland wins as
disagreements. Step 5 must classify each pdf_url comparison into
**three** buckets from day one:

| Bucket | Gold has pdf_url | Parseland has pdf_url | Verdict |
|---|---|---|---|
| ✅ match | yes | yes, equal | counts toward precision/recall |
| ⚠️ candidate Parseland win | no | yes | **flag for review**, not "wrong" — may be a true win that should backflow into the gold |
| ❌ regression | yes | no or differing | counts as a miss |
| ⬜ joint absent | no | no | excluded from denominator |

This same shape should generalize to any field where Parseland can
plausibly recover content OpenAlex missed (PDF, abstract, ORCID,
sometimes affiliations). For fields where Parseland strictly mirrors
OpenAlex (e.g., `openalex_id`), the simpler 2-way classification is
fine.

Operational note: the "candidate Parseland win" pile is the input to
the periodic gold-update proposal — Casey reviews, accepts a subset,
and the next snapshot becomes the new gold floor.

## Deferred coverage — no-publisher slice (2,721 rows, 27%)

2,721 records have no `primary_location.source.host_organization_name`
in OpenAlex (largely conference proceedings, book chapters, SSRN
preprints, eBooks). These rows can't be triaged per-publisher because
they have no publisher to attribute to.

**Decision:** Step 5 scopes to the **publisher-resolved slice (7,275
rows)** for the first diff loops. The no-publisher slice is deferred
until the top-3 publishers are stabilized, at which point we'll need
either:

- (a) a content-type-aware diff (proceedings vs book chapter vs preprint),
- (b) a domain-of-landing-page fallback resolver (e.g., infer "SSRN" from
  the URL host even when OpenAlex didn't fill `host_organization_name`),
- or (c) explicit per-corpus comparators for the largest sub-segments
  (SSRN ~77, ChemInform ~39, Lecture Notes in CS ~32, etc.)

Surfaced deliberately so Casey can see this is a scoped scope, not an
overlooked one.

## Deferred (do alongside diff-harness commit)

- Schema-key consistency scan across all 9,996 records (canonical key set)
- Per-artifact sha256 in `manifest.json` (currently only source CSV is hashed)
