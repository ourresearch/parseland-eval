# Gold-standard update proposal — affiliations from `citation_author_institution`

**For**: Casey Meyer, Jason Priem
**From**: Shubh
**Date**: 2026-04-30
**Decision needed**: Approve gold updates on 13 author-rows (3 DOIs spanning 3 publishers) so AI extraction matches the auditor record on these cases.

---

## TL;DR (the direct answer to Casey's 4/30 question)

> "How many more affiliations would we get correct if we updated the gold standard to use `citation_author_institution` as the primary source so that when AI finds that data it matches the gold standard?"

**13 author-level affiliations**, across **3 DOIs** spanning **3 publishers**.

| Counting | Currently passing | After gold update | Δ |
|---|---|---|---|
| Per-author rases | 40 / 115 = 35% | 53 / 115 = **46%** | **+13 authors, +11pp** |
| Per-DOI rases (current AND aggregation) | 18 / 50 = 36% | 20 / 50 = **40%** | +2 DOIs, +4pp |

The per-DOI lift is smaller (+2) because most "AI=meta" cases are concentrated on DOIs where *other* authors fail in *different* ways. Updating gold for the meta-matched authors doesn't flip the whole DOI to pass when the per-DOI scorer uses AND across authors.

**The proposal narrows gold** — every change drops detail (postal codes, secondary affiliations, role prefixes) the auditor recorded but the publisher's structured metadata does not carry. Whether that's acceptable depends on what CDL is paying for.

---

## All 13 author-row edits (grouped by publisher)

All 13 cases follow the same pattern: AI v1.8 emitted exactly what `citation_author_institution` says; the auditor recorded a longer string. None are publisher-vs-auditor disagreements on identity — every case is a *shortening*.

### Oxford University Press (5 authors, 2 DOIs)

#### `10.1093/jaoac/32.2.156` — D E Bullis
- **Current gold** (85 chars): `Associate Referee, Chemist, Oregon Agricultural Experiment Station, Corvallis, Oregon`
- **Proposed gold = meta tag** (66 chars): `Chemist, Oregon Agricultural Experiment Station, Corvallis, Oregon`
- **Dropped**: `Associate Referee,` (auditor included the role prefix; meta tag does not)

#### `10.1093/jee/97.2.646` — 4 authors, all same shortening
For each: gold has `…Bozeman, MT 59717; <secondary affiliation>`. Meta tag has only the Montana State primary. AI v1.8 = meta tag exactly.

| Author | Gold drops |
|---|---|
| Tao Wang | `; Department of Plant Science, North Dakota State University, Fargo, ND 58102` |
| Sharron S. Quisenberry | `; College of Agriculture and Life Sciences, 104 Hutcheson Hall (0402), Virginia Tech, Blacksburg, VA 24061` |
| Xinzhi Ni | `; Biological Control of Pests Research Unit, USDA-ARS, Stoneville, MS 38776` |
| Vicki Tolmay | `; Small Grain Institute, Private Bag X29, Bethlehem 9700, South Africa` |

For all 4: **Proposed gold** = `Department of Entomology, Montana State University, Bozeman, MT 59717` (the meta tag).

### Springer Nature (4 authors, 2 DOIs)

#### `10.1007/s10577-005-1005-6` — Alla Krasikova, Elena Gaginskaya (same affiliation)
- **Current gold** (139): `Biological Research Institute, Saint-Petersburg State University, Oranienbaumskoie sch. 2, Stary Peterhof, Saint-Petersburg, 198504, Russia`
- **Proposed gold = meta tag** (90): `Biological Research Institute, Saint-Petersburg State University, Saint-Petersburg, Russia`
- **Dropped**: `Oranienbaumskoie sch. 2, Stary Peterhof, … 198504` (street address + postal code)

