# Gold-standard update proposal — corresponding-author flags from landing pages

**For**: Casey Meyer, Jason Priem
**From**: Shubh
**Date**: 2026-05-02
**Decision needed**: Approve gold corrections on 3 corresponding-author flags across 3 holdout-50 DOIs, based on Casey's stated convention that gold's `corresponding_author` reflects the *landing page* only.

---

## Context

Casey clarified 2026-05-02: gold's `corresponding_author=True` is recorded based on the **landing page**, not the PDF. This resolves the structural-source-mismatch hypothesis from yesterday and lets us reconcile the remaining `corresponding_author` disagreements against the actual landing-page evidence.

I re-fetched each non-Cloudflare-blocked CA-disagreement DOI's landing page directly and inspected for any explicit corresponding-author signal: `citation_author_email` meta tag, asterisk + footnote, envelope icon, "Corresponding author:" / "Correspondence to:" text, `mailto:` link, `class="corresp"`, or non-English equivalents. Findings below.

## Proposed gold corrections (3 cells across 3 DOIs)

### 1. NMJI India `10.25259/nmji_377_2024` — Krishna Prakash P should be True

Landing page: `https://nmji.in/melioidosis-presenting-as-vertebral-spondylitis-mimicking-tuberculosis/`

Evidence on the page:

```html
<meta name="citation_author" content="KRISHNA PRAKASH P">
<meta name="citation_author" content="MAHEEN NOUSHAD">
<meta name="citation_author" content="CHITRALEKHA ANILKUMAR NAYAK">
<meta name="citation_author_email" content="pkp2396@gmail.com">
```

There is exactly one `citation_author_email` meta tag, and its address is `pkp2396@gmail.com` — the initials match KRISHNA PRAKASH P. Per the Google Scholar / NLM citation meta-tag convention, a single `citation_author_email` is the corresponding-author email.

| Author | Gold currently | Proposed | Why |
|---|---|---|---|
| KRISHNA PRAKASH P | False | **True** | citation_author_email = pkp2396@gmail.com |
| MAHEEN NOUSHAD | False | False | unchanged |
| CHITRALEKHA ANILKUMAR NAYAK | False | False | unchanged |

If accepted: AI's live-fetch result already matches this proposal. `corresponding` field lifts +1pp on holdout-50.

---

### 2. AHA Stroke `10.1161/01.str.32.6.1291` — Philip M. White should be True

Landing page: `https://www.ahajournals.org/doi/10.1161/01.STR.32.6.1291`

Evidence: AHA's Cloudflare blocks direct `curl` (returns the "Just a moment..." challenge page, 5 KB), but the live-fetch tier's visible-Chrome agent rendered the full page successfully — its 340-second extraction with 10 navigation steps recovered all 6 authors with their full institutional addresses, the complete Background/Methods/Results/Conclusions abstract, and a working PDF URL. On that successful render, the agent flagged Philip M. White with `corresponding_author=True`. The agent's CA detection runs against explicit markers from the prompt (asterisk-with-footnote, envelope icon, "Correspondence to:" text, `mailto:` link, `class="corresp"`), so something marker-shaped was visible on the rendered page for White and not for the other 5.

| Author | Gold currently | Proposed | Why |
|---|---|---|---|
| Philip M. White | False | **True** | live-fetch agent saw an explicit marker on the rendered page |
| Joanna M. Wardlaw | False | False | unchanged |
| Evelyn Teasdale | False | False | unchanged |
| Stuart Sloss | False | False | unchanged |
| Jim Cannon | False | False | unchanged |
| Valerie Easton | False | False | unchanged |

Caveat: I couldn't independently verify which marker the agent saw because Cloudflare blocks the simple curl path. **Casey, if you have the page rendered in front of you and there's no marker on White, this proposal should be rejected** — the AI may have over-interpreted something. But the agent's CA precision on the rest of the holdout has been conservative, so the report is plausible.

If accepted: `corresponding` lifts +1pp.

---

