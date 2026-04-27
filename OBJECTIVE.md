# OBJECTIVE.md

## Objective

Build a repeatable AI-assisted gold-standard generation pipeline for scholarly DOI metadata.

The pipeline should:
- Use rows 1-50 of the human gold standard to craft and tune a prompt.
- Extract rows 51-100 with the tuned prompt only for final validation.
- Compare AI output against human annotations field by field and word by word where applicable.
- Proceed to new 100-DOI batches only after the prompt and extraction method reach greater than 95% agreement.
- Emit each 100-DOI candidate batch as `ai-goldie-<n>.csv`.
- Keep AI output as candidate gold only until human review has accepted or corrected it.

Target fields:
- DOI
- Link
- Authors
- Author affiliations, stored in raw CSV as `rasses`
- `corresponding_author`
- Abstract
- PDF URL
- Status and extraction flags: `Has Bot Check`, `Resolves To PDF`, `broken_doi`, `no english`
- Notes

Success criteria:
- Greater than 95% agreement on the held-out 50-row validation set before scale-up.
- No silent failures: bot checks, broken DOI pages, missing abstracts, missing PDFs, and non-English pages must be visible in output flags or notes.
- Batch outputs are deterministic enough to audit, reproduce, and convert back into the raw gold CSV schema.

## Downstream value (why this is the foundation)

- **CDL — corresponding-author coverage** ($50k already paid). Demands the human goldie be PERFECT on CA. Dedicated CA second sweep is a hard prerequisite.
- **Abstract coverage grant** (~$12M opportunity). Demands the AI goldie scale cleanly to 10K DOIs.
- The goldie pair (human + AI) is reused indefinitely as the regression-test substrate for every future Parseland data-quality improvement.

## Hard prerequisite (gate before any AI run)

The human goldie at `eval/goldie/human-goldie-v2-audited.csv` must be reviewed PERFECT — including the dedicated CA second sweep — before AI numbers are reported. Pre-audit measurements are throwaway. The single audit-checklist row's `_ok` columns must all be filled (Y/N + notes); any N gets fixed in the v2 CSV before the holdout AI run.

## Locked stack (Session 2, 2026-04-27)

| Stage | Tooling | Rationale |
|---|---|---|
| Holdout-50 validation | browser-use library + 4 parallel headed Chromes via CDP, BYOK Anthropic | Existing local pipeline, proven; full operator control while iterating prompt versions. |
| 10K production extraction | **browser-use Cloud Tasks API, Business tier ($299/mo, 200 concurrent)** | ~38 min wall, ~$1,310 all-in. Accuracy + time prioritized over cost. Independent judge component verifies task success. |
| Default model | `claude-sonnet-4-5` (browser-use's name for sonnet-4.6) | browser-use's optimization target; benchmark gpt-5.4-mini only if Sonnet underperforms. |
| Rejected | Anthropic `/chrome` (interactive UI only); Anthropic Computer Use (vision-based, malformed JSON) | DOM accessibility tree wins over screenshot loops for structured extraction at scale. |

## Bullet-proof guarantees at 10K scale

`eval/scripts/extract_batch_cloud.py` will satisfy:
1. **Resumable** — SHA-256-keyed checkpoints; re-runs skip landed DOIs.
2. **Atomic** — per-batch CSV is `.tmp` + rename, no partial files visible to the user reviewer.
3. **Idempotent** — DOI-keyed; re-runs produce identical output.
4. **Transparent failures** — `ai-goldie-N.failures.jsonl` is the source of truth for blank rows.
5. **Bot-check resilient** — Cloud's hosted real Chrome plus residential-proxy ($5/GB) fallback only on `has_bot_check=true`.
6. **Schema-enforced** — Pydantic via Cloud `structuredOutput`; malformed responses fail fast and route to retry queue.
7. **Cost-capped** — `max_agent_steps=18` per task, retry cap N=3, optional `--max-cost-usd`.

## Cadence (hybrid)

1. Phase E.1: extract batch 1 only → user reviews → gate.
2. Phase E.2: extract batches 2–100 in parallel only after batch-1 review is clean.

## Companion files

- [CLAUDE.md](./CLAUDE.md) — repo conventions and locked decisions
- [PLAN.md](./PLAN.md) — phase-by-phase execution checklist with commands
- [MEMORY.md](./MEMORY.md) — persistent working memory
- `eval/goldie/README.md` — frozen vs working sets and audit workflow
