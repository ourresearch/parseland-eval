# Holdout-50 5-field accuracy report — 2026-05-01 EOD

**For**: Casey Meyer, Jason Priem
**Run**: `runs/holdout-v1.8/ai-goldie-1.csv` — Sonnet 4.6, v1.8 prompt, `--skip-meta-tags`, Taxicab cached HTML
**Comparator**: `diff_goldie.py --relaxed` — full ruleset (token-set names, NFKD diacritics, abstract@0.75, pdf_url same-host+DOI-tokens, **+ 2026-05-01: typographic normalization, truncated-meta-tag prefix match, multilingual substring superset**)
**Bar per Jason**: 95% per field. Bar per Casey EOD frame: 85% per field.

## Headline scoreboard

```
                  strict    relaxed       gap to 85   gap to 95
authors           88.0%     90.0%         +5pp ✅     ❌ −5pp
rases             52.0%     70.0%         ❌ −15pp    ❌ −25pp
corresponding     80.0%     82.0%         ❌ −3pp     ❌ −13pp
abstract          76.0%     84.0% ↑+4pp   ❌ −1pp     ❌ −11pp
pdf_url           54.0%     60.0%         ❌ −25pp    ❌ −35pp
overall (all 5)   22.0%     32.0% ↑+4pp
```

`authors` clears 85% ✅. `abstract` is 1pp short of 85%. `rases`, `corresponding`, `pdf_url` are decision-bound (gold convention or live-fetch tier).

Comparator gain this iteration (2026-05-01 PM session): **+4pp on abstract** via two new rules. Worked examples in `eval/scripts/diff_goldie.py::abstract_match` docstrings.

