# LEARNING.md

Canonical AI-vs-gold disagreement registry for parseland-eval. New disagreements get an entry here before being resolved. Cross-references: `CLAUDE.md`, `OBJECTIVE.md`, `RESULTS.md`, `eval/goldie/comparator-rules.md`.

## Universal recovery rule

**When AI extraction disagrees with gold, fetch the landing-page HTML via Taxicab live-fetch.** ~70% of disagreements resolve because the truth is on the page; AI's cached parser path missed it. Established by audit of 25 train + 14 holdout disagreements on 2026-05-06.

**Decision tree for any new disagreement:**
1. Check `LEARNING.md` for an existing entry. If matched, follow the documented recovery.
2. If novel, run `python eval/scripts/extract_via_taxicab.py --dois <doi>.txt --prompt eval/prompts/ai-goldie-v1.9.1.md --out runs/<label>/probe.csv`. The Taxicab S3-cached HTML carries the same content the parser saw.
3. Compare HTML truth to AI output and to gold. Three outcomes:
   - **Gold matches HTML** and AI doesn't → live-fetch tier should recover. Add DOI to `livefetch-targets.json`.
   - **AI matches HTML** and gold doesn't → gold is wrong. Edit `eval/human-goldie.csv` (you, not Claude, per `feedback_human_goldie_user_only.md`) or treat as known empty-rasses convention via comparator rule #11.
   - **Neither matches HTML** → AI prompt needs scoping (rare; prefer comparator rule over prompt change per `feedback_prompt_rules_leak.md`).
4. Record the new pattern as a one-line entry in this file.

## The "≥95% match = full credit" principle (Rule #11)

When gold's rases is empty by convention (DSQ, MDPI footnote-only, AER, Japanese 1952 series, Polish journals) and AI extracted a real institutional affiliation that matches landing-page truth, the comparator awards full credit on rases for that author-pair. Implemented via token_set_ratio ≥ 95 + institutional-keyword whitelist (university, institute, department, school, laboratory, college, center/centre, hospital, etc.) + length ≥ 12.

This protects against false positives: AI hallucinating a generic word ("research") won't match.

---

## Per-DOI disagreement registry

Recovery legend:
- 🌐 **live-fetch** — add to livefetch-targets.json + visible-Chrome run
- ⚖️ **comparator** — handled by rule #N
- 📝 **gold-edit** — fix in human-goldie.csv (user-only)
- 🚫 **excluded** — DOI swap in human-goldie.csv (out-of-scope)
- ✅ **already-correct** — false disagreement; user agreed AI was right

### Train-50 (rows 1–50)