#### `10.1007/s10577-005-1005-6` — Jose Luis Barbero
- **Current gold** (122): `Department of Immunology and Oncology, Centro Nacional de Biotecnologia, UAM Campus de Cantoblanco, Madrid, E-28049, Spain`
- **Proposed gold = meta tag** (113): `Department of Immunology and Oncology, Centro Nacional de Biotecnologia, UAM Campus de Cantoblanco, Madrid, Spain`
- **Dropped**: `E-28049,` (postal code)

#### `10.1007/s10967-018-6384-1` — B. K. Sapra
- **Current gold** (71): `Radiological Physics and Advisory Division, BARC, Mumbai, 400085, India`
- **Proposed gold = meta tag** (63): `Radiological Physics and Advisory Division, BARC, Mumbai, India`
- **Dropped**: `400085,` (postal code)

### Other / Unknown — `10.31857` Russian Geographic Society (4 authors, 1 DOI)

#### `10.31857/s2587556623070105` — R. S. Nikolaev, A. A. Lyadova, A. S. Luchnikov, S. A. Merkushev
For all 4 authors:
- **Current gold** (35): `Perm State University, Russia, Perm`
- **Proposed gold = meta tag** (21): `Perm State University`
- **Dropped**: `, Russia, Perm` (city + country)

---

## Per-publisher reliability verdict

| Publisher | Cases | Verdict | Rationale |
|---|---|---|---|
| **OUP** | 5 | 🟢 **Safe to accept meta tag** | All 5 cases are clean shortenings: institution + city + region. Meta tag is a defensible primary affiliation per Casey's "easiest reliable source wins" rule. The Bullis case (drops "Associate Referee" role prefix) is more arguable — that's a role label, not part of the institutional affiliation. |
| **Springer Nature** | 4 | 🟢 **Safe to accept meta tag** | Drops are postal codes and street addresses. Institution + country preserved on all 4. Defensible. |
| **Other / Unknown (10.31857)** | 4 | 🟡 **Acceptable but minimal** | Meta tag drops to *just* the institution, no country. "Perm State University" is recognizable but the country/city are useful disambiguators. Closer call. |

**Beyond holdout — open question**: I have not yet spot-checked OUP and Springer articles outside the holdout-50 to confirm publisher-wide consistency. Casey's "verify across publishers" rule says we should do that before generalizing. **Estimated 15-min browser-only investigation; will do as next step.**

---

## The decision Casey + Jason need to make

This proposal **narrows the gold standard**. Every accepted edit drops information the human auditor recorded:

- **Postal codes** (5 cases)
- **Secondary affiliations** (4 OUP cases, multi-aff entries)
- **Role prefixes** (1 OUP case: "Associate Referee")
- **City + country** (4 Russian cases)

**Question for Casey/Jason**: is the goal to record what the publisher has structured metadata for (= meta tag, the "easiest reliable source"), or to record everything visible on the page (= what the auditor did)?

- If **structured-metadata-as-truth**: approve all 13 edits, rases per-author lifts to 46%, then continue with comparator and Elsevier-cache work to push higher.
- If **page-visible-as-truth**: do not approve; document these 13 as known publisher-meta-vs-auditor-difference cases, and accept that rases will not exceed ~70% on holdout-50 even with comparator improvements.

---

## What this does NOT solve

- The 6 Elsevier DOIs in holdout where Taxicab cached the paywalled abstract page. Those have ZERO `citation_author_institution`. Different problem; needs a Taxicab refetch strategy. Tracked separately.
- The 17 Bucket 1 (empty rases) cases where Claude returned empty AND meta tag has nothing. Structurally uncrawlable from current cache.
- Comparator failures (unicode, name format, etc.). Tracked under separate workstreams.

## Reproducibility

Numbers in this doc come from `eval/scripts/meta_tag_audit.py`, run against `runs/holdout-v1.8/ai-goldie-1.csv` and `eval/goldie/holdout-50.csv`. No LLM calls. Re-runnable in <2 min:

```
eval/.venv/bin/python eval/scripts/meta_tag_audit.py
```

Underlying case data: `/tmp/gold-update-cases.json` (13 entries, one per author-row).
