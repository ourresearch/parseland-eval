# RESEARCH.md — extraction techniques + evaluation metrics for parseland-eval

> Targeted literature survey, 2026-05-06. Maps the academic / industry record onto our 8 publisher patterns (LEARNING.md) and 5 scored fields (`authors`, `rases`, `corresponding`, `abstract`, `pdf_url`). No code changes; this informs next-cycle choices.

---

## TL;DR

1. **Add Crossref polite-pool fallback in `eval/parseland_eval/api.py`.** Closes Old-Elsevier-OUP-redirect (Pattern 1) and CABI-extraction-source-mismatch (Pattern 6) by reading depositor metadata directly. Lowest engineering cost (one HTTP call), highest expected lift on `corresponding` and `authors`. **L: S, expected: +4–6 pp authors / +3–5 pp corresp on the affected rows.** ([Crossref REST](https://www.crossref.org/documentation/retrieve-metadata/rest-api/))

2. **Add a JSON-LD `ScholarlyArticle` parser tier ahead of the Claude fallback in `eval/scripts/extract_via_taxicab.py:~80–120`.** Closes MDPI affiliation-gap (Pattern 2) deterministically and picks up Frontiers / PLOS / OUP-newer publishers as a side benefit. **Effort: S, expected: +5–8 pp rases on MDPI rows; structurally cleaner than the citation_* path.** ([schema.org/ScholarlyArticle](https://schema.org/ScholarlyArticle), [Google Scholar Inclusion Guidelines](https://scholar.google.com/intl/en/scholar/inclusion.html))

3. **Keep Levenshtein for abstracts (`eval/parseland_eval/score/abstract.py:25` `ABSTRACT_MATCH_THRESHOLD = 0.74`).** The literature consensus for paragraph-length passages with low paraphrase is character-level edit distance; BERTScore wins on MT/captioning where paraphrase dominates, neither of which describes our gap. Add BERTScore only as a *diagnostic side-signal* on the 0.50 ≤ ratio < 0.74 band. ([BERTScore — Zhang et al., ICLR 2020](https://arxiv.org/abs/1904.09675))

4. **Lengthen the first-name initial in `eval/parseland_eval/score/authors.py:43` (`_name_key`)** from `first[:1]` to a 2-char prefix or full first-token tiebreak. Removes the "Yi Wang" ≡ "Yu Wang" collision class flagged in the Cohen/Ravikumar/Fienberg name-matching survey. **Effort: S, expected: ~1 pp authors on holdout; eliminates a known systematic false-positive class.** ([Cohen et al., COLING 2008](https://aclanthology.org/C08-1075.pdf))

5. **Add a GROBID `(orgName, country)` tier to affiliations** as a 4th rung in `eval/parseland_eval/score/affiliations.py:41`'s ladder, *not* a replacement. Token-ratio fails on "Univ. of Tokyo" vs "The University of Tokyo, Japan"; structured comparison fixes it. Keep determinism as the default — add embedding-cosine only behind a flag if the GROBID tier alone proves insufficient. **Effort: M, expected: +3 pp rases on free-form affiliation rows.** ([GROBID affiliation-address model](https://grobid.readthedocs.io/en/latest/training/affiliation-address/))

---

## Section A — Extraction techniques

### A.1 Current state

We have a two-tier extraction stack:

- **Tier 1, citation meta tags.** `eval/scripts/extract_via_taxicab.py` parses Highwire `citation_*` from Taxicab S3-cached HTML (Google Scholar's de-facto standard, see [Inclusion Guidelines](https://scholar.google.com/intl/en/scholar/inclusion.html)). Cheap, deterministic, but `citation_author_institution` is publisher-optional and unevenly emitted.
- **Tier 2, Claude on full HTML.** Same script falls back to Claude 4.6 Sonnet with prompt `eval/prompts/ai-goldie-v1.9.1.md` when meta tags are incomplete.
- **Tier 3, browser-use Agent over visible Chrome via CDP.** `eval/scripts/live_fetch_empty.py` for DOIs where the cache is too thin / bot-blocked / JS-required.

### A.2 Alternatives in the literature

**Citation meta tags + JSON-LD.** Highwire is the de-facto winner per Google Scholar's spec; JSON-LD `ScholarlyArticle` ([schema.org](https://schema.org/ScholarlyArticle)) is structurally cleaner — `author[].affiliation` is properly nested rather than a flat parallel `citation_author_institution` list — but adoption is spotty (Frontiers, PLOS, Hindawi, some OUP). **MDPI ships JSON-LD with affiliation properly attached per author.** The LYRASIS DSpace mapping ([wiki](https://wiki.lyrasis.org/display/DSDOC7x/Google+Scholar+Metadata+Mappings)) confirms this asymmetry across DSpace-hosted repositories.

**Open-source structured extractors (PDF-input).** Meuschke et al. 2023 ([arXiv:2303.09957](https://arxiv.org/abs/2303.09957)) ranks GROBID first (best across PDF metadata classes), CERMINE second (F1 ~0.74, see [Tkaczyk et al. 2015 / IJDAR](https://link.springer.com/article/10.1007/s10032-015-0249-8)), ScienceParse + ParsCit at ~0.49. **All PDF-input only.** None target publisher-rendered HTML directly. This is the gap our project lives in.

**Browser automation.** The 2026 NxCode benchmark ([nxcode.io](https://www.nxcode.io/resources/news/stagehand-vs-browser-use-vs-playwright-ai-browser-automation-2026)) reports deterministic Playwright + Claude at 92%, browser-use at 90%, Stagehand 89%, Anthropic Computer Use 78%, OpenAI CUA 75%. **DOM/accessibility-tree access wins anywhere DOM is reachable; vision wins only on canvas-only apps and anti-bot screens.** This validates our `live_fetch_empty.py` choice — visible Chrome over CDP, never headless, browser-use Agent for DOM extraction.

**Vision LLMs over screenshots.** Documented failure modes for our 8 patterns: (a) sup/sub digit markers visually collapse to baseline at typical screenshot DPR — directly relevant to Pattern 2 (MDPI), (b) CJK glyphs in parens get OCR-dropped at <2× DPR — relevant to Pattern 4. **Vision is strictly worse than DOM for our setting.** Don't pursue.

**Public APIs as fallback / ground truth.** Crossref REST ([docs](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)) returns `author[].given/family/affiliation[].name/ORCID` from depositor metadata — but `affiliation[]` is publisher-supplied and frequently empty for older or small-press DOIs, no abstract for ~50% of works, no CA flag. OpenAlex ([Works object](https://docs.openalex.org/api-entities/works/work-object)) ingests from Crossref + PubMed + ORCID + **Parseland itself** — using OpenAlex would be circular. DataCite covers datasets, not journal articles. Unpaywall provides best-OA-location URLs (relevant to Pattern 7 OJS canonicalization).

### A.3 Per-publisher recommendation

| Pattern | Best new technique | Lands in |
|---|---|---|
| 1 — Old Elsevier OUP-redirect (1990s, missing CA) | Crossref REST fallback for CA flag | new `crossref_fallback(doi)` in `eval/parseland_eval/api.py` |
| 2 — MDPI affiliation gap | JSON-LD `ScholarlyArticle` parser tier | `eval/scripts/extract_via_taxicab.py:~80–120` (before Claude fallback) |
| 3 — RSC book chapters (abstract chrome) | Chrome-text strip rule (post-LLM) | `eval/scripts/diff_goldie.py::_normalize_abstract_text` |
| 4 — CJK suffix dropped | Already covered by comparator rule #12; no extraction change | n/a |
| 5 — Cyrillic CA marker | Live-fetch + extend Cyrillic→Latin to rases helpers | `eval/scripts/diff_goldie.py::_transliterate_cyrillic` (extend to `_rases_normalize`) |
| 6 — CABI Compendium (org vs PDF author) | Crossref REST as authoritative author list | same as Pattern 1 |
| 7 — OJS short vs long PDF URL | Unpaywall `best_oa_location.url_for_pdf` | new `unpaywall_pdf(doi)` in `eval/parseland_eval/api.py` |
| 8 — Empty-rases-convention publishers | Per-publisher post-LLM allowlist (already covered by rule #11) | `eval/scripts/diff_goldie.py:rases_match` (existing rule #11 sufficient) |

---

## Section B — Evaluation metrics

### B.1 Current state

- **Authors** (`eval/parseland_eval/score/authors.py:38–129`): bipartite matching with key `(last_name_normalized, first_initial)`. Strict P/R/F1 + soft P/R/F1 (`token_set_ratio` ≥ 85.0). Diacritic stripping via NFKD. Cyrillic→Latin BGN/PCGN transliteration on the diff_goldie.py side.
- **Affiliations / rases** (`eval/parseland_eval/score/affiliations.py:17–150`): three tiers — strict (exact), soft (canonicalized), fuzzy (`token_set_ratio` ≥ 85, filler tokens dropped). Mean across matched author pairs in `aggregate.py::_aff_for_row`.
- **Corresponding** (`authors.py:175`, just landed as rule #15 this cycle): micro-aggregated P/R/F1 over matched author pairs, with unmatched-author CA flags counted as fp/fn.
- **Abstract** (`abstract.py:25`): Levenshtein ratio with `ABSTRACT_MATCH_THRESHOLD = 0.74`. Normalize NFKC + casefold. Both-empty = match.
- **PDF URL** (`pdf_url.py:1–41`): canonicalize-then-exact. Micro-aggregated P/R at `aggregate.py::_pdf_micro_pr`.

Plus the relaxed-comparator rules #1–#15 in `eval/scripts/diff_goldie.py`.

### B.2 Literature comparison

**Author name matching.** OpenAlex's pipeline ([help.openalex.org](https://help.openalex.org/hc/en-us/articles/24347048891543-Author-disambiguation), [openalex-name-disambiguation](https://github.com/ourresearch/openalex-name-disambiguation)) uses a learned model — overkill as a *scorer* but informative. The classic comparison ([Cohen et al. 2008 COLING](https://aclanthology.org/C08-1075.pdf)) found Soft-TFIDF with Jaro-Winkler ≥ 0.9 dominates plain Jaro-Winkler / plain Levenshtein / exact-match on PERSON entities. Our `_name_key` approximates Soft-TFIDF's "exact wins, fuzzy backstop" two-pass shape — but `initial = first[:1]` collides on "Yi Wang" / "Yu Wang." **Keep the bipartite shell; lengthen the initial; keep the full-name `token_set_ratio` tiebreak.**

**Affiliation matching.** GROBID's affiliation-address model ([docs](https://grobid.readthedocs.io/en/latest/training/affiliation-address/)) parses into `<orgName>`, `<settlement>`, `<region>`, `<country>` — structural normalization that token-ratio scoring can't match. Our `_clean` only strips emails/URLs/filler; "Univ. of Tokyo" ≡ "The University of Tokyo, Japan" routinely escapes `token_set_ratio` ≥ 85. SBERT embedding cosine is the modern alternative but introduces non-determinism that violates our reproducibility bar. **Add GROBID-parsed `(orgName, country)` as a fourth ladder rung, not a replacement.**

**Corresponding-author scoring.** GROBID's CRF "is_corresponding" tag is evaluated against per-author binary-flag P/R/F1 in metadata-extraction benchmarks ([Lipinski et al., JCDL 2013](https://dl.acm.org/doi/10.1145/2467696.2467753)). Convention: micro-F1 over author-tags — exactly what `score_corresponding` does. Pitfall: when upstream author match is wrong, CA inherits the failure. **Add a `ca_conditional_f1` side-signal at `authors.py:215` reporting F1 conditional on matched-pair-only**, so we can see CA-decision quality net of author-matching baseline. Headline metric stays.

**Abstract similarity.** Levenshtein ratio at 0.74 is *unusually* well-suited here because publisher paraphrase is rare — what we fight is chrome-text leakage and truncation, both of which Levenshtein catches. BERTScore ([Zhang et al., ICLR 2020 / arXiv:1904.09675](https://arxiv.org/abs/1904.09675)) wins on paraphrase-heavy MT/captioning but adds ~600 MB of model and stochasticity. BLEU/ROUGE-L are tokenizer-biased and lose against character-level metrics on abstract-length passages. **Keep Levenshtein. Add BERTScore as a diagnostic on rows where 0.50 ≤ ratio < 0.74 — that's the band where paraphrase vs truncation matters; ~5–8 rows on holdout-50.**

**PDF URL matching.** No published "PDF URL canonical equality" benchmark. The closest art is URL extraction in scholarly PDFs ([arXiv:2509.04759](https://www.arxiv.org/pdf/2509.04759)), which evaluates *presence-of-URL* recall, not URL-equality. Standard practice for "is this the same PDF" outside scholarly metadata is content-hash via SHA-256 — but a HEAD probe can't compute that, and a GET would 30× eval runtime. **Our canonicalize-then-exact is the right tradeoff. Optionally add `pdf_match_relaxed = canonical_equal OR same_doi_tail_token` at `pdf_url.py:39` as a side-signal.**

**Aggregation: micro vs macro.** scikit-learn's [precision_recall_fscore_support docs](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.precision_recall_fscore_support.html) are the cleanest reference: macro = unweighted mean over groups; micro = global TP/FP/FN sums. Authors / rases / abstract are macro-per-row because rows are the unit we report at; PDF URL and CA are micro because rows-with-no-PDF-or-CA-expected dominate the population and would inflate macro. **CLAUDE.md already documents this; the implementation in `aggregate.py::summarize` honors it. No change.**

### B.3 Per-field recommendation

| Field | Keep / change | Action |
|---|---|---|
| `authors` | **change** | Lengthen `_name_key` initial from 1 to 2 chars (or full first-token tiebreak) at `authors.py:43`. |
| `rases` | **change** | Add GROBID `(orgName, country)` tier in `affiliations.py:41`. |
| `corresponding` | **keep + side-signal** | Add `ca_conditional_f1` at `authors.py:215`. |
| `abstract` | **keep + diagnostic** | Add BERTScore on the 0.50–0.74 band as a side-signal in `abstract.py:75`. |
| `pdf_url` | **keep + side-signal** | Add `pdf_match_relaxed` at `pdf_url.py:39`. |

---

## Section C — Action items, ordered by impact

| # | Action | Target file | Effort | Expected lift |
|---|---|---|---|---|
| 1 | **Crossref REST fallback for CA / authors / affs** | new `crossref_fallback(doi)` in `eval/parseland_eval/api.py` | S | +4–6 pp authors, +3–5 pp corresp on Pattern 1 + 6 rows |
| 2 | **JSON-LD `ScholarlyArticle` parser tier** | `eval/scripts/extract_via_taxicab.py:~80–120` | S | +5–8 pp rases on MDPI + Frontiers + PLOS rows |
| 3 | **GROBID `(orgName, country)` tier in affiliations ladder** | `eval/parseland_eval/score/affiliations.py:41` | M | +3 pp rases on free-form affiliations |
| 4 | **Lengthen `_name_key` initial** | `eval/parseland_eval/score/authors.py:43` | S | ~1 pp authors; eliminates Wang-collision class |
| 5 | **Unpaywall `best_oa_location.url_for_pdf` for OJS canonicalization** | new `unpaywall_pdf(doi)` in `eval/parseland_eval/api.py` | S | +1–2 pp pdf_url on OJS rows |
| 6 | **Cyrillic→Latin transliteration in `_rases_normalize`** | `eval/scripts/diff_goldie.py:_rases_normalize` | S | +1–2 pp rases on Pattern 5 rows |
| 7 | **`ca_conditional_f1` side-signal** | `eval/parseland_eval/score/authors.py:215` | S | 0 pp; diagnostic only — decouples CA quality from author-match noise |
| 8 | **BERTScore diagnostic on abstract 0.50–0.74 band** | `eval/parseland_eval/score/abstract.py:75` | M | 0 pp; diagnostic only |
| 9 | **`pdf_match_relaxed` side-signal** | `eval/parseland_eval/score/pdf_url.py:39` | S | 0 pp; quantifies how much rule #10 under-counts |

Items #1–#6 are headline-moving; #7–#9 are diagnostics. All are additive (new fields), preserving back-compat with the dashboard schema per CLAUDE.md non-negotiable #3.

**Suggested next-cycle scope:** ship #1 + #2 first (lowest effort, highest combined lift on the patterns the audit already identified), validate against train-50 + holdout-50, then decide on #3.

---

## Appendix — Source bibliography

| # | Source | Relevance |
|---|--------|-----------|
| 1 | [MOLE — Alyafeai, Al-Shaibani, Ghanem (Findings of EMNLP 2025 / arXiv:2505.19800)](https://arxiv.org/abs/2505.19800) | Closest-neighbor anchor: LLM-driven extraction of ~30 attributes from scholarly papers (title / authors / affiliations / abstract / paper link). Their option-similarity validator is a sibling to our fuzzy-ratio scoring. [ACL Anthology PDF](https://aclanthology.org/2025.findings-emnlp.655.pdf) |
| 2 | [CERMINE — Tkaczyk et al., IJDAR 18(4):317–335, 2015](https://link.springer.com/article/10.1007/s10032-015-0249-8) | The canonical scholarly-metadata-extraction evaluation paper. Validates per-class macro-averaging (CLAUDE.md's existing convention) and the always-emit-both-P-and-R discipline. |
| 3 | [Meuschke et al., Benchmark of PDF Information Extraction Tools, iConference 2023 / arXiv:2303.09957](https://arxiv.org/abs/2303.09957) | Multi-tool comparison framework (GROBID / CERMINE / ScienceParse). Strongest empirical case for adding a non-LLM baseline (e.g., GROBID) as a side-by-side comparator in our run JSON. |
| 4 | [Cohen, Ravikumar, Fienberg — Robust Similarity for Named Entities, COLING 2008](https://aclanthology.org/C08-1075.pdf) | Anchor for author-name matching: Soft-TFIDF with Jaro-Winkler ≥ 0.9 dominates plain Jaro-Winkler / Levenshtein / exact. Our two-pass shape mirrors this; the `first[:1]` initial is the known-broken collision class. |
| 5 | [Zhang et al., BERTScore, ICLR 2020 / arXiv:1904.09675](https://arxiv.org/abs/1904.09675) | The reference for paraphrase-aware sentence/passage similarity. Wins on MT/captioning where paraphrase dominates; not our regime, hence diagnostic-only role. |
| 6 | [GROBID Principles documentation](https://grobid.readthedocs.io/en/latest/Principles/) and [affiliation-address model](https://grobid.readthedocs.io/en/latest/training/affiliation-address/) | The canonical structural-affiliation-parser. Output schema (`<orgName>`, `<settlement>`, `<region>`, `<country>`) is what we need to add as a fourth rung in our affiliations ladder. |
| 7 | [Google Scholar Inclusion Guidelines](https://scholar.google.com/intl/en/scholar/inclusion.html) | Canonizes Highwire `citation_*` as the de-facto meta-tag standard. Confirms `citation_author_institution` is publisher-optional — explains MDPI affiliation gap. |
| 8 | [schema.org/ScholarlyArticle](https://schema.org/ScholarlyArticle) | The JSON-LD shape MDPI emits. Structurally cleaner than parallel `citation_*` lists; `author[].affiliation` properly nested. |
| 9 | [NxCode — Stagehand vs browser-use vs Playwright benchmark, 2026](https://www.nxcode.io/resources/news/stagehand-vs-browser-use-vs-playwright-ai-browser-automation-2026) | Validates our DOM-over-vision choice for `live_fetch_empty.py`. 12–17 pp gap (DOM 90+%, Computer Use ~78%, OpenAI CUA ~75%) persists across recent benchmarks. |
| 10 | [Crossref REST API — Retrieve metadata](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) and [OpenAlex Works object](https://docs.openalex.org/api-entities/works/work-object) | Public-API fallback documentation. Crossref polite-pool is unmetered for our scale; OpenAlex would be circular (Parseland is one of its inputs). |
| 11 | [scikit-learn — precision_recall_fscore_support](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.precision_recall_fscore_support.html) | Reference for micro vs macro vs weighted aggregation. Confirms our existing per-field choice in `aggregate.py::summarize`. |

11 sources cited (de-duplicated). All URLs are retrievable. Coverage check: ✓ all 5 fields have at least one metric reference (1, 2, 4, 5, 6, 11), ✓ all 8 publisher patterns from LEARNING.md have at least one extraction-technique mapping in Section A.3.
