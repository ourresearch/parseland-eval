# eval

`eval/` contains two related but separate workflows.

1. `parseland_eval`: the live Parseland evaluation harness that scores the deployed
   Parseland service against the hand-annotated gold standard and feeds the dashboard.
2. `goldie_cli`: the random DOI extraction harness used to build and inspect larger
   Goldie corpora.

Do not conflate them. The dashboard eval calls Taxicab and Parseland live services.
Goldie CLI samples DOI corpora and runs a separate extraction/report workflow.

## Live Parseland Eval

Install from the repo root:

```bash
uv run --project eval python -m parseland_eval --help
```

Run the full live eval:

```bash
uv run --project eval python -m parseland_eval run --label <name>
```

Run a small dashboard smoke check:

```bash
uv run --project eval python -m parseland_eval run --label smoke --limit 5
```

Run artifacts land under `eval/runs/` and are consumed by the dashboard. The runner
calls the live service through Taxicab and Parseland; it should fail loudly if those
services are unavailable.

## Goldie CLI

Goldie CLI lives in `eval/goldie_cli/` and writes extraction artifacts under repo-root
`runs/`. It is quality-first: Crossref is used only to sample random DOI strings, while
field values must come from DOI.org-resolved pages, Taxicab/cache HTML, or rendered-browser
evidence.

Quick commands:

```bash
uv run --project eval goldie --help
uv run --project eval goldie random --count 100 --name goldie-random-100
uv run --project eval goldie prepare --count 10000 --name goldie-10k
uv run --project eval goldie resume --run runs/<run-dir>
uv run --project eval goldie report --run runs/<run-dir> --operator
```

Read the operator docs before launching a large run:

- [`docs/goldie/README.md`](docs/goldie/README.md)
- [`docs/goldie/HARNESS.md`](docs/goldie/HARNESS.md)
- [`docs/goldie/LEARNINGS.md`](docs/goldie/LEARNINGS.md)

## Tests

```bash
uv run --project eval pytest
uv run --project eval pytest eval/goldie_cli/tests -q
```

For Goldie cleanup and extraction work, also verify:

```bash
git diff --check
shasum -a 256 eval/data/merged-FINAL.csv
```

The protected `eval/data/merged-FINAL.csv` hash should remain
`b33dfd256fddf44b32c5543e11d6997256efcb24deaf9dc9323188bd22adcc43` unless an explicit
gold-data update is approved.
