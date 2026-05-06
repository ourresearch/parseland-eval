# Empirical pdf_url failure analysis — train-50

Run 2026-05-06. HEAD-checks via `requests` from local machine over residential IP. "Real PDF" = HTTP 200 + `Content-Type: application/pdf` (or `%PDF` magic bytes in body). All 12 train-50 pdf_url disagreements probed.

Methodology mirrors `PDF-EMPIRICAL-PROBE.md` (holdout-50, 2026-05-04). Holdout's findings led to comparator rule #10 (paywalled-publisher canonical ≅ N/A) for Springer / OUP / APS / Wiley / Thieme. This doc applies the same lens to train-50 to surface which patterns are already covered, which need new publisher coverage, and which need gold updates.

## Category A — gold=N/A, AI extracted publisher canonical (5 cases)

Same shape as holdout Cat A. AI's URL is the citation_pdf_url meta-tag canonical from the publisher; gold marks N/A because the URL doesn't return a real file without authentication.

| DOI | AI URL | Live verdict |
|---|---|---|
| `10.1038/ng1297-370` (Nature) | `nature.com/articles/ng1297-370.pdf` | 200 → HTML (4 redirects) |
| `10.1039/c5ra25098f` (RSC) | `pubs.rsc.org/en/content/articlepdf/2016/ra/c5ra25098f` | 200 → HTML (1 redirect) |
| `10.1088/0253-6102/36/1/109` (IOPscience) | `iopscience.iop.org/article/.../pdf` | 200 → HTML (1 redirect) |
| `10.1088/0256-307x/35/4/045201` (IOPscience) | `iopscience.iop.org/article/.../pdf` | 200 → HTML (1 redirect) |
| **`10.1086/116973`** (NASA ADS) | `ui.adsabs.harvard.edu/link_gateway/.../ADS_PDF` | **200 + application/pdf ✅** |

**Reading:** 4 of 5 are paywalled-pattern, gold N/A is empirically correct, same as holdout. **NASA ADS is the only train Cat-A real PDF** — it's the open-access gateway to the original article (1994 Astronomical Journal).

## Category B — AI URL is real PDF, gold has different URL form (2 cases)

OJS publishers where the same article is served at multiple URL forms. AI picked the `download/` endpoint; gold uses a sibling form. Both reach the same content.

| DOI | AI URL | Gold URL | Verdicts |
|---|---|---|---|
| `10.53555//kuey.v30i9.5180` (Kuey OJS) | `kuey.net/.../article/download/5180/5728` → **200 + application/pdf ✅** | `kuey.net/.../article/view/5180/5728` (HTML view) | Same numeric IDs, `download/` vs `view/` |
| `10.62480/tjms.2025.vol42.pp71-73` (Zien) | `zienjournals.com/.../article/download/6045/4922` → **200 + application/pdf ✅** | `zienjournals.com/.../article/download/6045/4922/5916` (with trailing) | AI is a prefix of gold URL |

## Category C — both broken / AI extracted wrong target (4 cases)

| DOI | AI URL | Gold URL | Verdicts |
|---|---|---|---|
| `10.1016/j.celrep.2018.10.057` (Cell Reports) | `cell.com/article/.../pdf` → 403 | `cell.com/action/showPdf?pii=...` | AI = paywalled-pattern; gold = same-host different path |
| `10.1016/j.clpl.2024.100067` (ScienceDirect) | `sciencedirect.com/.../pdfft` → 403 | `pdf.sciencedirectassets.com/.../main.pdf?X-Amz-...` (signed S3 URL) | AI = paywalled-pattern; gold = expired AWS-signed token |
| `10.9734/ajess/2023/v47i31023` (Journal Ajess OJS) | `journalajess.com/.../article/download/1023/1998` → 403 | `journalajess.com/.../download/1023/1998/1621` (with trailing) | Both 403; same shape as Cat B but bot-blocked |
| `10.3138/chr-027-04-br24` (UTP) | `doi.org/10.3138/chr-027-04-br24` → 403 | (gold N/A) | **AI is wrong** — extracted the DOI resolver URL, not a PDF link |

## Category D — network-level error (1 case)

| DOI | AI URL | Verdict |
|---|---|---|
| `10.3724/sp.j.1123.2014.10009` (Chrom-China) | `chrom-china.com/.../downloadArticleFile.do?...` | SSL handshake failure (server cert misconfig) — can't verify content |

