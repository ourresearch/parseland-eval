# Empirical pdf_url failure analysis — what each URL actually returns

Run 2026-05-04. HEAD-checks via `curl -skIL` from local machine. "Real PDF" = HTTP 200 + `Content-Type: application/pdf`.

## Category A — gold=N/A, AI extracted from `citation_pdf_url` (11 cases)

| DOI | AI URL | Live verdict |
|---|---|---|
| 10.1007/s10577-005-1005-6 (Springer) | `link.springer.com/content/pdf/...` | 200 → HTML landing (8 redirects, no PDF) |
| 10.1007/s10967-018-6384-1 (Springer) | `link.springer.com/content/pdf/...` | 200 → HTML landing (8 redirects, no PDF) |
| 10.1055/s-0033-1340558 (Thieme) | `thieme-connect.de/products/ejournals/pdf/...` | **403 paywall** |
| 10.1093/jaoac/32.2.156 (OUP) | `academic.oup.com/jaoac/article-pdf/...` | **403 Cloudflare HTML** (5.7KB) |
| 10.1093/jaoac/60.2.289 (OUP) | (same shape) | **403 Cloudflare** |
| 10.1093/jee/97.2.646 (OUP) | (same shape) | **403 Cloudflare** |
| 10.1103/physrevb.44.3757 (APS) | `link.aps.org/pdf/...` | **403 paywall** |
| 10.1111/j.1365-2222.2005.02173.x (Wiley) | `onlinelibrary.wiley.com/doi/epdf/...` | **403 paywall** |
| **10.1371/journal.pone.0192138 (PLOS)** | `journals.plos.org/.../article/file?...&type=printable` | **200 + application/pdf, 15.3 MB ✅** |
| 10.18041/0124-0021/dialogos.52.2020.8807 | `revistas.infotegra.com/.../article/download/...` | 522 (Cloudflare error) |
| 10.3791/30429 (JoVE) | `jove.com/pdf/30429/...` | 202 bot-check |

**Reading:** gold's N/A on 10 of 11 is empirically correct — those URLs don't return PDFs without authentication. AI is over-extracting `citation_pdf_url` from publishers that don't actually serve the file via that URL. **Only PLOS returns a real PDF.** This is a specific gold-update candidate, not a convention change.

## Category B — AI empty, gold has URL (4 cases)

| DOI | Gold URL | Live verdict |
|---|---|---|
| 10.1016/0016-5085(95)22767-9 (Gastrojournal '95) | `gastrojournal.org/article/.../pdf` | **403 paywall** (gold's URL is also paywalled) |
| 10.1163/9789004273610_010 (Brill) | `brill.com/downloadpdf/display/book/...` | 202 bot-check (so does AI's would-be backfill from `citation_pdf_url`) |
| **10.36838/v4i6.14 (Terra-docs IJHSR)** | `terra-docs.s3.../IJHSR/.../Nguyen.pdf` | **200 + application/pdf, 485 KB ✅** (Taxicab no-harvest, AI couldn't see it) |
| **10.7256/2454-0730.2019.1.20595 (Cyberleninka)** | `cyberleninka.ru/.../servisologiya-kak-nauchnaya-osnova-razvitiya-sfery-servisa.pdf` | **200 + application/pdf ✅** (cached HTML has no PDF link, real miss) |

## Category C — same article, different URL form (2 cases)

| DOI | AI URL | Gold URL | Verdicts |
|---|---|---|---|
| 10.1121/1.413202 (ASA) | `asa.scitation.org/doi/pdf/...` → 200 text/html (1.4 KB Cloudflare interstitial) | `watermark02.silverchair.com/...?token=...` (token expired) | Both broken in different ways |
| **10.25259/nmji_377_2024 (NMJI)** | `nmji.in/content/.../NMJI-377-2024.pdf` → **200 + application/pdf, 407 KB ✅** | `nmji.in/view-pdf/?article=<token>` → 200 text/html | **AI more correct than gold** — AI returns the actual PDF, gold returns the wrapper page |

## What this means for moving pdf_url

The 66% number is being held back by *empirically correct* gold N/As (10 of 17). The deterministic ceiling without per-DOI gold updates is therefore close to current.

Clean gold-update candidates with concrete evidence (would lift pdf_url 66 → 70):

1. **PLOS `10.1371/journal.pone.0192138`**: gold should change N/A → AI's URL. The URL returns a real 15.3 MB PDF. Open-access journal.
2. **NMJI `10.25259/nmji_377_2024`**: gold should change to AI's URL (`nmji.in/content/.../NMJI-377-2024.pdf`). Gold's current URL is the HTML wrapper page; AI's URL is the actual PDF.

Live-fetch candidates (would lift +4pp if recovered):

3. **Terra-docs `10.36838/v4i6.14`**: real PDF exists at S3 URL. Taxicab hasn't harvested this DOI (oxjob #133 already tracks). Live-fetch could find it via DOI resolver.
4. **Cyberleninka `10.7256/2454-0730.2019.1.20595`**: real PDF exists. Cached HTML doesn't link to it; live-fetch needed.

Hard residuals (no path on current convention):
- Brill 10.1163/9789004273610_010 — gold's URL is bot-checked; can't verify content.
- Gastrojournal 1995 — gold's URL paywalled; live-fetch likely 403s.
- ASA Scitation 10.1121/1.413202 — silverchair token expired in cache; AI's URL is Cloudflare HTML.
