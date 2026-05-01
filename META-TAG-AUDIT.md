# Meta-tag audit — Casey 2026-04-30 meeting action items

**Run**: v1.8 Sonnet on holdout-50, `--skip-meta-tags` (Casey's apples-to-apples LLM-only baseline). Cached HTML is the same Taxicab-S3 source the LLM saw.
**Tool**: `eval/scripts/meta_tag_audit.py` — re-runnable, ~1 min wall, no LLM calls.

## Action item 1 — Is AI using citation_author_institution as primary?

**Yes, on Oxford. Partially on Springer Nature. No on Elsevier (because Elsevier doesn't ship those tags).**

Per-author state across 115 author-comparisons in 50 DOIs:

| State | Count | What it means |
|---|---|---|
| AI_matches_gold | 40 | Success |
| AI_and_gold_differ_meta_unrelated | 19 | AI got something different from gold; meta tag also disagrees |
| AI_empty_gold_filled | 17 | Bucket 1 — AI returned empty, gold has data |
| **AI_matches_meta_not_gold** | **13** | **Casey's smoking gun — AI is reading the meta tag verbatim** |
| no_shared_authors | 16 | Author lists don't intersect at all (different problem) |
| all_empty | 6 | Both gold and AI empty (true negatives) |
| gold_matches_meta_AI_misses | 2 | AI failed to extract even though meta tag had the right answer |
| AI_substr_meta_not_gold | 1 | AI got a substring of meta tag |
| fetch_failed | 1 | Taxicab couldn't deliver HTML |

The 13 `AI_matches_meta_not_gold` are concentrated:

| Publisher | Count of "AI = meta tag, gold has more" |
|---|---|
| Oxford University Press | 5 |
| Other / Unknown (long tail) | 5 |
| Springer Nature | 3 |
| **Total** | **13** |

## Action item 2 — Gold-to-meta update yield

| Metric | Value |
|---|---|
| DOIs currently passing rases (relaxed) | 18 / 50 = 36.0% |
| DOIs that WOULD pass if gold accepted meta-tag rases when AI matches meta | 20 / 50 = 40.0% |
| **Net additional DOIs from gold update** | **+2 DOIs (+4 pp)** |

The yield is **smaller than the per-author count would suggest** because most "AI=meta" cases happen on DOIs where *other* authors fail in *different* ways. Updating gold for the meta-matched authors doesn't flip the whole DOI to pass — DOI-level rases is per-author AND.

**Recommendation**: A targeted gold-to-meta update **just for OUP** (where the pattern is cleanest) is worth doing for documentation honesty, even though the DOI-level yield is small. It removes a class of "punishing AI for not finding data that isn't on the page" failures.

### Concrete OUP cases (3 of 5 OUP DOIs have meta tags)

```
10.1093/jaoac/32.2.156 — D E Bullis (1 author)
  META: "Chemist, Oregon Agricultural Experiment Station, Corvallis, Oregon"
  AI:   "Chemist, Oregon Agricultural Experiment Station, Corvallis, Oregon"
  GOLD: "Associate Referee, Chemist, Oregon Agricultural Experiment Station, Corvallis, Oregon"
  → AI matches meta. Gold added the role prefix "Associate Referee".

10.1093/jee/97.2.646 — 4 authors, ALL match meta tag
  META & AI: "Department of Entomology, Montana State University, Bozeman, MT 59717"
  GOLD: "...Bozeman, MT 59717; College of Agriculture and Life Sciences, 104 Hutcheson Hall (0402), Virginia Tech, Blacksburg, VA 24061"
  (Quisenberry; similar pattern for Wang, Ni, Tolmay with different secondary affiliations.)
  → AI matches meta. Gold added secondary affiliations.

10.1093/jaoac/60.2.289 — meta tags present, no shared author overlap
10.1093/oed/5131921241 — no citation_author meta tags (OED archive)
10.1093/oed/4932880791 — no citation_author meta tags (OED archive)
```

## Action item 3 — Beyond Oxford?

**Yes (Springer Nature, partial), but the picture is mixed.**

| Publisher | DOIs | Meta tags present | AI=meta cases |
|---|---|---|---|
| **Oxford University Press** | 5 | 3 | 5 (across 2 DOIs) |
| **Springer Nature** | 7 | 6 | 3 |
| Other / Unknown (tail) | 11 | 7 | 5 |
| **Elsevier** | **6** | **0** | **0** |
| IEEE | 3 | 0 | 0 |
| Wolters Kluwer | 1 | 0 | 0 |
| Taylor & Francis | 1 | 0 | 0 |
| Wiley | 1 | 1 | 0 |
| Cambridge University Press | 1 | 0 | 0 |
| (others — 1 DOI each) | 12 | 8 | 0 |

**The Elsevier story is the biggest finding outside OUP.** All 6 Elsevier DOIs have:
- No `citation_author` meta tags
- No `citation_author_institution` meta tags
- No `dc.creator` / `dc.contributor.affiliation`
- No JSON-LD `affiliation` field
- No `class=affiliation` HTML elements

Resolved URLs are ScienceDirect *abstract* pages (`/article/abs/pii/...`) — paywalled stubs, not the full article. **The Taxicab cache for Elsevier holds the abstract-page HTML, not the article-page HTML.** AI can't extract affiliations because they aren't in the cached HTML.

This is a **fetch/cache problem, not a prompt problem**. Fixing it requires either:
- Taxicab refetching Elsevier from a different URL (the article view, if accessible), or
- Per-publisher CDP-based browser fetches that bypass the abstract gate, or
- Accept that Elsevier paywalled-abstract DOIs in the gold set will never have rases extractable from cache and either drop them or mark gold accordingly.

## Action item 4 — Next biggest blocker

Ranked by addressability:

1. **Elsevier no-metadata cache (6 DOIs, ~12 pp ceiling cost)**: structural; needs Taxicab strategy change. Biggest single chunk.
2. **OUP gold-to-meta convention (3 DOIs, ~4 pp gain)**: small but cheap. Update gold or accept meta as valid.
3. **The 19 "AI_and_gold_differ_meta_unrelated" cases**: real extraction errors where neither AI nor meta tag has gold's value. Mix of older articles and book chapters where Claude grabbed the wrong section. Hardest to fix at scale; needs per-row inspection.
4. **The 16 "no_shared_authors" cases**: author-list mismatches (different problem from rases). The morning's Bucket 1 analysis already covers some of these.

## Action item 5 — Progress update

Sending a Slack post with these numbers. SLACK-DRAFT.md will be updated.

## How to reproduce

```
eval/.venv/bin/python eval/scripts/meta_tag_audit.py
```

Reads `eval/goldie/holdout-50.csv` + `runs/holdout-v1.8/ai-goldie-1.csv`, fetches cached HTML via Taxicab, no LLM calls, ~1 min wall.
