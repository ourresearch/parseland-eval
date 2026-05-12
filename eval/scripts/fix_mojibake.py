"""Post-pass that runs ftfy on text fields of a v2 CSV.

Fixes mojibake like `â€™` → `'`, `â'…'â` → `'…'`, etc. Reads input, writes
output, never touches gold or human-goldie.csv.

Run:
    eval/.venv/bin/python eval/scripts/fix_mojibake.py \\
        --input  runs/10k/batch-1-judge/ai-goldie-1.v2.csv \\
        --output runs/10k/batch-1-judge/ai-goldie-1.v2.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import ftfy

TEXT_FIELDS = ("Authors", "Abstract", "Notes")


def _fix_authors_json(s: str) -> tuple[str, int]:
    if not s or s.strip() in ("", "[]"):
        return s, 0
    try:
        authors = json.loads(s)
    except Exception:
        return ftfy.fix_text(s), 1
    n = 0
    out = []
    for a in authors:
        new = dict(a)
        for k in ("name", "rasses"):
            v = new.get(k)
            if isinstance(v, str):
                fixed = ftfy.fix_text(v)
                if fixed != v:
                    n += 1
                new[k] = fixed
        out.append(new)
    return json.dumps(out, ensure_ascii=False), n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    with args.input.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    fixes = 0
    fields_touched: dict[str, int] = {}
    for r in rows:
        a_new, n = _fix_authors_json(r.get("Authors", ""))
        if n:
            r["Authors"] = a_new
            fixes += n
            fields_touched["Authors"] = fields_touched.get("Authors", 0) + n
        for f in ("Abstract", "Notes"):
            v = r.get(f, "")
            if isinstance(v, str) and v:
                fixed = ftfy.fix_text(v)
                if fixed != v:
                    r[f] = fixed
                    fixes += 1
                    fields_touched[f] = fields_touched.get(f, 0) + 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(args.output)

    print(f"wrote {args.output}")
    print(f"mojibake fixes: {fixes}")
    if fields_touched:
        for k, v in fields_touched.items():
            print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
