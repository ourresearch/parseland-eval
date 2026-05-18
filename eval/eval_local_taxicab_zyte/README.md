# eval_local_taxicab_zyte

**Isolated parallel workspace** for the 10K-goldies recovery cascade. Built 2026-05-17 in response to Phase 2.7 cascade gate showing `browser_use_cloud` Tier 2 hit 6.7% success (11/15 timeouts, 0 Cloudflare detections — the failure is Cloud Agent latency, not bot walls).

This workspace does NOT touch `eval/browser-use/` or `eval/scripts/rerun_targeted.py` — those keep running in flight. Net-new code lives only here; existing modules are imported **read-only**.

## What it does

Two independent jobs, one per missing-field bucket:

| Script | Input | Tier ladder |
|---|---|---|
| `scripts/rerun_authors_local.py` | `data/rerun-no-authors.csv` (1,619 rows) | A) Taxicab POST re-harvest → re-extract; B) local visible Chrome over CDP → re-extract; C) terminal |
| `scripts/rerun_rases_zyte.py` | `data/rerun-no-rases.csv` (3,826 rows) | A) Taxicab POST re-harvest → re-extract; B) Zyte residential proxy → re-extract; C) local visible Chrome fallback on `zyte_blocked`; D) terminal |
| `scripts/merge_results.py` | both run outputs | merged-FINAL.csv + CASEY-SUMMARY.md with per-publisher before/after fill rates |

Both jobs reuse the existing extractor (`extract_via_claude`) and judge chain (`run_doi_with_judge_on_html`) by importing — no duplication of judge logic.

## How to run

```bash
# Prereqs
cp .env.example .env  # then fill in keys
# For local Chrome tier: launch visible Chrome with CDP
google-chrome --remote-debugging-port=9222 &  # or Chrome.app on macOS

# Smoke (10 rows each)
python eval/eval_local_taxicab_zyte/scripts/rerun_authors_local.py --limit 10 --label smoke
python eval/eval_local_taxicab_zyte/scripts/rerun_rases_zyte.py --limit 10 --label smoke

# Full
python eval/eval_local_taxicab_zyte/scripts/rerun_authors_local.py --label prod
python eval/eval_local_taxicab_zyte/scripts/rerun_rases_zyte.py --label prod

# Merge
python eval/eval_local_taxicab_zyte/scripts/merge_results.py --label prod
```

## Outputs

```
runs/
├── authors-local-<label>/
│   ├── results.csv               # gold-shape schema
│   ├── results.tier-log.jsonl    # one line per row, which tier closed it
│   ├── failures.jsonl            # terminal-tier rows with reason
│   └── cost-ledger.json          # extractor + judge spend
└── rases-zyte-<label>/
    └── (same shape)
```

## Boundary — what this workspace promises NOT to touch

- `eval/browser-use/**` — read-only
- `eval/scripts/rerun_targeted.py`, `extract_via_taxicab.py`, `extract_with_judge.py` — read-only imports allowed, no edits
- `eval/goldie/human-goldie.csv`, `eval/gold-standard.json` — gold is read-only forever
- `eval/prompts/ai-goldie-v1.9.*.md` — prompt locked; import-and-reuse only

`git status eval/browser-use/ eval/scripts/` after every run should show **no changes**.