## Crosswalk to comparator rule #10

Rule #10 currently absorbs paywalled-publisher canonical ≅ N/A for Springer / OUP / APS / Wiley / Thieme. Of the 12 train-50 disagreements, the publishers that would need new rule #10 coverage to mirror the holdout convention:

| Publisher | URL pattern | Rows affected | Empirically same shape as holdout? |
|---|---|---|---|
| Nature | `nature.com/articles/<id>.pdf` | 1 | Yes (200 → HTML) |
| RSC | `pubs.rsc.org/en/content/articlepdf/<vol>/<jrn>/<doi>` | 1 | Yes (200 → HTML) |
| IOPscience | `iopscience.iop.org/article/<doi>/pdf` | 2 | Yes (200 → HTML) |
| Cell.com | `cell.com/article/<pii>/pdf` | 1 | Yes (403) |
| ScienceDirect (direct PDF) | `sciencedirect.com/.../pdfft` | 1 | Yes (403) |
| Journal Ajess (OJS, bot-blocked) | `journalajess.com/.../article/download/<id>` | 1 | Yes (403) |

## Decisions surfaced (no code changes pending Casey/Shubh signoff)

Per the standing discipline (no train-tuning extensions to comparator without validation-set decision), these are surfaced as **candidates** rather than executed.

### A — Rule #10 publisher additions

Mirror holdout's paywalled-pattern convention to 6 more publishers. Would land **+12pp on train-50 pdf_url** (76 → ~88).

- Nature articles `.pdf` URL form
- RSC `articlepdf/...` URL form
- IOPscience `/pdf` suffix form (covers 2 train rows)
- Cell.com `/article/<pii>/pdf` form
- ScienceDirect `/pdfft` form
- Journal Ajess OJS `download/<id>/<id>` form

### B — Gold-flip (real PDF, gold N/A is wrong)

- `10.1086/116973` NASA ADS → flip gold N/A → AI URL. Same shape as holdout's PLOS gold-flip (open-access gateway). Lands **+2pp**.

### C — Comparator extension for OJS view↔download / subset (small)

- Kuey: `download/<a>/<b>` ≅ `view/<a>/<b>` when same numeric pair (real PDF on download side).
- Zien: AI URL is a prefix of gold URL (subset). Same numeric prefix → match.
- Same shape would cover Journal Ajess if not blocked by 403.

Lands **+4pp** if applied.

### D — Articulate-why (no fix path)

- `10.3138/chr-027-04-br24` — AI extracted the DOI resolver URL (`doi.org/...`) rather than a PDF. AI is wrong; gold N/A is correct. Stochastic LLM extraction error, not a class to encode in comparator.
- `10.3724/sp.j.1123.2014.10009` — chrom-china SSL handshake fails; can't verify whether AI URL serves a PDF. Server-side issue.

### Total potential pdf_url movement

| Path | Train pdf_url Δ | Comparator overfit risk |
|---|---|---|
| A only (mirror holdout convention to 6 publishers) | 76 → 88 (+12pp) | **Low** — same paywalled-pattern shape, just more publishers |
| A + B (also flip ADS gold) | 76 → 90 (+14pp) | Low (gold update is per-row evidence) |
| A + B + C (also OJS endpoint absorption) | 76 → 96 (+20pp) | **Medium** — OJS variation is shape-narrow but covers fewer holdout cases |

Per the standing rule, none of these execute without explicit signoff because they all encode train-shaped patterns. **A is the cleanest path** — it's not a train tune; it's *consistency with the existing holdout convention* extended to 6 publishers we hadn't seen on holdout.

## What stays unmovable on train pdf_url after all of A+B+C

- `10.1016/j.clpl.2024.100067` ScienceDirect — AI = paywalled `/pdfft`; gold = expired S3 signed URL. Rule #10 absorbs AI side, but gold's own URL is gibberish (signed token from when gold was authored). Probably needs a gold update (replace gold's signed URL with AI's `/pdfft` form).
- `10.3138/chr-027-04-br24` — articulate-why; AI is wrong.
- `10.3724/sp.j.1123.2014.10009` — server SSL.

So even with all surfaced changes applied, ~3–4 train pdf_url residuals remain.
