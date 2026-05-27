"""Apply ``fix_gold`` rows from a parseland-lib mismatch CSV to merged-FINAL.csv.

This is the gold-side counterpart of the parser-patch loop. The mismatch CSV
(produced by ``parseland-lib/scripts/elsevier_mismatch.py``) tags each
disagreement row with a ``suggested_action``. This script consumes the rows
where that action is ``fix_gold`` — meaning HTML grounding showed the gold
text isn't on the page but the parser's is — and updates the gold accordingly
in the source-of-truth CSV that the eval NDJSON is derived from.

Truth source: the **HTML page** (already established by the mismatch script's
``gold_on_page=False, parser_on_page=True`` predicate). Parseland is one of
two candidates; the gold annotation was the other. The page disagreed with
the gold, so we replace.

For each ``fix_gold`` row:

  - ``gold_no_affs_parser_has`` (gold Authors list is empty) → populate with
    parser's authors + affiliations.
  - ``zero_f1_gold_wrong`` or ``zero_f1_both_on_page_format_diff`` (gold has
    authors with rasses, but rasses don't match the page) → match each gold
    author to a parser author by name (fuzzy) and overwrite the rasses field.
    Gold authors with no parser match keep their existing rasses.

Outputs:

  - In-place update of ``merged-FINAL.csv``.
  - Backup at ``merged-FINAL.csv.bak-{YYYYmmdd-HHMMSS}`` (timestamped — every
    run leaves an audit trail).
  - ``gold-delta-{date}.diff.json`` next to the mismatch CSV — every changed
    ``(doi, author_name, old_rasses, new_rasses, verdict_bucket)`` row plus a
    summary block.

Usage:

    cd parseland-eval
    .venv/bin/python eval/scripts/apply_gold_delta.py \\
        --mismatch-csv ../parseland-lib/mismatches/elsevier_2026-05-26/affiliations.csv \\
        --merged-csv  eval/data/merged-FINAL.csv \\
        --field affiliations

    # Dry-run: write the diff log but don't touch the CSV
    .venv/bin/python eval/scripts/apply_gold_delta.py \\
        --mismatch-csv ../parseland-lib/mismatches/elsevier_2026-05-26/affiliations.csv \\
        --merged-csv  eval/data/merged-FINAL.csv \\
        --field affiliations \\
        --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
from pathlib import Path

# rapidfuzz is already a parseland-eval dep
from rapidfuzz import fuzz


def _now_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _normalize_name(n: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    return "".join(c.lower() if c.isalnum() or c.isspace() else " " for c in (n or ""))


def _parse_parser_value_affiliations(s: str) -> dict[str, list[str]]:
    """Parse a mismatch-CSV parser_value cell for the affiliations field.

    Input format (locked by elsevier_mismatch.py):

        Author Name::Aff1 || Other Author::Aff2 || Author Name::Aff3

    Note: an author with multiple affiliations appears as multiple ``::``
    entries with the same name. Output groups them.
    """
    out: dict[str, list[str]] = {}
    if not s:
        return out
    for chunk in s.split(" || "):
        if "::" not in chunk:
            continue
        name, _, aff = chunk.partition("::")
        name = name.strip()
        aff = aff.strip()
        if not name or not aff:
            continue
        out.setdefault(name, []).append(aff)
    return out


def _match_author(gold_name: str, parser_names: list[str], threshold: float = 80.0) -> str | None:
    """Match a gold author name to a parser author name via token_set_ratio.

    Returns the parser name if best match >= ``threshold``, else None.
    ``threshold`` is the same band parseland-eval's authors scorer uses for
    soft-matching, so two annotators that agree on the author score-wise also
    agree on the rasses transfer here.
    """
    gn = _normalize_name(gold_name)
    if not gn:
        return None
    best, best_score = None, 0.0
    for pn in parser_names:
        score = fuzz.token_set_ratio(gn, _normalize_name(pn))
        if score > best_score:
            best, best_score = pn, score
    return best if best_score >= threshold else None


def _load_fix_gold_rows(mismatch_csv: Path, field: str) -> dict[str, dict]:
    """Return {doi: {verdict_bucket, parser_authors: {name: [affs]}, gold_value, parser_value}}."""
    out: dict[str, dict] = {}
    with mismatch_csv.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("suggested_action") != "fix_gold":
                continue
            if row.get("field") != field:
                continue
            doi = (row.get("doi") or "").strip()
            if not doi:
                continue
            if field == "affiliations":
                parser_affs = _parse_parser_value_affiliations(row.get("parser_value") or "")
            else:
                # Future fields can plug in their own parsers here.
                raise NotImplementedError(f"field {field} not supported yet")
            out[doi] = {
                "verdict_bucket": row.get("verdict_bucket"),
                "parser_authors": parser_affs,
                "gold_value": row.get("gold_value"),
                "parser_value": row.get("parser_value"),
            }
    return out


def _apply_to_authors_json(
    raw_json: str,
    parser_authors: dict[str, list[str]],
    bucket: str,
) -> tuple[str, list[dict]]:
    """Apply parser affiliations to the JSON-encoded Authors cell.

    Returns ``(new_json_string, change_records)``. ``change_records`` is a
    list of dicts describing each per-author change for the audit log.
    """
    changes: list[dict] = []

    if not raw_json or not raw_json.strip():
        # Gold author list empty — populate from parser entirely.
        new_authors = []
        for name, affs in parser_authors.items():
            rasses = "; ".join(affs)
            new_authors.append(
                {
                    "name": name,
                    "rasses": rasses,
                    "corresponding_author": False,
                }
            )
            changes.append(
                {
                    "author_name": name,
                    "old_rasses": "",
                    "new_rasses": rasses,
                    "operation": "populate_empty_authors",
                    "bucket": bucket,
                }
            )
        return json.dumps(new_authors, ensure_ascii=False), changes

    try:
        gold_authors = json.loads(raw_json)
    except json.JSONDecodeError:
        # Malformed JSON — leave untouched so we don't make things worse.
        return raw_json, []
    if not isinstance(gold_authors, list):
        return raw_json, []

    if not gold_authors and parser_authors:
        # Empty list in JSON (vs missing) — populate.
        for name, affs in parser_authors.items():
            rasses = "; ".join(affs)
            gold_authors.append(
                {
                    "name": name,
                    "rasses": rasses,
                    "corresponding_author": False,
                }
            )
            changes.append(
                {
                    "author_name": name,
                    "old_rasses": "",
                    "new_rasses": rasses,
                    "operation": "populate_empty_authors",
                    "bucket": bucket,
                }
            )
        return json.dumps(gold_authors, ensure_ascii=False), changes

    parser_names = list(parser_authors.keys())
    matched_parser_names: set[str] = set()

    # Per-gold-author overwrite via fuzzy name match.
    for ga in gold_authors:
        if not isinstance(ga, dict):
            continue
        gold_name = ga.get("name") or ""
        if not gold_name:
            continue
        match = _match_author(gold_name, parser_names)
        if match is None:
            continue
        new_rasses = "; ".join(parser_authors[match])
        old_rasses = ga.get("rasses") or ""
        if new_rasses == old_rasses:
            continue
        ga["rasses"] = new_rasses
        matched_parser_names.add(match)
        changes.append(
            {
                "author_name": gold_name,
                "matched_parser_name": match,
                "old_rasses": old_rasses,
                "new_rasses": new_rasses,
                "operation": "overwrite_rasses",
                "bucket": bucket,
            }
        )

    # Parser-only authors (in parser but not matched to any gold author) — add
    # them. Useful when the gold author list is incomplete on the same row.
    for pname in parser_names:
        if pname in matched_parser_names:
            continue
        if any(_match_author(pname, [ga.get("name") or ""])
               for ga in gold_authors if isinstance(ga, dict)):
            continue
        rasses = "; ".join(parser_authors[pname])
        gold_authors.append(
            {
                "name": pname,
                "rasses": rasses,
                "corresponding_author": False,
            }
        )
        changes.append(
            {
                "author_name": pname,
                "old_rasses": "",
                "new_rasses": rasses,
                "operation": "add_missing_author",
                "bucket": bucket,
            }
        )

    return json.dumps(gold_authors, ensure_ascii=False), changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mismatch-csv", required=True, type=Path)
    ap.add_argument("--merged-csv", required=True, type=Path)
    ap.add_argument(
        "--field",
        required=True,
        choices=["affiliations"],
        help="which field to apply (only affiliations supported initially)",
    )
    ap.add_argument("--dry-run", action="store_true", help="write diff log, don't modify CSV")
    args = ap.parse_args()

    if not args.mismatch_csv.exists():
        print(f"ERROR: mismatch CSV not found: {args.mismatch_csv}", file=sys.stderr)
        return 1
    if not args.merged_csv.exists():
        print(f"ERROR: merged CSV not found: {args.merged_csv}", file=sys.stderr)
        return 1

    fix_gold_by_doi = _load_fix_gold_rows(args.mismatch_csv, args.field)
    print(f"loaded {len(fix_gold_by_doi)} fix_gold rows from {args.mismatch_csv.name}")

    # Backup
    backup: Path | None = None
    if not args.dry_run:
        backup = args.merged_csv.with_suffix(args.merged_csv.suffix + f".bak-{_now_stamp()}")
        shutil.copy2(args.merged_csv, backup)
        print(f"backup: {backup.name}")

    # Read all rows
    with args.merged_csv.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        all_rows = list(reader)
    print(f"loaded {len(all_rows)} rows from {args.merged_csv.name}")

    if "Authors" not in (fieldnames or []):
        print("ERROR: 'Authors' column not found in merged CSV", file=sys.stderr)
        return 1
    if "DOI" not in (fieldnames or []):
        print("ERROR: 'DOI' column not found in merged CSV", file=sys.stderr)
        return 1

    rows_touched = 0
    author_changes: list[dict] = []
    by_bucket: dict[str, int] = {}
    skipped_no_doi = 0

    for row in all_rows:
        doi = (row.get("DOI") or "").strip()
        if not doi or doi not in fix_gold_by_doi:
            continue
        entry = fix_gold_by_doi[doi]
        new_json, changes = _apply_to_authors_json(
            row.get("Authors") or "",
            entry["parser_authors"],
            entry["verdict_bucket"],
        )
        if changes:
            rows_touched += 1
            by_bucket[entry["verdict_bucket"]] = by_bucket.get(entry["verdict_bucket"], 0) + 1
            for c in changes:
                c["doi"] = doi
                author_changes.append(c)
            row["Authors"] = new_json

    print(f"\nrows touched: {rows_touched}")
    print(f"author-level changes: {len(author_changes)}")
    print(f"by bucket: {by_bucket}")

    # Write back
    if not args.dry_run:
        with args.merged_csv.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in all_rows:
                writer.writerow(row)
        print(f"updated {args.merged_csv}")

    # Audit log lives next to the mismatch CSV
    audit_path = args.mismatch_csv.parent / f"gold-delta-{_now_stamp()}.diff.json"
    audit = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mismatch_csv": str(args.mismatch_csv),
        "merged_csv": str(args.merged_csv),
        "backup": str(backup) if backup else None,
        "field": args.field,
        "dry_run": args.dry_run,
        "rows_touched": rows_touched,
        "author_changes_total": len(author_changes),
        "by_bucket": by_bucket,
        "changes": author_changes,
    }
    audit_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False))
    print(f"audit log: {audit_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
