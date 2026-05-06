# Disagreements — train-50 post-livefetch

28 rows with at least one field mismatch after Bucket B gold flip (6 rows) + Bucket A live-fetch (5 rows). Cycle log: `RESULTS.md` 2026-05-05 entries.

## Summary by bucket

| Bucket | Count | Rows | Path |
|---|---|---|---|
| **D: name/aff content mismatch** | 3 | CABI, CPL CJK-suffix, Acta Haematologica OCR | Articulate-why (no fix) |
| **E: rases-only** | 6 | MicroE superset, AER gold-empty, JINSTMET gold-empty, MDPI×2, JJCVS gold-empty | Gold-convention call |
| **H: pdf_url-only** | 7 | Cell Rep, Nature, AstroJ, IOPscience, KUEY, TJMS, AJESS | Empirical-probe holdover |
| **G/Z: abstract / multi-field** | 3 | JSTOR, FEMS Micro, CHR | One-off |
| **Live-fetch partial** | 5 | Physics Today, IEEE, Project Euclid, Elsevier CLPL, Elsevier JALLCOM | rases-empty or typo residual |
| **Skipped** | 2 | AHR 1932, CORDIS | Need Casey/Shubh call |
| **Remaining Bucket B** | 2 | RSC, Chinese J Chromatography | pdf_url still disagrees |

---

## DOI: 10.1016/j.celrep.2018.10.057

**pdf_url**
- AI:    https://www.cell.com/cell-reports/pdf/S2211-1247(18)31633-6.pdf
- Human: https://www.cell.com/action/showPdf?pii=S2211-1247%2818%2931633-6
- root_cause: same-host different-endpoint; AI uses direct `/pdf/` path, gold uses `/action/showPdf`. Both serve the same PDF.

---

## DOI: 10.1016/j.clpl.2024.100067

