# Goldie Cleanup Inventory

Date: 2026-06-05

This cleanup keeps GitHub professional without deleting useful evidence. It separates
commit-worthy artifacts from generated local output.

## Protected

- `eval/data/merged-FINAL.csv`
- Verified hash before cleanup:
  `b33dfd256fddf44b32c5543e11d6997256efcb24deaf9dc9323188bd22adcc43`

## Commit-Worthy

- CLI source and tests.
- `README.md`, `eval/README.md`, and `eval/docs/goldie/`.
- Compact run evidence: `manifest.json`, `report.json`, `OPERATOR_REPORT.md`,
  checksums, launch-readiness notes, and oxjob evidence.

## Ignored Generated Output

- Goldie sampled source CSVs and `.partial.jsonl` sample state.
- Goldie run internals: `batches/`, `checkpoints/`, `logs/`, `live.html`, and `merged.csv`.
- Old local 10K run scratch under `runs/10k/`.
- Generated `eval/data/ai-goldie-*` shards, zips, backups, and local grouping CSVs.
- Generated `eval/uv.lock` until the repo intentionally adopts a lockfile.

## Left Visible On Purpose

The cleanup does not hide or delete tracked WIP or untracked code/test files such as
`eval/goldie_cli/events.py`, `eval/goldie_cli/taxicab_reharvest.py`, and related tests.
Those need separate review before any commit or removal.

## Archive Plan

Bulky local generated artifacts were copied outside the repo before deletion from the
worktree. Archive root:

```text
/Users/shubh-trips/Documents/OpenAlex/artifact-archive/goldie-cli-20260605T171943Z/
```

Archive details:

| Item | Value |
|---|---|
| Archive | `generated-artifacts.tar.gz` |
| SHA-256 | `265208af88c2a54c264f9a3fdca79e6d3f3ab50d89365569620916f10c83cc2f` |
| Paths recorded | 136 |
| Archive size | 53M |

Archived groups included:

- `runs/10k/`
- `eval/eval_local_taxicab_zyte/runs/`
- `eval/data/ai-goldie-1-judged-10k*/`
- `eval/data/ai-goldie-10k*/`
- `eval/data/merged-FINAL.csv.bak-*`
- generated `eval/data/ai-goldie-*.csv` shards and old untracked Goldie run dirs

Do not archive or remove protected `eval/data/merged-FINAL.csv`.

Note: `eval/eval_local_taxicab_zyte/runs/` contained tracked prod artifacts mixed with
untracked scratch. The tracked files were restored from the archive immediately after
cleanup; the ignore rule now prevents future untracked scratch from cluttering status.