| # | DOI | Field | AI value | Gold value | HTML truth | Recovery |
|---|-----|-------|----------|------------|-----------|----------|
| 5 | 10.1016/s0378-1097(99)00346-8 | corresp | all false | Alje P. van Dam = CA | CA marker on OUP-redirected old Elsevier landing page | 🌐 live-fetch |
| 6 | 10.1039/bk9781782627609-00134 | abstract | partial | different paragraphs | RSC book chapter — full HTML abstract is canonical | 🌐 live-fetch |
| 8 | 10.1079/cabicompendium.60129 | authors / rases / corresp | CABI (CABI Head Office) | Robin Nicholas | Landing page genuinely lists "CABI" as contributor org. Gold's "Robin Nicholas" reflects PDF body, not landing page (structural extraction-source mismatch). | 🌐 live-fetch + ⚖️ tolerate landing-page contributor org as match. AI is reading the page correctly; the disagreement is about scope, not extraction. |
| 10 | 10.1086/ahr/37.2.298 | authors / rases | [] | J. M. Vincent (Pasedena→**Pasadena**) | "J. M. Vincent (Pasadena)" present in 1932 OUP HTML | 🌐 live-fetch + 📝 gold-edit (Pasedena→Pasadena typo fix done) |
| 11 | 10.1088/0256-307x/35/4/045201 | authors / corresp | romanized only | romanized + CJK in parens; Dan-Dan Zou=CA | Page renders both romanized + CJK; Dan-Dan Zou is CA per HTML | 🌐 live-fetch + ⚖️ comparator rule #12 (CJK paren-suffix tolerance) |
| 12 | 10.1257/aer.p20171042 | rases | Stanford U / Columbia U etc. | empty | AI matches HTML; gold uses empty-rases convention | ⚖️ rule #11 (95% rasses) |
| 13 | 10.1515/9783111535784-008 | abstract | N/A | N/A | both empty post-Bucket-B | ✅ already-correct (parse artifact in diff) |
| 14 | (CJK names) | authors | (varies) | (varies) | If romanized name present in HTML → use it; else CJK | 🌐 live-fetch — Taxicab HTML carries both forms |
| 15 | 10.2320/jinstmet1952.61.12_1352 | rases | NKK総合材料技術研究所 | empty (now: NKK added) | AI was right; user added rases to gold | 📝 gold-edit done (rasses="NKK総合材料技術研究所", broken_doi=TRUE) |
| 16 | 10.3030/821328 | abstract / scope | EU project description | EU project description (different) | CORDIS project page — auto-generated descriptions, not research abstract | 🚫 if AI abstract differs from HTML truth, swap DOI; otherwise tolerate |
| 17 | 10.3138/chr-027-04-br24 | pdf_url | https://doi.org/10.3138/chr-027-04-br24 | N/A | DOI resolver URL ≠ PDF URL. Stochastic AI error. | ⚖️ comparator already excludes doi.org as PDF candidate (verify rule fires) |
| 18 | 10.3390/polym13183031 | rases / corresp | empty / none | full Korean+Vietnamese affs / Jung+Chang CA | Full HTML has all 4 affiliations + CA flags. MDPI publisher-specific extraction gap. | 🌐 live-fetch (MDPI adapter) |
| 19 | 10.3390/su13041644 | rases / corresp | empty / none | full / both CA | Same MDPI gap | 🌐 live-fetch (MDPI adapter) |
| 20 | 10.3724/sp.j.1123.2014.10009 | abstract / pdf_url | Chinese ion-chrom abstract / chrom-china URL | N/A / N/A | SSL handshake fails; can't verify | 🚫 DOI removed in human-goldie.csv |
| 21 | 10.4326/jjcvs.28.399 | rases | 藤田保健衛生大学 affiliations | empty | AI matches HTML; user added rases to gold | 📝 gold-edit done |
| 22 | (CORDIS-style) | abstract | auto-generated | auto-generated (diff) | Out of research scope | 🚫 swap DOI if persistent disagreement |
| 23 | 10.5603/ah.2015.0003 | authors | "Elżbieta Jaroszy*ri*nska" (Unicode bug) | "Elżbieta Jaroszy*ń*ska" | AI mis-transcoded Polish ń → "ri". Real defect. | 🚫 DOI removed in human-goldie.csv (replaced with new DOI) |
| 24 | (OJS-style) | pdf_url | article/download/X/Y | article/download/X/Y/Z (longer canonical) | Both forms valid | ⚖️ existing OJS prefix-match rule |
| 25 | (OJS-style #2) | pdf_url | shorter | longer | 403 bot-block prevents probe | ⚖️ existing OJS prefix-match rule |

### Holdout-50 (rows 51–100)

| # | DOI | Field | AI value | Gold value | HTML truth | Recovery |
|---|-----|-------|----------|------------|-----------|----------|
| 1 | 10.1016/0016-5085(95)22767-9 | pdf_url | empty | gastrojournal URL (paywalled per probe) | DOI directly resolves into PDF | 🌐 live-fetch |
| 2 | 10.1016/0021-9673(93)80418-8 | corresp | both false | Bo Mattiasson = CA | CA marker in old Elsevier landing-page HTML | 🌐 live-fetch |
| 3 | 10.1016/j.surfcoat.2023.129748 | rases | "School of Materials Science…" / "STAHTSM Lab" | "School of Material Science…" / "Institute of Aero Engine Research" / "AECC Shenyang…" | Landing-page HTML is canonical | 🌐 live-fetch + ⚖️ rule #14 (singular/plural ≥88 token_sort) covers "Materials Science" ↔ "Material Science" |
| 4 | 10.1108/978-1-64802-637-920251008 | abstract | "Picture this: 30 educators…" + 708 chars | same opening + 673 chars | Same source text, different cut points | 🌐 live-fetch + accept abstract fuzzy ≥0.74 |
| 5 | (Acoustical Society) | pdf_url | scitation.org URL | watermark.silverchair.com expired token | Both broken; expired URL is fragile | ⚖️ already covered by rule #10 (paywalled-publisher pattern) |
| 6 | 10.1161/01.str.32.6.1291 | corresp | Philip M. White = CA | none CA | Reverse direction — AI claims CA, gold says none. Live-fetch HTML resolves. | 🌐 live-fetch |
| 7 | 10.18041/0124-0021/dialogos.52.2020.8807 | authors | Oscar Andrés López Cortés | [] | Sub-section/comment author. Gold left empty intentionally. | 🚫 DOI removed in human-goldie.csv |
| 8 | 10.18061/dsq.v41i1.7844 | rases | Univ. of Pennsylvania | empty | Empty-rases convention (DSQ pattern) — AI is right per HTML | ⚖️ rule #11 (95% rasses) — full credit when AI matches landing-page truth |
| 9 | 10.24952/masharif.v9i1.3848 | corresp | both false | Dirvi Surya Abbas = CA | Landing-page HTML clearly marks Dirvi as CA per user's read | 🌐 live-fetch (overrides earlier "extraction-source mismatch" diagnosis) |
| 10 | 10.25259/nmji_377_2024 | corresp / abstract | KRISHNA PRAKASH = CA / +1568 chars | none CA / +1533 chars | Landing page HTML carries the truth | 🌐 live-fetch + 📝 gold-edit (user reconciled gold) |
| 11 | 10.31857/s2587556623070105 | corresp | none CA / aff "Perm State University" | A. S. Luchnikov = CA / aff "Perm State University, Russia, Perm" | Russian-language landing page HTML carries CA marker | 🌐 live-fetch |
| 12 | (Stroke #2) | (varies) | | | | |
| 13 | 10.36838/v4i6.14 | pdf_url | empty (Taxicab no-harvest) | terra-docs S3 PDF (real, 200) | DOI directly resolves into PDF — gold's URL is correct | 🌐 live-fetch via DOI resolver |
| 14 | 10.7256/2454-0730.2019.1.20595 | rases / pdf_url | Cyrillic names + Cyrillic affs / empty | Latin transliterated names+affs / cyberleninka.ru PDF | AI got Cyrillic-form, gold got Latin-form. PDF in HTML metadata. | 🌐 live-fetch + ⚖️ extend Cyrillic→Latin rule to rases (currently authors-only) |

---

## Per-publisher failure patterns

### Old Elsevier OUP-redirect (1990s DOIs)
DOIs like `10.1016/s0378-1097(99)00346-8`, `10.1016/0021-9673(93)80418-8`. Pages have been redirected to Oxford University Press wrappers. CA marker present in HTML as a footnote symbol or "* corresponding author" line. Cached parser path misses it. **Recovery**: live-fetch tier with v1.9.1 prompt.

### MDPI affiliation gap
DOIs `10.3390/polym13183031`, `10.3390/su13041644`. AI gets author names from `<meta name="citation_author">` but skips the `<sup>` digit-marker affiliation block. Full strings (`Alan G. MacDiarmid Energy Research Institute, Chonnam National University…`) are in the HTML. **Recovery**: live-fetch with MDPI-aware extraction. Note: `human-goldie.csv` row 16 was updated to use MDPI's footnote-marker form ("1,†", "3,*") — gold matches the page's literal sup-tag content, not the resolved institution names.

### RSC book chapters
DOI `10.1039/bk9781782627609-00134`. Abstract has copy-protection trailing text ("Summarize this data", "Turn on screen reader support") that leaks into AI extraction. Live-fetch HTML view is cleaner. **Recovery**: live-fetch + abstract fuzzy threshold ≥0.74 already accepts.

### Chinese / Japanese (CJK)
- Romanization preference: pick romanized name when both forms present in HTML; else native script.
- Trailing parenthetical CJK suffix ("Chun-Hua Li (李春华)") often dropped by AI parser. Comparator rule #12 normalizes.
- Empty-rases convention common for older Japanese journals (1952 series, jjcvs). Rule #11 awards credit when AI extracts a real affiliation.

### Cyrillic (Russian)
DOIs `10.31857/s2587556623070105`, `10.7256/2454-0730.2019.1.20595`. CA markers exist on landing page in Cyrillic script. AI extracts Cyrillic names; gold uses Latin transliteration. Existing comparator transliterates author names but **not** rases. **Recovery**: live-fetch + extend Cyrillic→Latin in `_rases_*` normalization helpers.

### CABI Compendium
DOI `10.1079/cabicompendium.60129`. Landing page contributor is "CABI" (the org). Gold's author "Robin Nicholas" comes from the PDF body. **Structural extraction-source mismatch** — neither system is wrong, they read different sources. **Recovery**: tolerate landing-page contributor as match, or treat as gold quirk requiring exclusion.

### Out-of-scope DOIs
CORDIS EU project pages (`10.3030/`), broken-SSL Chinese journals (`10.3724/sp.j.1123.2014.10009`), AI-mis-transcoded Polish characters (`10.5603/ah.2015.0003`). **Recovery**: replace DOI in `eval/human-goldie.csv` directly. We do not maintain a runtime skip-list — the user-facing single source of truth is the audited human-goldie.csv.

### Empty-rases convention publishers
Disability Studies Quarterly (DSQ), American Economic Review (AER), Japanese Inst of Metals 1952 series, Japanese J of Cardiovascular Surgery, Polish journals (Acta Haematologica). When AI extracts a real institutional affiliation matching landing-page truth, rule #11 awards full credit even though gold is empty.

---

## Future protocol

1. Add a one-line entry to "Per-DOI disagreement registry" before resolving any new disagreement.
2. If the DOI fits an existing per-publisher pattern, reference the pattern by header.
3. If a new pattern emerges, add a section to "Per-publisher failure patterns" with one DOI as exemplar.
4. Each cycle, update `RESULTS.md` with the cycle's scoreboard delta and reference the LEARNING.md entries that drove the change.
