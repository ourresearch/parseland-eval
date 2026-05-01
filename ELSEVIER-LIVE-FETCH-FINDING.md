# Elsevier ScienceDirect live-fetch finding — 2026-04-30

**TL;DR**: Affiliations are accessible via browser-use click-on-author-button, NOT via static HTML or meta tags. This is the unblocker for the 3 Elsevier holdout DOIs that currently return empty rases.

## What was tested

Live-fetched `https://www.sciencedirect.com/science/article/abs/pii/S0012821X25002195` (DOI `10.1016/j.epsl.2025.119420`) via local Chrome MCP. Inspected for affiliation data through three lenses:

| Source | Result |
|---|---|
| `<meta name="citation_author">` | 0 entries |
| `<meta name="citation_author_institution">` | 0 entries |
| `<meta name="citation_pdf_url">` | null |
| `<script type="application/ld+json">` | 0 entries |
| `[class*="affiliation"]` HTML elements | 0 |
| Visible author byline text | author NAMES only, no affiliations |
| **Click "Emily Stoll" author button → dialog** | **FULL AFFILIATION + corresponding-author marker** |

## The dialog content (clicked Emily Stoll author button)

```
Author
Emily Stoll
View in Scopus
Corresponding author.
Department of Earth and Planetary Sciences, Harvard University,
Cambridge, Massachusetts, 02138, USA
```

Compare to gold:

```
Department of Earth and Planetary Sciences, Harvard University,
Cambridge, Massachusetts, 02138, USA
```

**Exact match.** The data is present on Elsevier's page, just not exposed in any way the static Taxicab cache can capture.

## Why the Taxicab cache misses it

Taxicab harvests HTML via `requests.get()` + lxml parsing. No JavaScript execution. ScienceDirect's author affiliation data is loaded on-click via a Scopus-fed dialog — fundamentally invisible to a static fetcher. This is by design (Elsevier's anti-scraping posture).

## What this unlocks

Currently 3 of 50 holdout DOIs are Elsevier-with-empty-rases. Each has multiple authors. Total Bucket-1 author cases on Elsevier: ~9 author-rows. Live-fetch + click would recover all 9 → estimated **+6pp on per-DOI rases** (3 DOIs flip from fail to pass) + **+8pp on per-author rases**.

## Implementation path (NOT done in this commit, scoped for next iteration)

The repo already has `eval/scripts/run_ai_goldie.py` which integrates the local `browser-use` library + real Chrome over CDP. The minimum viable change is:

1. Detect: in `extract_via_taxicab.py`, when Claude returns empty `rasses` for ANY author AND DOI host is `sciencedirect.com`, mark that DOI for live-fetch.
2. Live-fetch: launch a browser-use Agent task on the DOI URL with prompt: "click each author name button, read the affiliation from the dialog, return per-author rases".
3. Merge: backfill the empty rases from the agent output into the Taxicab CSV.

Cost estimate: ~$0.30/DOI extra (~$1 for the 3 holdout cases; ~$300 for the 1000-DOI Elsevier slice in the 10K production run if proportional).

## Why this isn't blocking

The judge stage (Sonnet→Opus verifier on all 50) gives compounding gains across all fields, not just rases-on-Elsevier. Sequence the judge stage first; then the Elsevier live-fetch as a targeted second-pass.

## Reproducibility

```python
# Run via mcp__claude-in-chrome__navigate to /article/abs/<pii>/
# Find the author button by name
# Click and read dialog content
btn = [b for b in document.querySelectorAll('button')
       if b.textContent.strip() == 'Emily Stoll'][0]
btn.click()
# Wait ~500ms
dialog = document.querySelector('[role="dialog"]').textContent
```

This was executed successfully on 2026-04-30. Result above.

## Cross-publisher question (open)

Does the same pattern apply to:
- ACS Publications (`pubs.acs.org`) — also paywall-stub abstract pages
- APS Physical Review (`journals.aps.org`) — bot-checked
- T&F (`tandfonline.com`) — sometimes meta-tag, sometimes JS

Research synthesis says yes (Elsevier-class behavior is common on the heaviest publishers). Confirming on 1 article each for those three publishers is a 30-min follow-up; not in this iteration's scope.
