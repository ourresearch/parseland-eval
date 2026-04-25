# Goldie — frozen + audit working set

Weekend Goldie Sprint artifacts. The Human Goldie is our hand-audited 100-row gold standard; the AI Goldie Machine is the LLM prompt we're tuning to ≥95% agreement with it.

## Files

| File | Role | Editable? |
|---|---|---|
| `human-goldie-v1-pre-audit.csv` | Frozen snapshot of the canonical gold standard taken on **2026-04-25**, copied byte-for-byte from `parseland-lib/eval/gold-standard.csv`. The audit trail. | **NO. Never edit.** |
| `human-goldie-v2-audited.csv` | Working copy. This is where row-level fixes from the manual audit land. After the audit completes this becomes the "Human Goldie v2". | YES — edit during the audit. |
| `audit-checklist.csv` | One row per DOI with `landing_page_url` (auto-filled by `eval/scripts/audit_helper.py`) and `*_ok` columns (`authors_ok`, `rases_ok`, `corresponding_ok`, `abstract_ok`, `pdf_url_ok`, `notes`) for you to fill in while comparing v1 against the live landing page. | YES — fill `_ok` columns + notes during audit. |
| `train-50.csv` | First 50 rows by `No` — used for AI Goldie prompt iteration. | NO — regenerate via `split_train_holdout.py`. |
| `holdout-50.csv` | Last 50 rows by `No` — **sacred. Never run AI Goldie on this during prompt iteration.** | NO — regenerate via `split_train_holdout.py`. |

## Source of truth

Canonical pre-audit source (read-only):
`/Users/shubh-trips/Documents/OpenAlex/parseland-enhancer/parseland-lib/eval/gold-standard.csv`

Both `parseland-eval/eval/gold-standard.csv` and the parseland-lib copy were byte-identical on 2026-04-25; we copied from the parseland-lib path per the sprint instructions (it is the documented source).

## Schema (raw CSV columns)

`No, DOI, Link, Authors, Abstract, PDF URL, Status, Notes, Has Bot Check, Resolves To PDF, broken_doi, no english`

`Authors` is a JSON-encoded array of objects with keys `name`, `rasses` (intentional misspelling of "affiliations"), `corresponding_author`. The `eval/parseland_eval/gold.py` adapter normalizes `rasses → affiliations` and `corresponding_author → is_corresponding` for downstream code, but the raw CSV keeps the source spelling.

## Workflow

1. v1 is frozen. Don't touch.
2. Audit each row in `audit-checklist.csv`. For each DOI, open `landing_page_url` and verify v1's authors / rases / corresponding flags / abstract / pdf URL against the live page. Fill the `_ok` columns (`Y`/`N` or `✓`/`✗` — your call) and any `notes`.
3. For each `N` finding, edit the corresponding row in `human-goldie-v2-audited.csv`.
4. After the audit lands, re-run `python eval/scripts/split_train_holdout.py` to regenerate `train-50.csv` and `holdout-50.csv` from the audited v2.
5. Iterate the AI Goldie prompt against `train-50.csv` only. Never touch `holdout-50.csv` until you're ready for the final lock.
