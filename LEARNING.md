# LEARNING.md

Canonical AI-vs-gold disagreement registry for parseland-eval. New disagreements get an entry here before being resolved. Cross-references: `CLAUDE.md`, `OBJECTIVE.md`, `RESULTS.md`, `eval/goldie/comparator-rules.md`.

## Universal recovery rule

**When AI extraction disagrees with gold, fetch the landing-page HTML via Taxicab live-fetch.** ~70% of disagreements have the truth visible on the cached HTML; AI's cached parser path missed it. Established by audit of 25 train + 14 holdout disagreements on 2026-05-06.

**Important caveat (2026-05-07 experiment, RESULTS.md):** "HTML carries the truth" ≠ "v1.9.1 + extract_via_taxicab.py recovers it." A coherent re-extraction with v1.9.1 over the same Taxicab cache recovered only **2 of 13 (15%)** 🔴→🌐-tagged rows in this registry. The 70% claim is about HTML content, not about extractor success. The path forward for the 11 unrecovered rows is per-publisher post-LLM transforms (the leak-safe pattern from holdout) or live_fetch_empty.py (real Chrome) — not just re-running extract_via_taxicab.py. Fresh v1.9.1 runs also introduce ~3-5 rows of stochastic LLM noise per cycle (kanji-variant glyph swaps, dropped abstracts, hallucinated PDF URLs).

**Decision tree for any new disagreement:**
1. Check `LEARNING.md` for an existing entry. If matched, follow the documented recovery.
2. If novel, run `python eval/scripts/extract_via_taxicab.py --source <subset>.csv --prompt eval/prompts/ai-goldie-v1.9.1.md --output-dir runs/<label>`. The Taxicab S3-cached HTML carries the same content the parser saw.
3. Compare HTML truth to AI output and to gold. Three outcomes:
   - **Gold matches HTML** and AI doesn't → live-fetch tier should recover. Add DOI to `livefetch-targets.json`.
   - **AI matches HTML** and gold doesn't → AI was right. Two sub-cases: (a) gold was wrong → user-side gold edit (not Claude — see `feedback_human_goldie_user_only.md`); (b) gold-empty convention → comparator rule #11 (≥95% rasses tolerance) awards full credit.
   - **Neither matches HTML** → out-of-scope DOI; replace in `eval/human-goldie.csv` (CORDIS auto-generated, broken-SSL, AI-Unicode-bug rows all swapped this cycle).
4. Record the new pattern as a one-line entry in this file.

## The "≥95% match = full credit" principle (Rule #11)

When gold's rases is empty by convention (DSQ, MDPI footnote-only, AER, Japanese 1952 series, Polish journals, Disability Studies Quarterly) and AI extracted a real institutional affiliation that matches landing-page truth, the comparator awards full credit on rases for that author-pair. Implemented via token_set_ratio ≥ 95 + institutional-keyword whitelist (university / institute / department / school / laboratory / college / center / hospital / 大学 / 学院 / 研究所 / 대학교 etc.) + length ≥ 12.

This protects against false positives: AI hallucinating a generic word ("research") won't match.

---

## Per-DOI registry

