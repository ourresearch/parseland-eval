"""Split eval/data/ai-goldie-source-10k.csv into 100 batches of 100 DOIs each.

Output:
    eval/data/ai-goldie-1.csv   rows 1-100
    eval/data/ai-goldie-2.csv   rows 101-200
    ...
    eval/data/ai-goldie-100.csv rows 9901-10000

Each batch CSV preserves the source schema (12 columns: No, DOI, Link, ...).
The `No` column is renumbered 1-100 within each batch so downstream scripts
that key on No don't collide across batches.

Idempotent — overwrites outputs. Atomic per-batch via .tmp + rename.

Run:
    eval/.venv/bin/python eval/scripts/split_10k_batches.py
    eval/.venv/bin/python eval/scripts/split_10k_batches.py \
        --input eval/data/ai-goldie-source-10k.csv \
        --out-dir eval/data \
        --batch-size 100

Surfaces failures: refuses to run if source row count isn't a multiple of
batch-size (no silent truncation).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

DEFAULT_INPUT = Path("eval/data/ai-goldie-source-10k.csv")
DEFAULT_OUT_DIR = Path("eval/data")
DEFAULT_BATCH_SIZE = 100


def split(input_path: Path, out_dir: Path, batch_size: int) -> list[Path]:
    if not input_path.exists():
        raise SystemExit(f"input CSV not found: {input_path}")

    with input_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        if not fieldnames:
            raise SystemExit(f"empty or headerless CSV: {input_path}")
        rows = list(reader)

    total = len(rows)
    if total == 0:
        raise SystemExit(f"no data rows in {input_path}")
    if total % batch_size != 0:
        raise SystemExit(
            f"row count {total} not a clean multiple of batch-size {batch_size}; "
            f"refuse to split (would silently drop or pad)"
        )

    n_batches = total // batch_size
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for batch_idx in range(n_batches):
        batch_rows = rows[batch_idx * batch_size : (batch_idx + 1) * batch_size]
        out_path = out_dir / f"ai-goldie-{batch_idx + 1}.csv"
        tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")

        with tmp_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for within_batch_idx, row in enumerate(batch_rows, start=1):
                row = dict(row)
                row["No"] = str(within_batch_idx)
                writer.writerow(row)

        os.replace(tmp_path, out_path)
        written.append(out_path)

    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = ap.parse_args()

    written = split(args.input, args.out_dir, args.batch_size)
    print(f"wrote {len(written)} batch files to {args.out_dir}")
    print(f"  first: {written[0].name}")
    print(f"  last:  {written[-1].name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
