# Stage A — Codex's proposed evaluation criteria

**Model:** gpt-5.5 (Codex v0.128.0)
**Reasoning effort:** xhigh
**Input given:** 10 rows × 12 cols (Notes column stripped — Codex was NOT told about iter R framework)

---

## Criteria (15)

### 1. DOI and DOI Link Validity — **Critical**
- **Test:** `DOI` must be non-empty, match DOI-like syntax, and `Link` must be `https://doi.org/<DOI>`.
- **Why:** Bad identifiers make the row unsafe to join, dedupe, or resolve downstream.

### 2. Resolved Link Validity — **High**
- **Test:** `resolved_links` must contain at least one valid HTTP(S) URL unless `broken_doi` is `TRUE`.
- **Why:** The resolved landing page is the only provenance signal available in the CSV.

### 3. Boolean Field Validity — **Critical**
- **Test:** `Status`, `Has Bot Check`, `Resolves To PDF`, `broken_doi`, and `no english` must be literal `TRUE` or `FALSE`, not blank or free text.
- **Why:** These fields drive downstream filtering and exception handling.

### 4. Condition Flag Consistency — **High**
- **Test:** If `broken_doi` is `TRUE`, core metadata may be empty; if `broken_doi` is `FALSE` and `Has Bot Check` is `FALSE`, the row should not have all of `Authors`, `Abstract`, and `PDF URL` empty.
- **Why:** A non-broken, non-blocked DOI with no extracted metadata is likely an extraction failure.

### 5. Authors JSON Parseability — **Critical**
- **Test:** `Authors`, when non-empty, must parse as a JSON array.
- **Why:** Invalid JSON will break automated ingestion even if the visible text looks usable.

### 6. Author Object Schema — **Critical**
- **Test:** Each author object must contain `name`, `rasses`, and `corresponding_author`; `name` and `rasses` must be strings, and `corresponding_author` must be boolean.
- **Why:** Downstream systems need predictable author fields to map contributors and affiliations.

### 7. Author Name Plausibility — **High**
- **Test:** Each non-empty author `name` should look like a person name, not a URL, DOI, institution, article title, navigation label, or publisher boilerplate.
- **Why:** Author pollution is especially damaging because it creates false contributor records.

### 8. Duplicate Author Detection — **Medium**
- **Test:** A row should not contain exact duplicate author names after trimming whitespace and normalizing case.
- **Why:** Duplicate authors inflate contributor counts and distort attribution.

### 9. Affiliation Plausibility — **Medium**
- **Test:** `rasses`, when present, should look like an affiliation string and should not contain URLs, DOI strings, abstracts, article titles, or generic UI text.
- **Why:** Affiliation fields are easy to contaminate with nearby page text.

### 10. Abstract Prose Plausibility — **High**
- **Test:** `Abstract`, when present, should be substantive prose, roughly sentence-like, and not just keywords, menu text, references, copyright notices, or download instructions.
- **Why:** Without ground truth, prose quality is the best CSV-only signal that the abstract was extracted from the right page region.

### 11. Abstract Encoding Quality — **Medium**
- **Test:** `Abstract` should not contain obvious mojibake or replacement artifacts such as `â`, `Ã`, `�`, or broken quote/dash sequences beyond a small tolerance.
- **Why:** Encoding corruption makes otherwise correct abstracts unsuitable for search, display, and NLP.

### 12. Abstract Contamination Check — **Medium**
- **Test:** `Abstract` should not include obvious non-abstract appendages such as `KEY WORDS:`, `DOI:`, `Contact:`, `Availability and implementation:`, citation metadata, or publisher footer text unless that is genuinely part of the abstract format.
- **Why:** Mixed metadata fields reduce precision and can poison downstream text analysis.

### 13. PDF URL Validity — **High**
- **Test:** `PDF URL`, when present, must be a valid HTTP(S) URL and should look PDF-like by path, filename, query, or publisher download route.
- **Why:** Invalid or non-document URLs break PDF fetchers and create false availability signals.

### 14. PDF Flag Consistency — **Medium**
- **Test:** If `Resolves To PDF` is `TRUE`, either `Link`, `resolved_links`, or `PDF URL` should point to a PDF-like URL; if `PDF URL` is present, `broken_doi` should not be `TRUE`.
- **Why:** Contradictory flags make it hard to interpret whether the row represents metadata extraction or direct-PDF resolution.

### 15. Language Flag Consistency — **Medium**
- **Test:** If `no english` is `TRUE`, an English-looking abstract should be absent or treated as suspect; if `no english` is `FALSE`, obvious non-English-only extracted text should be flagged.
- **Why:** The language annotation affects whether missing or low-quality English metadata is acceptable.

---

## Codex's aggregate approval threshold

> "I would require **zero Critical failures** before approving any rows for downstream use. At the batch level, I would want at least **95% of non-broken, non-bot-check rows** to have parseable authors and at least one usable core field among authors, abstract, or PDF URL, with **High-severity failures under 5%**. Medium issues like encoding artifacts or abstract contamination can be tolerated only if they are measured, reviewable, and low enough not to bias downstream indexing or analytics."

---

## Severity breakdown

| Severity | Count | Criteria # |
|---|---:|---|
| Critical | 4 | 1, 3, 5, 6 |
| High | 4 | 2, 4, 7, 10, 13 |
| Medium | 7 | 8, 9, 11, 12, 14, 15 |

Total: 15 criteria.
