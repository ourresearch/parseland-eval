# Goldie 10K Launch Readiness

Prepared: 2026-06-05
Corpus: `goldie-10k-20260605T160114Z`
Source directory: `runs/goldie-10k-20260605T160114Z`
Source CSV: `runs/goldie-10k-20260605T160114Z/source.csv`

## Source Preparation

```bash
uv run --project eval goldie sample \
  --target 10000 \
  --out runs/goldie-10k-20260605T160114Z/source.csv \
  --gold eval/human-goldie.csv
```

Result:

- `source.csv`: 10,001 lines including header
- `source.csv.partial.jsonl`: 10,000 accepted DOI records
- Crossref was used for DOI sampling only. Metadata extraction must still come from DOI.org-resolved publisher pages, Taxicab/cache HTML, or rendered-browser evidence.

Checksums:

```text
0db4d0abf290e226b1e4266d8d3a25357e408599f321ac28759027afc981b8f4  runs/goldie-10k-20260605T160114Z/source.csv
e50949afa370bff3a28e827fc0d59a455358fc060e356b8375a5fcf3afffbe8f  runs/goldie-10k-20260605T160114Z/source.csv.partial.jsonl
```

## Extraction Command

The documented extraction command is:

```bash
uv run --project eval goldie run \
  --source runs/goldie-10k-20260605T160114Z/source.csv \
  --corpus goldie-10k-20260605T160114Z \
  --tier cached \
  --fallback-tier cloud
```

Monitor command:

```bash
uv run --project eval goldie monitor --run runs/goldie-10k-20260605T160114Z-<run-stamp>
```

Report command:

```bash
uv run --project eval goldie report --run runs/goldie-10k-20260605T160114Z-<run-stamp>
```

## Launch Gate

The 10K extraction was not launched from this session.

Exact blocker: the random-100 quality run completed successfully but attempted cloud fallback on 82/100 rows, used fallback on 32/100 rows, and cost $38.27 total. At the same observed rate, 10K projects to roughly $3,827 and a long cloud retry tail. This is not a correctness failure, but it is a scale/runtime/cost gate that should be accepted explicitly before starting a long unattended extraction.

The 10K source is ready for a controlled launch window after the operator accepts the fallback volume and confirms the run environment should spend that amount for quality.