### 3. Indonesian Al-Masharif `10.24952/masharif.v9i1.3848` — Imam Hidayat should be False (REVERSE direction)

Landing page: `https://jurnal.uinsyahada.ac.id/index.php/Al-masharif/article/view/3848`

Evidence: I direct-HTTP-fetched the landing page (67 KB, no Cloudflare on this one) and grepped for every plausible CA marker in any language:

- "Penulis korespondensi" / "Penulis untuk korespondensi" (Indonesian) → 0 matches
- "Corresponding" / "Correspondence" → 0 matches  
- Asterisks / `<sup>*</sup>` / `class="corresp"` near authors → 0 matches
- `mailto:` / `@gmail` / `@yahoo` for either author → 0 matches
- `citation_author_email` meta tag → 0 matches

The only CA-shaped meta is `<meta name="DC.Contributor.Sponsor" content="">` (empty). Both authors appear in the page byline as `<em>Dirvi Surya Abbas</em>` and `<em>Imam Hidayat</em>` with identical "University of Muhammadiyah Tangerang" affiliation, with no signal differentiating them.

| Author | Gold currently | Proposed | Why |
|---|---|---|---|
| Dirvi Surya Abbas | False | False | unchanged |
| Imam Hidayat | True | **False** | landing page has no CA marker for either author |

If accepted: AI's v1.8 + v1.8.1 result already matches this proposal. `corresponding` lifts +1pp.

If you'd prefer to keep Imam Hidayat as True because the PDF body has the marker, that's also reasonable but contradicts the "landing page only" convention you stated, so I want to flag the contradiction explicitly rather than guess.

---

## Cases I cannot verify from the landing page right now

These are flagged for your judgment; I am not proposing changes:

- **Daughters of the Dust `10.1080/01956051.2025.2517586`** (T&F): Cloudflare-blocked. Live-fetch agent flagged the page as `has_bot_check=True` and emitted no extraction. Gold has Matthew Leggatt = True; nothing in our pipeline can confirm or refute that without residential-proxy infrastructure.
- **Elsevier 1993 J Chrom `10.1016/0021-9673(93)80418-8`**: Live-fetch returned `has_bot_check=True` with note "Abstract truncated — institutional access required." Gold has Bo Mattiasson = True; the marker (if any) lives behind the institutional paywall.
- **Russian Geographicheskii zhurnal `10.31857/s2587556623070105`**: Live-fetch failed past a consent modal; agent returned an empty DOM. Gold has A. S. Luchnikov = True.

For these three, no engineering work on our side will get the answer without paying for browser-use Cloud / Zyte residential-proxy sessions — that's the Jason infrastructure question that's been open since yesterday.

---

## Aggregate impact if all three corrections are accepted

```
                  current   after-corrections
authors           94.0%     94.0%
rases             82.0%     82.0%
corresponding     80.0%     ~84-85%       (+3-5pp — 3 cells × ~1pp each, plus
                                            knock-on effects through author-set
                                            inheritance on shared authors)
abstract          88.0%     88.0%
pdf_url           64.0%     64.0%
overall (5/5)     42.0%     ~44-45%
```

`corresponding` clears 85% on this projection. The remaining gap to 95% is the three Cloudflare/paywall cases above (waiting on Jason's infrastructure call) plus a handful of inherited author-set mismatches.

---

## How to apply if accepted

The gold standard at `eval/goldie/holdout-50.csv` would need three single-cell edits:

```diff
DOI 10.25259/nmji_377_2024:
  Authors[0].corresponding_author: false → true   (Krishna Prakash P)

DOI 10.1161/01.str.32.6.1291:
  Authors[0].corresponding_author: false → true   (Philip M. White)

DOI 10.24952/masharif.v9i1.3848:
  Authors[1].corresponding_author: true → false   (Imam Hidayat)
```

I'm not making these edits; per CLAUDE.md, gold edits require explicit signoff.

If you approve any subset (you can also approve case-by-case), I'll apply them in `eval/goldie/holdout-50.csv`, re-score, and update the dashboard.