Outcome legend:
- 🔴 **disagree** — AI and gold differ; needs recovery
- 🟢 **align** — AI was right per HTML truth (gold edited, or rule #11 awards credit)
- ⚪ **swap** — DOI replaced in `eval/human-goldie.csv` (out-of-scope)
- ⚫ **no audit note this cycle** — not walked through 2026-05-06; placeholder

Recovery legend:
- 🌐 **live-fetch** — add to livefetch-targets.json + visible-Chrome run
- ⚖️ **comparator rule #N** — handled by added comparator rule
- 📝 **gold-edit-done** — user fixed in human-goldie.csv this cycle
- 🚫 **excluded** — DOI swap

---

### Train-50

#### Train 5/25 — `10.1016/s0378-1097(99)00346-8` 🔴 disagree → 🌐 live-fetch
**Field:** `corresponding`. AI marks all 3 authors `corresponding_author: false`. Gold marks `Alje P. van Dam` as CA. Same 3 authors, same affiliations (Academic Medical Centre, Univ. Amsterdam) on both sides — only the CA flag differs.
**Mechanism:** OUP-redirected old Elsevier landing page hides the CA marker behind an asterisk-footnote pattern that the cached parser path doesn't read.
**HTML truth (verified by user):** "When I went to fetch the HTML page source, as a human I was able to extract the correct CA."
**Recovery:** Live-fetch tier with v1.9.1 prompt picks up the asterisk-footnote on a real DOM render.

---

#### Train 6/25 — `10.1039/bk9781782627609-00134` 🔴 disagree → 🌐 live-fetch
**Field:** `abstract`. Both contain identical first ~400 chars; AI continues with one paragraph, gold with a different paragraph. RSC book chapter.
**HTML truth (verbatim, via user's RSC HTML probe):**
> There are numerous inheritance patterns involving autosomes and sex chromosomes. This chapter discusses the consequences of possessing, in a pair of homologous chromosomes, two mutant alleles or one mutant allele. Dominant or non-dominant (recessive) relationships in autosomes and sex chromosomes are illustrated with a number of human diseases and animal characteristics. Examples discussed include hemophilia in humans and animals, roan cows and palomino horses, cheetahs, lethal alleles, yellow mice, mitochondrial inheritance and Richard III.

(The "Summarize this data" / "Turn on screen reader support" trailing text in the HTML is RSC's screen-reader chrome; live-fetch needs to strip it.)
**Recovery:** Live-fetch + abstract fuzzy threshold ≥0.74 already accepts this once the chrome trailing text is stripped.

---

#### Train 8/25 — `10.1079/cabicompendium.60129` (CABI Compendium) 🔴 disagree → 🌐 live-fetch + ⚖️ tolerate landing-page contributor
**Field:** `authors`, `rases`, `corresponding`. AI: `CABI` (CABI Head Office, Wallingford, UK). Gold: `Robin Nicholas` (no aff, marked CA).
**Mechanism:** Landing page lists "CABI" as the contributor org; gold's "Robin Nicholas" comes from the PDF body. Structural extraction-source mismatch.
**HTML truth (verbatim via user's Taxicab probe):**
```json
[
  {
    "name": "Ralph G Wilkins",
    "rasses": "New Mexico State University, USA",
    "corresponding_author": false
  }
]
```
**Note:** HTML carries a third option (Ralph G Wilkins) that matches neither AI nor gold — all three are different. CABI Compendium is consistently structurally complicated; treat as a known failure pattern.
**Recovery:** Either accept landing-page contributor as match (loose), exclude these from scoring, or live-fetch the PDF directly.

---

#### Train 10/25 — `10.1086/ahr/37.2.298` (1932 American Historical Review) 🟢 align → 🌐 live-fetch + 📝 gold-edit-done (Pasedena→Pasadena)
**Field:** `authors`, `rases`. AI: `[]`. Gold: `J. M. Vincent (Pasedena)` → user fixed typo to `Pasadena`.
**HTML truth (verbatim):**
```json
[
  {
    "name": "J. M. Vincent",
    "rasses": "Pasadena",
    "corresponding_author": false
  }
]
```
**Mechanism:** 1932 OUP page is genuinely sparse; cache was thin (~4075 tokens). Live-fetch was skipped on this row as structurally unrecoverable. Gold's "J. M. Vincent (Pasadena)" matches HTML — gold is right, AI cache too thin.
**Recovery:** Live-fetch tier recovers from the live HTML.

---

#### Train 11/25 — `10.1088/0256-307x/35/4/045201` (Chinese Phys Lett) 🔴 disagree → 🌐 live-fetch + ⚖️ rule #12 (CJK paren suffix)
**Fields:** `authors` (CJK suffix), `rases`, `corresponding` (Dan-Dan Zou CA flag).
**HTML truth (verbatim via user's Taxicab probe):**
```json
[
  {
    "name": "Chun-Hua Li",
    "rasses": "Department of Information Engineering, Hefei University of Technology, Hefei 230009",
    "corresponding_author": false
  },
  {
    "name": "Shao-Wei Wang",
    "rasses": "Department of Information Engineering, Hefei University of Technology, Hefei 230009",
    "corresponding_author": false
  },
  {
    "name": "Yun-Hao Liu",
    "rasses": "Department of Information Engineering, Hefei University of Technology, Hefei 230009",
    "corresponding_author": false
  },
  {
    "name": "Zhen-Wei Xia",
    "rasses": "School of Information Engineering, North China University of Water Resources and Electric Power, Zhengzhou 450046",
    "corresponding_author": false
  },
  {
    "name": "Xiao-Hui Zhang",
    "rasses": "Department of Information Engineering, Hefei University of Technology, Hefei 230009",
    "corresponding_author": false
  },
  {
    "name": "Dan-Dan Zou",
    "rasses": "School of Electrical and Automation Engineering, East China Jiaotong University, Nanchang 330013",
    "corresponding_author": true
  }
]
```
**Mechanism:** Page renders both Romanized + CJK in parens. AI dropped CJK suffix and missed CA marker on Dan-Dan Zou.
**Recovery:** Live-fetch + rule #12 (CJK paren stripping in `normalize_name`).

---

#### Train 12/25 — `10.1257/aer.p20171042` (American Economic Review) 🟢 align → ⚖️ rule #11 (95% rasses, gold-empty convention)
**Field:** `rases`. AI: real institutional affiliations. Gold: 4 authors with empty rases.
**HTML truth (verbatim, AI was right):**
```json
[
  {
    "name": "Susan Athey",
    "rasses": "Stanford U",
    "corresponding_author": false
  },
  {
    "name": "Guido Imbens",
    "rasses": "Stanford U",
    "corresponding_author": false
  },
  {
    "name": "Thai Pham",
    "rasses": "Stanford U",
    "corresponding_author": false
  },
  {
    "name": "Stefan Wager",
    "rasses": "Columbia U and Stanford U",
    "corresponding_author": false
  }
]
```
**User confirmation:** "Ai was right".
**Recovery:** Rule #11 awards full credit on rases when gold is empty AND AI matches landing-page truth (length ≥ 12 + institutional-keyword whitelist).

---

#### Train 13/25 — `10.1515/9783111535784-008` (De Gruyter chapter) 🟢 align (parse artifact)
**Field:** `abstract`. AI: `N/A` literal string. Gold: `N/A`. Both effectively empty post-Bucket-B.
**User confirmation:** "You are right." — parse artifact in diff, not a real disagreement.
**Recovery:** None needed; comparator already accepts both-empty as match.

---

#### Train 14 — CJK names general protocol 🟢 align (protocol clarification)
**User directive:** "In case if there is non-english names, there could be two options, roman name available or roman name not available — if available please pick roman name else pick non-english name, purely based out of the html (via the Taxicab live fetch)."
**Implementation:** AI Goldie prompt v1.9.1 already prefers romanized when both forms are present in HTML. Comparator rule #12 strips parenthetical CJK suffixes so both forms compare-equal.

---

#### Train 15/25 — `10.2320/jinstmet1952.61.12_1352` (Japanese Inst of Metals 1952) 🟢 align → 📝 gold-edit-done (rasses added)
**Field:** `rases`. AI: `NKK総合材料技術研究所` for both authors. Gold (pre-edit): empty.
**HTML truth (verbatim, AI was right):**
```json
[
  {
    "name": "高木 真一",
    "rasses": "NKK総合材料技術研究所",
    "corresponding_author": false
  },
  {
    "name": "大内 千秋",
    "rasses": "NKK総合材料技術研究所",
    "corresponding_author": false
  }
]
```
**User confirmation:** "this is right in this case" — gold updated to add NKK rasses + flip `broken_doi` to TRUE.

---

#### Train 16/25 — `10.3030/821328` (CORDIS EU project page) ⚪ swap-pending
**Field:** `abstract`. AI: auto-generated EU project description. Gold: similar but different framing.
**Authoritative HTML abstract (verbatim from CORDIS):**
> Advanced electrical power distribution networks that reduce aircraft wiring are crucial for future more-electric aircraft. The EU-funded IDEN project aims to develop an innovative electrical power distribution network using solid-state transformers. The network will form part of a decentralized, modular, flexible smart-grid system capable of managing energy more efficiently, including reducing or eliminating generator overload. The new design is intended to be integrated into the Iron Bird test rig for regional aircraft.

**User directive:** "If the AI found abstract is different from this then lets change the doi itself."
**Recovery:** Conditional swap — if AI's extraction diverges from this canonical, replace the DOI in human-goldie.csv. Not yet swapped.

---

#### Train 17/25 — `10.3138/chr-027-04-br24` (UTP Canadian Historical Review book review) 🔴 disagree → ⚖️ comparator already excludes doi.org as PDF
**Field:** `pdf_url`. AI: `https://doi.org/10.3138/chr-027-04-br24` (DOI resolver URL — not a PDF). Gold: `N/A`.
**Mechanism:** Stochastic AI error (DOI resolver URL leaked into pdf_url field).
**User directive:** "It does not have abstract, authors, only PDF url on the html page, and when on html page, sometimes there might be several but anyone always work."
**Recovery:** Existing comparator rejects doi.org as PDF candidate. Verify rule fires.

---

#### Train 18/25 — `10.3390/polym13183031` (MDPI Polymers) 🔴 disagree → 🌐 live-fetch (MDPI adapter)
**Field:** `rases` (all empty), `corresponding` (none flagged). Gold has full Korean+Vietnamese affs + 2 CAs flagged.
**HTML truth (verbatim, but with rasses left blank intentionally for MDPI footnote-marker convention):**
```json
[
  {
    "name": "Vinh Van Tran",
    "rasses": "",
    "corresponding_author": false
  },
  {
    "name": "Truong Thi Vu Nu",
    "rasses": "",
    "corresponding_author": false
  },
  {
    "name": "Hong-Ryun Jung",
    "rasses": "",
    "corresponding_author": true
  },
  {
    "name": "Mincheol Chang",
    "rasses": "",
    "corresponding_author": true
  }
]
```
**Note:** Gold row 16 in human-goldie.csv now uses MDPI's footnote-marker convention (`"1,†"`, `"3,*"`) for rasses. CA flags are still recoverable from HTML.
**Recovery:** Live-fetch tier with MDPI-aware extraction recovers CA flags.

---

#### Train 19/25 — `10.3390/su13041644` (MDPI Sustainability) 🔴 disagree → 🌐 live-fetch (MDPI adapter)
**Field:** `rases` (empty), `corresponding` (none flagged). Gold marks both authors as CA.
**HTML truth (verbatim, full institutional addresses present in HTML):**
```json
[
  {
    "name": "Alpaslan Kelleci",
    "rasses": "Department of Business Administration, Faculty of Economics, Administrative and Social Sciences, Istanbul Gelisim University, Istanbul 34310, Turkey",
    "corresponding_author": true
  },
  {
    "name": "Oğuz Yıldız",
    "rasses": "Department of Aviation Management, Faculty of Economics, Administrative and Social Sciences, Istanbul Gelisim University, Istanbul 34310, Turkey",
    "corresponding_author": true
  }
]
```
**User confirmation:** "again I got it from the html via the Taxicab".
**Recovery:** Live-fetch + MDPI-aware extractor.

---

#### Train 20/25 — `10.3724/sp.j.1123.2014.10009` (Chinese J Chrom) ⚪ swap → 🚫 DOI removed
**Field:** `abstract`, `pdf_url`. AI: Chinese ion-chrom abstract / chrom-china.com URL. Gold: `N/A` / `N/A`.
**Mechanism:** SSL handshake failure prevents URL probe; can't verify content.
**User directive:** "We are removing it." → DOI removed from `eval/human-goldie.csv` this cycle.

---

#### Train 21/25 — `10.4326/jjcvs.28.399` (Japanese J of Cardiovascular Surgery) 🟢 align → 📝 gold-edit-done (rasses added)
**Field:** `rases`. AI: 8 authors with `藤田保健衛生大学胸部外科` / `藤田保健衛生大学短期大学`. Gold: empty.
**User confirmation:** "yes you are right, I have added the rasses." — gold updated.

---

#### Train 22/25 — CORDIS-style scope rule 🟢 align (protocol clarification)
**User directive:** "If the AI found abstract is different from this then lets change the doi itself."
**Implementation:** Out-of-scope DOIs (CORDIS, project descriptions, non-research auto-generated content) get swapped in human-goldie.csv rather than scored. No comparator rule needed.

---

#### Train 23/25 — `10.5603/ah.2015.0003` (Acta Haematologica Polonica) ⚪ swap → 🚫 DOI removed/replaced
**Field:** `authors`. AI: 5 authors with `Elżbieta Jaroszy*ri*nska` (Unicode bug — Polish ń mis-transcoded as "ri"). Gold: same 5 names with correct ń. AI defect.
**User directive:** "we deleted this and added a new DOI in teh goldie." — DOI replaced this cycle.

---

#### Train 24–25 — OJS URL suffix patterns 🔴 disagree → ⚖️ existing OJS prefix-match rule
**Field:** `pdf_url`. AI: `article/download/X/Y`. Gold: `article/download/X/Y/Z` (longer canonical with galley ID).
**Mechanism:** Both forms are valid OJS URLs; gold uses the longer canonical including galley ID.
**Recovery:** Existing OJS prefix-match rule treats both as equivalent.

---

### Holdout-50

#### Holdout 1/14 — `10.1016/0016-5085(95)22767-9` (Gastrojournal '95) 🟢 align → 🌐 live-fetch
**Field:** `pdf_url`. AI: empty. Gold: `gastrojournal.org/article/0016-5085(95)22767-9/pdf`.
**User directive:** "This directly resolves into a PDF" — gold's URL is a real PDF.
**Recovery:** Live-fetch via DOI resolver picks it up.

---

#### Holdout 2/14 — `10.1016/0021-9673(93)80418-8` (J of Chromatography 1993) 🔴 disagree → 🌐 live-fetch
**Field:** `corresponding`. AI: both authors `corresponding_author: false`. Gold: `Bo Mattiasson` = CA.
**Mechanism:** Old Elsevier OUP-redirect — same pattern as train 5. CA marker is in HTML but cached parser path misses it.
**User directive:** "I got the right information mentioned in teh Gold via the html, please use Taxicab."
**Recovery:** Live-fetch with v1.9.1 prompt.

---

#### Holdout 3/14 — `10.1016/j.surfcoat.2023.129748` (Surface & Coatings Tech, Beihang) 🔴 disagree → 🌐 live-fetch
**Field:** `rases`. 8 authors, names match. AI: `School of Materials Science and Engineering` + `Science and Technology on Advanced High Temperature Structural Materials Lab`. Gold: `School of Material Science and Engineering` + `Institute of Aero Engine Research` + `AECC Shenyang Engine Research Institute`. Substantively different lab assignments.
**User directive:** "the correct one is something is that mentioned int he html of the landing page."
**Recovery:** Live-fetch — landing-page HTML is the canonical source.

---

#### Holdout 4/14 — `10.1108/978-1-64802-637-920251008` (Emerald book chapter) 🔴 disagree → 🌐 live-fetch
**Field:** `abstract`. Both have identical opening ("Picture this: 30 educators and 5 researchers…"). AI: +708 chars. Gold: +673 chars.
**Mechanism:** Same source, different cut points / fuzzy threshold drift.
**User directive:** "please again check the html taxicab — live fetch."
**Recovery:** Live-fetch + abstract fuzzy ≥0.74 already accepts.

---

#### Holdout 5/14 ⚫ no audit note this cycle 2026-05-06
User did not write a section for holdout 5 in the 2026-05-06 walkthrough. Per prior memory (project_residual_failures_2026_05_04.md) holdout-5 was the Acoustical Society / scitation.org vs watermark.silverchair.com expired-token case, already covered by comparator rule #10 (paywalled-publisher pattern). Carrying forward.

---

#### Holdout 6/14 — `10.1161/01.str.32.6.1291` (Stroke journal) 🔴 disagree → 🌐 live-fetch
**Field:** `corresponding`. Same 6 authors. AI: marks `Philip M. White` = CA. Gold: marks none.
**Mechanism:** Reverse direction from typical — AI claims CA, gold says none. Live-fetch resolves which is right.
**User directive:** "Please check the html, fetch it live with taxicab."
**Recovery:** Live-fetch.

---

#### Holdout 7/14 — `10.18041/0124-0021/dialogos.52.2020.8807` (Diálogos OJS) ⚪ swap → 🚫 DOI removed
**Field:** `authors`, `rases`, `corresponding`. AI: `Oscar Andrés López Cortés (Universidad Libre)`. Gold: `[]`.
**Mechanism:** Sub-section/comment author. Gold left empty intentionally; post-LLM transforms didn't catch this row.
**User directive:** "we got rid of it, remember again the taxicab, via html." — DOI removed this cycle, replaced with `10.3389/fcimb.2020.00307`.

---

#### Holdout 8/14 — `10.18061/dsq.v41i1.7844` (Disability Studies Quarterly) 🟢 align → ⚖️ rule #11 (95% rasses, full credit)
**Field:** `rases`. AI: `Amanda DiLodovico (University of Pennsylvania)`. Gold: `Amanda DiLodovico (∅)` empty (DSQ pattern).
**Mechanism:** DSQ empty-rases convention.
**User directive:** "still qualifies a good match so full point in accuracy please."
**User principle:** "lets think this if there is more than 95% mathc then its get 100% mark on accuracy match."
**Recovery:** Rule #11 awards full credit when AI's rasses contains an institutional keyword + length ≥ 12.

---

#### Holdout 9/14 — `10.24952/masharif.v9i1.3848` (Indonesian, Masharif) 🔴 disagree → 🌐 live-fetch
**Field:** `corresponding`. AI: both authors no CA flag. Gold: `Dirvi Surya Abbas` = CA.
**HTML truth (verbatim via user's Taxicab probe — overrides earlier "extraction-source mismatch" diagnosis):**
```json
[
  {
    "name": "Dirvi Surya Abbas",
    "rasses": "University of Muhammadiyah Tangerang, Indonesia",
    "corresponding_author": true
  },
  {
    "name": "Imam Hidayat",
    "rasses": "University of Muhammadiyah Tangerang, Indonesia",
    "corresponding_author": false
  }
]
```
**Recovery:** Live-fetch — the HTML page clearly carries the CA marker per user's verification.

---

#### Holdout 10/14 — `10.25259/nmji_377_2024` (NMJI, Indian medical) 🟢 align → 📝 gold-edit-done
**Field:** `corresponding` (AI marks `KRISHNA PRAKASH P` = CA, gold none); `abstract` (lengths +1568 vs +1533, substantively similar).
**User directive:** "you are right, I updated the Goldie. Please refer always what is mentioned in the html via Taicab live fetch." — gold updated to match landing-page CA.

---

#### Holdout 11/14 — `10.31857/s2587556623070105` (Russian, Perm State) 🔴 disagree → 🌐 live-fetch
**Field:** `corresponding`, `rases`. AI: 4 authors no CA + `Perm State University`. Gold: `A. S. Luchnikov` = CA + `Perm State University, Russia, Perm` (longer aff suffix).
**HTML truth (verbatim via user's Taxicab probe):**
```json
[
  {
    "name": "A. S. Luchnikov",
    "rasses": "Perm State University",
    "corresponding_author": true
  },
  {
    "name": "A. A. Lyadova",
    "rasses": "Perm State University",
    "corresponding_author": false
  },
  {
    "name": "S. A. Merkushev",
    "rasses": "Perm State University",
    "corresponding_author": false
  },
  {
    "name": "R. S. Nikolaev",
    "rasses": "Perm State University",
    "corresponding_author": false
  }
]
```
**Mechanism:** Russian-language landing page carries CA marker; AI parser missed it. Aff-suffix difference between AI and gold (both have `Perm State University` core; gold appends `, Russia, Perm`) — comparator should treat as match via existing substring rule.
**Recovery:** Live-fetch picks up CA flag; existing rule handles aff suffix.

---

#### Holdout 12/14 ⚫ no audit note this cycle 2026-05-06
User did not write a section for holdout 12 in the 2026-05-06 walkthrough. Carrying forward whatever the prior baseline disagreement (if any) was; treat as unverified until next audit cycle. **Action item:** verify in next live-fetch run whether any field disagreement remains.

---

#### Holdout 13/14 — `10.36838/v4i6.14` (Terra-docs IJHSR) 🟢 align → 🌐 live-fetch
**Field:** `pdf_url`. AI: empty (Taxicab no-harvest). Gold: `terra-docs.s3.us-east-2.amazonaws.com/.../2022_46_p80_Nguyen.pdf` (real PDF, 200 + application/pdf, 485 KB).
**User directive:** "This doi directly got resolved into PDF so we dont any other information."
**Recovery:** Live-fetch via DOI resolver finds the S3 PDF directly.

---

#### Holdout 14/14 — `10.7256/2454-0730.2019.1.20595` (Cyberleninka, Russian Servicology) 🔴 disagree → 🌐 live-fetch + ⚖️ extend Cyrillic→Latin to rases
**Fields:** `rases` (AI Cyrillic, gold Latin transliteration); `pdf_url` (AI empty, gold cyberleninka.ru PDF).
**Mechanism:** AI extracted Cyrillic-script names + Cyrillic affs. Gold has Latin-transliterated. Existing comparator transliterates author names but **not rases**.
**User confirmation:** "Here I fetched whatever was there in the html, metadata, and fetching it via the Taxicab live fetch."
**Recovery:** Live-fetch picks up the PDF; comparator should extend Cyrillic→Latin transliteration to rases helpers.

---

## Per-publisher failure patterns

### Old Elsevier OUP-redirect (1990s DOIs)
Examples: `10.1016/s0378-1097(99)00346-8`, `10.1016/0021-9673(93)80418-8`.
**Mechanism:** Pages redirected to Oxford University Press wrappers. CA marker present in HTML as a footnote symbol or "* corresponding author" line. Cached parser path misses it.
**Recovery:** Live-fetch tier with v1.9.1 prompt.

### MDPI affiliation gap
Examples: `10.3390/polym13183031`, `10.3390/su13041644`.
**Mechanism:** AI gets author names from `<meta name="citation_author">` but skips the `<sup>`-digit-marker affiliation block. Full institutional strings are in the HTML.
**Note:** Gold row 16 (polym13183031) was updated to use MDPI's footnote-marker form (`"1,†"`, `"3,*"`) — gold matches the page's literal `<sup>` content, not the resolved institution names.
**Recovery:** Live-fetch with MDPI-aware extraction.

### RSC book chapters
Example: `10.1039/bk9781782627609-00134`.
**Mechanism:** Abstract has copy-protection trailing text ("Summarize this data", "Turn on screen reader support") that leaks into AI extraction. Live-fetch HTML view is cleaner once chrome is stripped.
**Recovery:** Live-fetch + abstract fuzzy threshold ≥0.74.

### Chinese / Japanese (CJK)
Examples: `10.1088/0256-307x/35/4/045201`, `10.2320/jinstmet1952.61.12_1352`, `10.4326/jjcvs.28.399`.
- **Romanization preference:** pick romanized name when both forms present in HTML; else native script.
- **Trailing parenthetical CJK suffix** ("Chun-Hua Li (李春华)") often dropped by AI parser. Comparator rule #12 normalizes.
- **Empty-rases convention** common for older Japanese journals (1952 series, jjcvs). Rule #11 awards credit when AI extracts a real affiliation.

### Cyrillic (Russian)
Examples: `10.31857/s2587556623070105`, `10.7256/2454-0730.2019.1.20595`.
**Mechanism:** CA markers exist on landing page in Cyrillic script. AI extracts Cyrillic names; gold uses Latin transliteration. Existing comparator transliterates author names but **not rases**.
**Recovery:** Live-fetch + extend Cyrillic→Latin to `_rases_*` normalization helpers.

### CABI Compendium
Example: `10.1079/cabicompendium.60129`.
**Mechanism:** Landing page contributor is "CABI" (the org). Gold's author "Robin Nicholas" comes from PDF body. HTML carries a third option ("Ralph G Wilkins") that matches neither AI nor gold. Structural extraction-source mismatch with three competing answers.
**Recovery:** Either tolerate landing-page contributor as match, exclude these from scoring, or live-fetch the PDF.

### Out-of-scope DOIs (replaced this cycle)
- `10.3030/821328` (CORDIS EU project page — auto-generated description)
- `10.3724/sp.j.1123.2014.10009` (broken SSL Chinese Chrom)
- `10.5603/ah.2015.0003` (AI mis-transcoded Polish ń → "ri" — real defect)
- `10.18041/0124-0021/dialogos.52.2020.8807` (Diálogos sub-section author)
**Recovery:** Replace DOI in `eval/human-goldie.csv` directly. We do not maintain a runtime skip-list — the user-facing single source of truth is the audited human-goldie.csv.

### Empty-rases convention publishers
DSQ, AER, Japanese Inst of Metals 1952 series, Japanese J of Cardiovascular Surgery, Polish journals (Acta Haematologica). When AI extracts a real institutional affiliation matching landing-page truth, rule #11 awards full credit even though gold is empty.

---

## Live-fetch tier checklist (next cycle Phase C)

The 22 audit-flagged DOIs that should be added to `livefetch-targets.json` and re-extracted with a visible Chrome over CDP. After live-fetch, merge the delta into `runs/train-final/ai-goldie-1.merged.csv` / `runs/holdout-v1.9.1/ai-goldie-1.csv` and re-score.

### Train (12 DOIs)

| # | DOI | Reason | Expected lift |
|---|-----|--------|---------------|
| 5 | `10.1016/s0378-1097(99)00346-8` | `cache=oup-redirect-stale-elsevier-CA` | corresp +1 |
| 6 | `10.1039/bk9781782627609-00134` | `cache=rsc-book-chapter-screenreader-chrome` | abstract +1 |
| 8 | `10.1079/cabicompendium.60129` | `cache=cabi-extraction-source-mismatch` | authors / rases / corresp (?) |
| 10 | `10.1086/ahr/37.2.298` | `cache=1932-oup-thin-cache` | authors +1 (gold matches HTML) |
| 11 | `10.1088/0256-307x/35/4/045201` | `cache=cjk-suffix-dropped` | authors / rases / corresp +1 |
| 12 | `10.1257/aer.p20171042` | `cache=aer-empty-rases-convention` | rule #11 in flight |
| 15 | `10.2320/jinstmet1952.61.12_1352` | gold updated; verify post-edit | rule #11 in flight |
| 17 | `10.3138/chr-027-04-br24` | `cache=stochastic-doi-resolver-as-pdf` | pdf_url +1 |
| 18 | `10.3390/polym13183031` | `cache=mdpi-aff-empty` | corresp +1 (rule #11 covers rases) |
| 19 | `10.3390/su13041644` | `cache=mdpi-aff-empty` | corresp +1 (rule #11 covers rases) |
| 21 | `10.4326/jjcvs.28.399` | gold updated; verify | rule #11 in flight |
| — | (OJS rows) | existing prefix-match rule | already accepted |

### Holdout (10 DOIs — single-shot, comparator freezes after)

| # | DOI | Reason | Expected lift |
|---|-----|--------|---------------|
| 1 | `10.1016/0016-5085(95)22767-9` | `cache=gastrojournal-pdf-direct` | pdf_url +1 |
| 2 | `10.1016/0021-9673(93)80418-8` | `cache=oup-redirect-stale-elsevier-CA` | corresp +1 |
| 3 | `10.1016/j.surfcoat.2023.129748` | `cache=beihang-aff-strings` | rases +1 |
| 4 | `10.1108/978-1-64802-637-920251008` | `cache=emerald-book-abstract-cut` | abstract +1 |
| 6 | `10.1161/01.str.32.6.1291` | `cache=stroke-CA-marker-direction` | corresp ±1 |
| 8 | `10.18061/dsq.v41i1.7844` | rule #11 covers; verify | already accepted |
| 9 | `10.24952/masharif.v9i1.3848` | `cache=masharif-CA-marker` | corresp +1 |
| 10 | `10.25259/nmji_377_2024` | gold updated; verify | already aligned |
| 11 | `10.31857/s2587556623070105` | `cache=cyrillic-CA-marker` | corresp +1 |
| 13 | `10.36838/v4i6.14` | `cache=terra-docs-S3-pdf-direct` | pdf_url +1 |
| 14 | `10.7256/2454-0730.2019.1.20595` | `cache=cyberleninka-cyrillic-rases` | rases / pdf_url +1 |

---

## Future protocol

1. Add a one-line entry to "Per-DOI registry" before resolving any new disagreement.
2. If the DOI fits an existing per-publisher pattern, reference the pattern by header.
3. If a new pattern emerges, add a section to "Per-publisher failure patterns" with one DOI as exemplar.
4. Each cycle, update `RESULTS.md` with the cycle's scoreboard delta and reference the LEARNING.md entries that drove the change.
5. **Verbatim HTML truth blocks** stay in this file. If user pastes a JSON dump from a Taxicab probe, preserve it inline rather than summarizing — it is the most valuable artifact of the audit.