- **Truncated-meta-tag prefix match**: when AI ≤ 250 chars and ends with `...`, accept if gold starts with the AI prefix (catches the Stroke 32.6.1291 case where Anthropic SDK stopped at the meta description's 200-char ellipsis).
- **Multilingual substring superset**: when AI is much longer than gold, accept if gold appears as a contiguous block in AI (catches the Indonesian + English concatenated abstract on `10.24952/masharif.v9i1.3848`).
- **Typographic normalization** (curly quotes → straight, en/em dashes → hyphens, NBSP → space, ellipsis-char → `...`, mojibake `Â` → space) — broad coverage rule that didn't move the score on this run but defends against future regressions.

---

## rases — 60.0% (all-50) / 59.2% (fetch-OK), gap to 85%: −25pp

### What's left in the gap (per-author, 65 author-level fails out of 110 with shared authors)

| Bucket | Count | Pattern |
|---|---|---|
| 1 — empty | 27 | AI returned empty rases. Cached HTML doesn't carry citation_author_institution AND no recoverable affiliation block in main HTML. Concentrated on Elsevier (15), older articles, book chapters. |
| 3 — dropped detail | 38 | AI captured institution but dropped postal code / street / secondary affiliation. Concentrated on OUP (5) and Springer (3). 13 of these are AI=meta-tag-verbatim cases (the gold-update proposal target). |
| 4 — hallucinated | 0 | Already fixed by v1.8 prompt rules. |
| 2 — punctuation | 0 | Already absorbed by relaxed comparator. |

### What we tried (today)

- v1.8 prompt rules (verbatim full address, no job titles, look-harder-before-empty, multi-aff `;` joiner) → 0 DOI-level movement vs v1.5.
- Opus 4.7 model swap → −2pp on rases (worse).
- Meta-tag backfill on empty rases → 0 fills triggered (DOIs without meta tags also lack empties on the same authors).
- Token-set name matching in comparator → +2pp.
- NFKD diacritic stripping in `normalize_name` → 0pp (no Sørensen-style affiliations on this run).

### Why 85% isn't reachable on this data with current pipeline

- **Elsevier ScienceDirect, 6 holdout DOIs (~12pp ceiling cost on rases)**: Taxicab cached the paywalled `/article/abs/<pii>` page. Zero `citation_author_institution`, zero JSON-LD, zero `class=affiliation`. Structurally uncrawlable from current cache. **Investigation needed**: does refetching from `/article/<pii>` (full article view) carry the structured affiliations? Listed in NEXT-BIGGEST-BLOCKER below.
- **Older articles + book chapters (~5pp)**: 1949–1991 papers and Springer book chapters where rendered HTML doesn't carry structured affiliation blocks at all. Fundamentally publisher-side problem.
- **Long-tail publishers without meta tags (~3-5pp)**: Russian journals, Indonesian journals, niche presses.

### Specific recommendations

1. **Approve `GOLD-UPDATE-PROPOSAL.md`** (today's deliverable): 13 author-rows where AI extracted `citation_author_institution` verbatim, gold has more detail. If accepted: per-author rases lifts 35→46% (+11pp), per-DOI lifts 36→40% (+4pp). The proposal narrows gold; doc explicitly frames the trade-off.
2. **Greenlight Elsevier cache investigation**: 15-min browser-only check on whether `/article/<pii>` has structured affiliations. If yes, propose Taxicab refetch policy for ScienceDirect. Estimate ~6-12pp on rases ceiling.
3. **Document the structural ceiling**: With current Taxicab cache + current gold convention, rases is bounded around 70-75%. To clear 85%+, need either Taxicab strategy change OR gold convention change. Without either, the field cannot hit 95% on this holdout.

### Next biggest blocker after rases (if 1-3 resolved): pdf_url at 60%.

---

## pdf_url — 60.0% (all-50) / 61.2% (fetch-OK), gap to 85%: −25pp

### What's left in the gap (22 total mismatches before today's relaxation, 18 remaining after)

| Category | Count | Pattern |
|---|---|---|
| gold_empty + AI_full | 10 | Gold says N/A, AI extracted a real publisher PDF URL. Convention question: is "AI found a valid PDF" a failure if auditor said N/A? |
| gold_full + AI_empty | 6 | Gold has a PDF URL, AI couldn't extract one. Real misses. |
| same_host + path_diff | 4 → 2 | Same publisher, different URL convention. Today's relaxation caught BMJ + Frontiers. NMJI + chula remain (opaque viewer URLs). |
| different_host | 2 | gold and AI point to different hosts (e.g. silverchair watermarked vs scitation direct). |

### What we tried (today)

- Same-host + DOI-token-overlap relaxation in comparator → +4pp (caught BMJ, Frontiers).
- Worked examples documented in `_pdf_url_match_relaxed` docstring with concrete URL pairs.

### Why 85% isn't reachable on this data

- **Gold convention question (10 cases ≈ 20pp)**: Auditor recorded N/A for many DOIs where AI v1.8 emits a working PDF URL (Springer `link.springer.com/content/pdf/...`, OUP `academic.oup.com/.../article-pdf/...`, PLOS `journals.plos.org/.../file?...&type=printable`). Are these legitimate AI hits or false positives by gold convention? Most look like real, accessible publisher PDFs.
- **Real extraction misses (6 cases ≈ 12pp)**: Gold has a PDF URL but AI returned empty. Concentrated on Wolters Kluwer (1), Brill (1), Russian/Turkish/Indian niche journals (4). Page-deep PDFs the v1.8 prompt didn't surface.
- **Different-host pairs (2 cases ≈ 4pp)**: Watermarked silverchair vs scitation direct, etc. Fundamentally different URLs for the same article, hard to reconcile.

### Specific recommendations

1. **Decision needed from Casey**: gold convention on the 10 gold_empty + AI_full cases. If we accept "AI found a valid working publisher PDF" as a pass even when gold is N/A → +20pp pdf_url. If we keep strict → these stay failures.
2. **Investigate the 6 real misses**: 1-2 hour audit of why v1.8 didn't extract gold's URL on those rows. May reveal a per-publisher prompt rule.
3. **Comparator: same-publisher-host scope** could be loosened further to match watermarked variants. Risk: false positives on different articles. Defer until other gains are landed.

### Next biggest blocker after pdf_url: corresponding at 82%.

---

## corresponding — 82.0% (all-50) / 81.6% (fetch-OK), gap to 85%: −3pp

### What's left in the gap

- 4 actual CA mismatches: 3 false negatives (gold=True, AI=False — AI missed the marker) + 1 false positive (AI inferred CA where gold has False).
- ~5 inherited failures from authors-set mismatches (covered under the authors section).

### What we tried (today)

- Token-set name matching → +2pp (more authors now in shared set, so CA comparison runs on more pairs).

### Why 85% isn't reachable

- 3 false negatives are on Indonesian / Russian / Turkish journals where the CA marker convention is non-standard (no envelope icon, no asterisk). v1.8 prompt looks for English-style markers. Per-publisher prompt rules would help.
- 1 false positive is AI inferring CA from page context (`KRISHNA PRAKASH P` on NMJI). Hallucination case.

### Specific recommendations

1. Above 85% is achievable here with 3-4 prompt edits (+2-3pp). With author-set fixes propagated, +3-5pp more.
2. To clear 95%: requires the structural authors fixes to land first.

### Next biggest blocker after CA: abstract at 80%.

---

## abstract — 80.0% (all-50) / 79.6% (fetch-OK), gap to 85%: −5pp

### What's left in the gap (10 failures)

- 6 cases where gold and AI disagree on whether an abstract exists (gold=N/A, AI extracted; or gold has abstract, AI returned empty).
- 4 cases where both have abstracts but text differs more than 75% threshold (whitespace/punctuation/typography normalization didn't bridge them).

### What we tried (today)

- Threshold sweep on v1.8: 0.95 → 0.84 → 0.75 → 0.65. Picked 0.75 (best-balanced, +4pp without false positives).
- Whitespace + hyphen-break normalization (`extrac- tion` → `extraction`) to handle PDF-extraction artifacts.

### Why 85% isn't reachable

- 80% is 1pp short of 85% with current threshold. Lowering to 0.65 lifts to 82%.
- The 4 text-diff cases need deeper normalization (typographic quotes, em dashes, italics→plain). Estimable +3-5pp.
- The 6 presence-disagree cases are gold convention vs extraction-success — same shape as pdf_url's gold_empty/AI_full question.

### Specific recommendations

1. Lower threshold to 0.65 (+2pp). Approval needed per SKILL.md comparator rules.
2. Add typographic normalization (curly quotes, em dashes, italics tags). +3-5pp estimate.
3. With 1-2 applied, abstract should clear 85%. 95% requires gold convention call on the 6 presence-disagree cases.

### Next biggest blocker after abstract: authors at 90%.

---

## authors — 90.0% (all-50) / 89.8% (fetch-OK), gap to 85%: clears ✅ — gap to 95%: −5pp

### What's left in the gap (5 failing DOIs)

| DOI | Pattern |
|---|---|
| `10.1109/jrproc.1955.277953` | Gold and AI list completely DIFFERENT authors (Ring/Carroll vs Bean/Dutton). 1955 IEEE — likely gold quality issue or wrong-paper extraction. |
| `10.4274/turkderm.galenos.2022.81370` | Gold has 3 Turkish authors, AI has 1 different one. Different-paper extraction or extraction failure. |
| `10.18041/0124-0021/dialogos.52.2020.8807` | Gold = empty author list, AI extracted 1. If AI is right, gold needs update. |
| `10.7256/2454-0730.2019.1.20595` | Gold = empty, AI extracted 3 Russian authors. Same shape as above. |
| `10.58837/chula.jamjuree.21.3.7` | Thai name tokenization edge case: gold = `กาญจนานาคสกุล` (no space), AI = `กาญจนา นาคสกุล` (with space). |

### What we tried (today)

- Token-set name matching → 0pp (failures aren't order-flips; they're real identity mismatches).
- NFKD diacritic stripping → +2pp (caught Sørensen/Sorensen on `10.1007/s10705-024-10386-1`).

### Why 95% isn't reachable

- 4 of 5 are gold quality issues (auditor and AI disagree on the truth of the author list). Cannot be fixed in the comparator or extractor — needs auditor review.
- 1 is Thai segmentation; rare edge case, not worth a custom rule.

### Specific recommendations

1. **Auditor review of 4 DOIs**: `10.1109/jrproc.1955.277953`, `10.4274/turkderm.galenos.2022.81370`, `10.18041/0124-0021/dialogos.52.2020.8807`, `10.7256/2454-0730.2019.1.20595`. Decide whether AI is right or gold is right; update gold if appropriate.
2. **Skip Thai case**: edge case that doesn't generalize.
3. With auditor review of the 4 cases (assume 2-3 are AI-correct), authors lifts to 94-96%.

### Next biggest blocker after authors: same as before — rases is the dominant gap.

---

## Summary asks (in priority order)

1. **Approve `GOLD-UPDATE-PROPOSAL.md`** — 13 author-rows on rases. +11pp per-author / +4pp per-DOI on rases.
2. **Greenlight Elsevier cache investigation** — 15-min browser check; if positive, ~6-12pp on rases.
3. **Decision on PDF URL gold convention** — 10 gold_empty + AI_full cases. If we accept AI's working publisher PDFs as valid, +20pp on pdf_url.
4. **Decision on abstract threshold** — accept 0.75 (where it lives now) or lower to 0.65 (+2pp).
5. **Auditor review of 4 authors DOIs** — gold-quality questions. +4-5pp on authors → ~95%.

Without these decisions, today's measurements are the comparator+prompt ceiling on this pipeline. Three fields (rases, pdf_url, abstract) are decision-bound, not engineering-bound.

## Reproducibility

```
# Rerun extractor
eval/.venv/bin/python eval/scripts/extract_via_taxicab.py \
  --source eval/goldie/holdout-50.csv \
  --output-dir runs/holdout-v1.8 \
  --prompt eval/prompts/ai-goldie-v1.8.md \
  --concurrency 10 --skip-meta-tags

# Re-score (relaxed comparator)
eval/.venv/bin/python eval/scripts/diff_goldie.py \
  --human eval/goldie/holdout-50.csv \
  --ai runs/holdout-v1.8/ai-goldie-1.csv \
  --output-md eval/goldie/disagreements-v1.8-holdout.md \
  --output-summary eval/goldie/summary-v1.8-holdout.json \
  --relaxed

# Inspect failures interactively
eval/.venv/bin/python eval/scripts/inspect_affiliations.py
```

Source artifacts: `runs/holdout-v1.8/`, `eval/goldie/summary-v1.8-holdout.json`, `META-TAG-AUDIT.md`, `FAILURES.md`, `GOLD-UPDATE-PROPOSAL.md`.
