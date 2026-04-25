"""Deterministically split human-goldie-v2-audited.csv into train-50 / holdout-50.

Sort key is the integer `No` field ascending. Rows 1-50 → train, 51-100 → holdout.
This intentionally mirrors the existing `gold-standard.seed.json` / `.holdout.json`
partition produced by `eval/parseland_eval/split.py` so splits stay consistent
across the codebase.

The holdout is sacred — never run AI Goldie on it during prompt iteration.

Run:
    python eval/scripts/split_train_holdout.py
    python eval/scripts/split_train_holdout.py --input eval/goldie/human-goldie-v2-audited.csv --out-dir eval/goldie

Idempotent — overwrites outputs.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

DEFAULT_INPUT = Path("eval/goldie/human-goldie-v2-audited.csv")
DEFAULT_OUT_DIR = Path("eval/goldie")
TRAIN_SIZE = 50
HOLDOUT_SIZE = 50


def split(input_path: Path, out_dir: Path) -> tuple[Path, Path]:
    with input_path.open(newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        if not fieldnames:
            raise SystemExit(f"empty or headerless CSV: {input_path}")
        rows = [r for r in reader if (r.get("No") or "").strip()]

    if len(rows) != TRAIN_SIZE + HOLDOUT_SIZE:
        raise SystemExit(
            f"expected {TRAIN_SIZE + HOLDOUT_SIZE} rows, got {len(rows)} in {input_path}"
        )

    rows.sort(key=lambda r: int(r["No"]))

    train_rows = rows[:TRAIN_SIZE]
    holdout_rows = rows[TRAIN_SIZE:]

    out_dir.mkdir(parents=True, exist_ok=True)
    train_path = out_dir / "train-50.csv"
    holdout_path = out_dir / "holdout-50.csv"

    for path, subset in ((train_path, train_rows), (holdout_path, holdout_rows)):
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(subset)

    print(f"input: {input_path} ({len(rows)} rows)")
    print(f"train-50.csv : {len(train_rows)} rows | first DOI={train_rows[0]['DOI']!r} last DOI={train_rows[-1]['DOI']!r}")
    print(f"holdout-50.csv: {len(holdout_rows)} rows | first DOI={holdout_rows[0]['DOI']!r} last DOI={holdout_rows[-1]['DOI']!r}")
    print(f"written to: {out_dir.resolve()}")
    return train_path, holdout_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help=f"default: {DEFAULT_INPUT}")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help=f"default: {DEFAULT_OUT_DIR}")
    args = parser.parse_args(argv)
    split(args.input, args.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