**rases**
- AI:    (populated via live-fetch — 3 authors w/ Scuola Superiore Sant'Anna affiliations)
- Human: (populated)
- root_cause: Live-fetch recovered authors+abstract; rases content mismatch on affiliation string normalization (Sant'Anna vs Piazza Martiri address form)

---

## DOI: 10.1016/j.jallcom.2006.06.063

**rases** (partial)
- AI:    3 authors w/ "Bhabha Atomic Research Centre, Trombay, Mumbai" affiliations
- Human: 3 authors w/ full "Bhabha Atomic Research Centre" address
- root_cause: Live-fetch recovered via redirect; affiliation substring vs full form

---

## DOI: 10.1016/j.mee.2007.12.032

**rases**
- AI:    "Microelectronics Research Group, NCSR Demokritos, Institute of Microelectronics, Aghia Paraskevi 15310, Greece"
- Human: "Institute of Microelectronics, NCSR Demokritos, Aghia Paraskevi 15310, Greece"
- root_cause: AI rases is **superset** of gold's. Substring comparator exists but didn't fire — needs investigation.

---

## DOI: 10.1016/s0378-1097(99)00346-8

**corresponding**
- AI:    CA=true on first author
- Human: CA=false on all
- root_cause: OUP-redirected old Elsevier; page ambiguous on corresponding author marker.

---

## DOI: 10.1038/ng1297-370

**pdf_url**
- AI:    https://www.nature.com/articles/ng1297-370.pdf
- Human: N/A
- root_cause: Publisher-canonical-but-paywalled. Same shape as holdout PM directive. HEAD returns 200→HTML after 7 redirects.

---

## DOI: 10.1063/pt.5.6117

**rases**
- AI:    (empty — live-fetch got 1 author name only, no affiliation)
- Human: (populated)
- root_cause: Physics Today wrapper; JS-rendered content has no structured affiliation even after live-fetch.

---

## DOI: 10.1079/cabicompendium.60129

**authors**
- AI:    "CABI" (the organization)
- Human: "Robin Nicholas" (researcher)
- root_cause: CABI Compendium pages list publisher org as contributor; gold pulled actual researcher from attached PDF. Page genuinely shows different info.

---

## DOI: 10.1086/116973

**pdf_url**
- AI:    https://ui.adsabs.harvard.edu/link_gateway/1995AJ....110.2415A/ADS_PDF
- Human: N/A
- root_cause: **Real PDF via NASA ADS gateway.** HEAD returns 200 + application/pdf. Clean gold-flip candidate — AI is correct.

---

## DOI: 10.1086/ahr/37.2.298

**all fields**
- root_cause: SKIPPED. American Historical Review 1932. OUP page genuinely lacks structured authorship metadata. Publisher-page-content limit. **Needs Casey/Shubh call:** accept as known-loss class or revise expectation?

---

## DOI: 10.1088/0253-6102/36/1/109

**pdf_url**
- AI:    https://iopscience.iop.org/article/10.1088/0253-6102/36/1/109/pdf
- Human: N/A
- root_cause: Publisher-canonical-but-paywalled. IOPscience not in rule #10. HEAD returns 200→HTML.

---

## DOI: 10.1088/0256-307x/35/4/045201

**authors**
- AI:    "Chun-Hua Li"
- Human: "Chun-Hua Li (李春华)"
- root_cause: CJK-suffix-in-parentheses pattern. Comparator extension candidate but risks overfit (1 train row).

---

## DOI: 10.1109/icsrs48664.2019.8987669

**rases**
- AI:    "Chongqing Electrionic Engineering Tseting Center" (visible OCR typos)
- Human: "Chongqing Electronic Engineering Testing Center"
- root_cause: Live-fetch cleared bot-block; affiliation has OCR-style typos (`Electrionic`, `Tseting`). Accepted as live-fetch noise rather than tune around.

---

## DOI: 10.1257/aer.p20171042

**rases**
- AI:    "Stanford U"
- Human: (empty)
- root_cause: **Gold-empty rases.** AI extracted from `citation_author_institution`. Same convention question as DSQ on holdout.

---

## DOI: 10.1307/mmj/20236362

**rases**
- AI:    (empty — live-fetch got 2 author names, no affiliations)
- Human: (populated)
- root_cause: Project Euclid JS-rendered; no structured affiliations even after live-fetch.

---

## DOI: 10.2307/3283523

**abstract**
- AI:    (extracted)
- Human: (different content)
- root_cause: JSTOR abstract divergence. Single-row, Z-bucket-shaped one-off.

---

## DOI: 10.2320/jinstmet1952.61.12_1352

**rases**
- AI:    "NKK総合材料技術研究所" (Japanese)
- Human: (empty)
- root_cause: **Gold-empty rases**, Japanese text. Same gold-empty pattern as AER.

---

## DOI: 10.3030/821328

**all fields**
- root_cause: SKIPPED. CORDIS EU project page. Not a journal article — no per-author attribution model. **Needs Casey/Shubh call:** should non-article DOIs be in eval scope?

---

## DOI: 10.3138/chr-027-04-br24

**abstract + pdf_url**
- root_cause: Z-bucket multi-field one-off. Canadian Historical Review.

---

## DOI: 10.3390/polym13183031

**rases**
- AI:    (empty — 4 author names extracted, no rases)
- Human: (full Korean/Vietnamese affiliations)
- root_cause: **MDPI extraction gap.** LLM got names but no rases on 22K-token harvest. Per-publisher extractor would fix but risks overfit (2 train rows, 0 holdout).

---

## DOI: 10.3390/su13041644

**rases**
- AI:    (empty)
- Human: (populated)
- root_cause: Same MDPI extraction gap as polym13183031.

---

## DOI: 10.3724/sp.j.1123.2014.10009

**pdf_url**
- AI:    (empty)
- Human: https://www.chrom-china.com/article/doi/10.3724/SP.J.1123.2014.10009?viewType=HTML
- root_cause: Bucket B gold flip recovered authors/rases; pdf_url still disagrees (AI empty, gold has HTML-view URL).

---

## DOI: 10.4326/jjcvs.28.399

**rases**
- AI:    "藤田保健衛生大学胸部外科" (Fujita Health University Thoracic Surgery)
- Human: (empty)
- root_cause: **Gold-empty rases**, Japanese. Same pattern as JINSTMET.

---

## DOI: 10.53555//kuey.v30i9.5180

**pdf_url**
- AI:    https://kuey.net/index.php/kuey/article/download/5180/5728
- Human: https://kuey.net/index.php/kuey/article/view/5180/5728
- root_cause: Same-host `download/` vs `view/` endpoint. HEAD on AI URL returns **200 + application/pdf** (real PDF). Comparator absorption candidate.

---

## DOI: 10.5603/ah.2015.0003

**authors**
- AI:    "Elżbieta Jaroszyriska"
- Human: "Elżbieta Jaroszyńska"
- root_cause: OCR/encoding artifact: AI saw `ri` where actual character is `ń`. Stochastic.

---

## DOI: 10.62480/tjms.2025.vol42.pp71-73

**pdf_url**
- AI:    https://zienjournals.com/index.php/tjms/article/download/6045/4922
- Human: https://zienjournals.com/index.php/tjms/article/download/6045/4922/5916
- root_cause: Subset match — AI URL is prefix of gold URL. HEAD on AI URL returns **200 + application/pdf**. Comparator absorption candidate.

---

## DOI: 10.9734/ajess/2023/v47i31023

**pdf_url**
- AI:    https://journalajess.com/index.php/AJESS/article/download/1023/1998
- Human: https://journalajess.com/index.php/AJESS/article/download/1023/1998/1621
- root_cause: Same subset pattern as TJMS. HEAD returns 403→HTML (OJS paywall).

---

## DOI: 10.1039/c5ra25098f

**pdf_url**
- AI:    (empty)
- Human: https://pubs.rsc.org/en/content/articlepdf/2016/ra/c5ra25098f
- root_cause: Bucket B gold flip recovered authors; pdf_url still disagrees (AI empty).
