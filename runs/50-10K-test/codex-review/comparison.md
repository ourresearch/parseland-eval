# Comparison — iter R framework vs Codex (GPT-5.5) review

**Date:** 2026-05-11
**Subject:** `runs/50-10K-test/ai-goldie-50.v2.csv` (50 rows × 13 cols)
**Iter R label distribution:** 15 complete / 27 paywalled / 7 bot-check / 1 pdf-redirect / 5 extraction-miss / 2 resolve-error
**Codex verdict:** REJECTED — 1 Critical, 2 High, 9 Medium failures (39 PASS)

---

## Headline

**The two frameworks are complementary, not redundant.** Iter R focuses on *why an empty cell is correct*. Codex focuses on *whether a filled cell is correct*. Codex passed every row that iter R labelled paywalled or bot-check (validating the framework's "empty is OK here" logic), but caught **12 data-quality issues iter R was blind to**.

If we ship iter R alone to 10K, we will report inflated quality numbers on rows where the cells *are* filled but the content is mojibake-corrupted, contaminated with footer/contact text, cross-contaminated from another DOI, or violating the author JSON schema. Codex's review identifies those gaps explicitly.

---

## Criteria overlap table

| Codex criterion | Severity | Maps to iter R? | Notes |
|---|---|---|---|
| #1 DOI/Link Validity | Critical | — (assumed valid from sampler) | Pre-check, not in iter R scope |
| #2 Resolved Link Validity | High | Partial — iter R has `resolve-error` label | Codex treats resolve-error as a High failure; iter R just labels it |
| #3 Boolean Field Validity | Critical | — | Schema-level, iter R assumes valid |
| #4 Condition Flag Consistency | High | Partial — iter R explains empties | Codex's "should not have all-3-empty without flag" is sharper than iter R |
| #5 Authors JSON Parseability | Critical | — | Iter R doesn't validate JSON shape |
| #6 Author Object Schema | Critical | — | Iter R doesn't validate per-object schema → **Codex caught row 34 bug** |
| #7 Author Name Plausibility | High | — | Iter R doesn't text-check filled fields |
| #8 Duplicate Author Detection | Medium | — | Iter R doesn't dedupe |
| #9 Affiliation Plausibility | Medium | — | Iter R doesn't text-check filled `rasses` |
| #10 Abstract Prose Plausibility | High | — | Iter R doesn't text-check filled abstracts |
| #11 Abstract Encoding Quality | Medium | — | **Iter R blind to mojibake — Codex caught 5 rows** |
| #12 Abstract Contamination | Medium | — | **Iter R blind — Codex caught rows 7, 8** |
| #13 PDF URL Validity | High | — | Iter R only labels empty PDF URLs, not filled ones |
| #14 PDF Flag Consistency | Medium | Partial — iter R sets `Resolves To PDF=TRUE` | Codex checks the inverse direction too |
| #15 Language Flag Consistency | Medium | — | **Iter R blind — Codex caught rows 26, 47** |

**Conclusion:** Of Codex's 15 criteria, **only 2 partially overlap with iter R** (#2 resolve-error, #14 pdf-redirect). The other 13 are dimensions iter R doesn't touch — because iter R was scoped to "explain empties," not "validate fills."

---

## Per-row deltas

### Codex flagged rows iter R passed as "complete"

Iter R said no label needed (15 complete rows). Codex flagged 8 of those 15 for filled-cell quality issues:

| Row | DOI | iter R | Codex | Issue |
|---|---|---|---|---|
| 6 | `10.2218/ls.v2i1.2016.1429` | complete | FAIL-MEDIUM | 10× `â` mojibake in Abstract |
| 7 | `10.4314/bcse.v33i2.14` | complete | FAIL-MEDIUM | KEY WORDS + journal citation + DOI appended to Abstract |
| 8 | `10.1093/bioinformatics/btt612` | complete | FAIL-MEDIUM | "Availability:" + "Contact:" appended to Abstract |
| 15 | `10.1007/978-3-642-03085-7_117` | complete | FAIL-MEDIUM | 4× `â` + 1× `Î` mojibake in Abstract + Author affiliation |
| 26 | `10.70675/...` | complete | FAIL-MEDIUM | **Spanish abstract with `no english=FALSE` — actually CROSS-CONTAMINATION** (see "serious finding" below) |
| 46 | `10.3389/fpls.2025.1613503` | complete | FAIL-MEDIUM | 8× `â` mojibake in Author affiliations |
| 47 | `10.36015/cambios.v19.n1.2020.601` | complete | FAIL-MEDIUM | Spanish abstract with `no english=FALSE` (legitimately Spanish — flag bug) |
| 34 | `10.1016/j.camwa.2011.12.003` | complete | **FAIL-CRITICAL** | Author 2 missing required `corresponding_author` key — schema bug |

### Codex agreed with iter R's "empty is explained"

All 7 bot-check rows (13, 16, 19, 20, 27, 30, 41) → Codex PASS.
All 27 paywalled rows → Codex PASS for the empty cells; Codex only flagged paywalled rows when they ALSO had a filled-cell quality issue.
The 1 pdf-redirect row (14) → Codex PASS.
The 5 extraction-miss rows (1, 2, 9, 17, 45) → Codex PASS (Codex didn't penalize them either, since broken_doi=FALSE but resolved_links was non-empty).

### Codex flagged rows iter R labelled "resolve-error"

| Row | DOI | iter R | Codex | Note |
|---|---|---|---|---|
| 24 | `10.1289/isee.2022.p-0608` | resolve-error → extraction-miss | FAIL-HIGH | `broken_doi=FALSE` but `resolved_links` empty — should flag broken_doi=TRUE |
| 32 | `10.24297/ijct.v15i2.569` | resolve-error | FAIL-HIGH | Same — connection error should propagate to broken_doi=TRUE |

Codex's framework treats resolve-errors as schema violations, not just structural commentary. Iter R was too soft here.

---

## Serious finding: cross-contamination on row 26

**This is the highest-value catch of the entire Codex review.**

Rows 26 and 47 have **identical Spanish abstracts** about COVID-19 ("La enfermedad producida por el Coronavirus..."). Row 47 is `10.36015/cambios.v19.n1.2020.601` (Ecuadorian journal *Cambios* — a real COVID paper, Spanish content makes sense). Row 26 is `10.70675/d49f50e4z0a1ez4d31z88c7zce445b43f421` — a **French thesis from Lyon** (`theses.fr/2024LYO10010`).

A French thesis cannot have a Spanish COVID abstract. The extraction cross-contaminated row 26's data from row 47's source page (or vice versa). This is not a mojibake issue — it's a **wrong-content-extracted issue**, the worst kind of failure because the row looks fully populated and "valid" by every surface check.

Iter R completely missed this. Codex caught it (via the language-flag criterion). **For 10K, we need cross-DOI deduplication of extracted abstracts to catch this class of error.**

---

## Codex's aggregate verdict: REJECTED

> "I would require zero Critical failures before approving any rows for downstream use." — Codex Stage A

The one Critical failure (row 34 author schema) triggers Codex's hard-stop. Plus the verdict notes 12% Medium failure rate and 4% High failure rate, both above Codex's 5% / 0% bars respectively.

This is a stricter standard than iter R applied. Iter R's framing was "is every empty cell explained?" — and that answer was yes (42/50). Codex's framing is "is every filled cell defensibly correct?" — and that answer is no (12 issues across 11 rows).

---

## Top 3 systemic patterns Codex identified

1. **Mojibake in abstracts and affiliations** — 5 rows. UTF-8 → Latin-1 misinterpretation at the source HTML or response-decoding layer.
2. **Resolve-error rows retain `broken_doi=FALSE`** — the extractor didn't propagate the connection failure to the schema-level flag.
3. **Non-English abstracts not flagged** — the extractor didn't run language detection on the extracted abstract before setting `no english`.

---

## Recommended actions (in priority order for 10K push)

### Must-fix before 10K
1. **Author schema validator** (Codex #6 Critical) — assert every author object has `name`, `rasses`, `corresponding_author` with correct types. Run as a post-extraction lint. Fix the one extraction path that emitted row 34's malformed object.
2. **Cross-DOI abstract dedup** — at the end of any batch, look for duplicate Abstract text across different DOIs. Flag both rows for human review. Would have caught row 26.
3. **Mojibake detector** (Codex #11) — regex over Abstract + Author rasses for `â`, `Ã`, `Î`, `�`, broken quote/dash. Flag the row; either re-extract with explicit UTF-8 decoding, or mark Status=FALSE.

### Should-fix before 10K
4. **Abstract contamination filter** (Codex #12) — strip trailing "KEY WORDS:", "Availability:", "Contact:", "DOI:" appendages from filled abstracts. Catches rows 7, 8 today.
5. **Language detection on filled abstracts** — `langdetect` or `fasttext` on every non-empty Abstract; set `no english=TRUE` when detector returns non-English. Catches row 47 cleanly; surfaces row 26 as anomalous (DOI publisher language ≠ abstract language).
6. **Resolve-error → broken_doi propagation** (Codex #4) — when `requests.get` raises, set `broken_doi=TRUE` instead of leaving it FALSE.

### Nice-to-have
7. **Author name plausibility** (Codex #7) — regex against URLs, DOIs, "Department of...", "Click here" etc. in `name` field.
8. **Affiliation plausibility** (Codex #9) — similar for `rasses`.

---

## What this means for the 10K decision

**Iter R alone is insufficient.** The framework correctly handles structural empties but is blind to filled-cell quality issues. Going to 10K with iter R-only:
- We'd report ~84% "complete-or-explained" — looks healthy
- But hidden inside that: ~24% of rows would have mojibake, contamination, or wrong-content issues
- The downstream consumer (OpenAlex DB ingest) would get poisoned data with no warning

**Adding Codex's 6 must-fix + should-fix criteria as post-extraction validators is the right next step.** They're all 10-30 line Python predicates over the CSV. Cheap to add, dramatic improvement in trust.

**Recommended sequence:**
1. Implement criteria #1, #2, #3, #11, #12, #15 as `eval/scripts/validate_extraction.py` (new script)
2. Re-run validation on `ai-goldie-50.v2.csv` — produce a `validation-report.md` mirroring Codex's per-row output
3. If validation reaches zero Criticals + Highs <5% on the 50-DOI sample, green-light 10K
4. If not, fix the underlying extraction issues, re-run Tier 1, re-validate

---

## Files in this review

- `stage-a-input.csv` — 10-row slice handed to Codex (Notes column stripped)
- `stage-a-criteria.md` — Codex's 15 proposed criteria (this doc)
- `stage-b-review.md` — Codex's per-row verdicts for all 50 rows
- `codex-stdout.log` / `codex-stage-b-stdout.log` — raw Codex CLI output
- `comparison.md` — this document
