"""Principled tuning of ``ABSTRACT_MATCH_THRESHOLD``.

Given a run JSON, this tool recommends the threshold at which a fuzzy
Levenshtein ratio should be treated as a binary "match" for the abstract
field. The recommendation is label-free and reproducible: same input run →
same number, no eyeballing.

Method:

1. **Largest-gap midpoint above the match floor** (primary) — sort the
   paired ratios, **filter to values ≥ ``MATCH_FLOOR``** (default 0.5),
   find the widest remaining gap, return its midpoint. The floor is a
   domain prior: below 0.5, fewer than half the characters align and the
   texts definitionally aren't the same abstract. This isolates the real
   question — "where does the good-match cluster begin?" — from the
   distribution's multi-modal bad-match tail, which would otherwise
   dominate variance-based methods on trimodal data.

2. **Otsu's method** (Otsu, 1979) — classical unsupervised binary-split
   selector that maximizes inter-class variance. Included as a reference.
   On trimodal distributions (very-bad / partially-bad / good) Otsu is
   pulled between the very-bad and not-very-bad clusters, which isn't
   what we want; the floored gap-midpoint is more faithful to our
   "begin-of-good-cluster" question.

3. **KDE valley** — Gaussian KDE with bandwidth 0.05 on a 0-1 grid. Finds
   the lowest-density point between the two strongest modes. Sanity check.

4. **Bootstrap 95% CI** — 1000 resamples (with replacement), primary
   estimator re-run on each, 2.5/50/97.5 percentiles reported. Wide CI
   means the recommended value is unstable at this sample size; retune
   after gold expands.

All three are computed on both ``fuzzy_ratio`` (raw text) and
``soft_ratio`` (NFKC+casefold+diacritic-stripped). The current contract
thresholds ``fuzzy_ratio``; ``soft_ratio`` is printed side-by-side so a
future switch is a measured decision.

Usage:
    python scripts/tune_abstract_threshold.py [--run <path>]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path
from statistics import mean
from typing import Any

COARSE_THRESHOLDS = (0.70, 0.75, 0.80, 0.85, 0.90, 0.95)
TRUNCATION_MAX = 0.5
BLOAT_MIN = 1.5
OTSU_BINS = 200
KDE_BANDWIDTH = 0.05
KDE_GRID_POINTS = 1001
BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 42
STABILITY_CI_WIDTH = 0.15  # Warn above this
# Domain prior: anything below this many characters aligning is not the
# same abstract. The gap-midpoint estimator only considers ratios above
# this floor.
MATCH_FLOOR = 0.5


def _runs_dir() -> Path:
    here = Path(__file__).resolve().parent.parent
    return here / "runs"


def _latest_run(runs_dir: Path) -> Path | None:
    candidates = [p for p in runs_dir.glob("*.json") if p.name != "index.json"]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _extract(run_json: dict[str, Any]) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for row in run_json.get("rows", []):
        score = (row.get("score") or {}).get("abstract") or {}
        fuzzy = score.get("fuzzy_ratio")
        soft = score.get("soft_ratio")
        if fuzzy is None:
            continue
        out.append(
            {
                "fuzzy_ratio": float(fuzzy),
                "soft_ratio": float(soft if soft is not None else fuzzy),
                "length_ratio": float(score.get("length_ratio") or 0.0),
                "present": bool(score.get("present", False)),
                "expected_present": bool((row.get("gold") or {}).get("abstract")),
            }
        )
    return out


def _paired(rows: list[dict[str, float]], key: str) -> list[float]:
    return [r[key] for r in rows if r["expected_present"] and r["present"]]


# ---------- Otsu ----------


def _otsu(values: list[float], bins: int = OTSU_BINS) -> float:
    """Otsu's optimal binary split on a list of floats in [0, 1].

    Returns the threshold (bin-center) that maximizes inter-class variance.
    """
    if not values:
        return 0.0
    hist = [0] * bins
    for v in values:
        idx = min(bins - 1, max(0, int(v * bins)))
        hist[idx] += 1
    total = len(values)
    sum_total = sum(i * hist[i] for i in range(bins))
    w0 = 0
    sum0 = 0.0
    best_var = -1.0
    best_bin = 0
    for t in range(bins):
        w0 += hist[t]
        if w0 == 0:
            continue
        w1 = total - w0
        if w1 == 0:
            break
        sum0 += t * hist[t]
        mu0 = sum0 / w0
        mu1 = (sum_total - sum0) / w1
        var_between = w0 * w1 * (mu0 - mu1) ** 2
        if var_between > best_var:
            best_var = var_between
            best_bin = t
    return (best_bin + 0.5) / bins


# ---------- KDE valley ----------


def _kde_valley(values: list[float], bandwidth: float = KDE_BANDWIDTH) -> float | None:
    """Find the lowest-density point between the two strongest KDE modes.

    Returns ``None`` if fewer than two modes are detectable.
    """
    if len(values) < 4:
        return None
    grid = [i / (KDE_GRID_POINTS - 1) for i in range(KDE_GRID_POINTS)]
    densities: list[float] = []
    norm = 1.0 / (bandwidth * math.sqrt(2 * math.pi))
    for x in grid:
        d = 0.0
        for v in values:
            z = (x - v) / bandwidth
            d += math.exp(-0.5 * z * z)
        densities.append(d * norm)
    # Local maxima (strict) on the interior.
    peaks: list[tuple[float, int]] = []
    for i in range(1, len(grid) - 1):
        if densities[i] > densities[i - 1] and densities[i] > densities[i + 1]:
            peaks.append((densities[i], i))
    if len(peaks) < 2:
        return None
    peaks.sort(reverse=True)
    left_idx, right_idx = sorted([peaks[0][1], peaks[1][1]])
    valley_rel = min(
        range(right_idx - left_idx + 1),
        key=lambda j: densities[left_idx + j],
    )
    return grid[left_idx + valley_rel]


# ---------- Largest-gap midpoint ----------


def _largest_gap(values: list[float], *, floor: float = MATCH_FLOOR) -> tuple[float, float, float]:
    """Find the widest gap between adjacent sorted values above the floor.

    Returns ``(midpoint, lower_edge, upper_edge)``. Filters out values
    below ``floor`` before looking for gaps — by prior, ratios below the
    floor are not candidate match boundaries.

    Fallback behaviour:
    - Fewer than 2 values above the floor → ``(floor, floor, 1.0)`` so the
      recommended threshold defaults to the floor rather than returning
      something meaningless.
    - All values above the floor are identical → ``(v, v, v)``.
    """
    kept = [v for v in values if v >= floor]
    if not kept:
        return floor, floor, 1.0
    if len(kept) < 2:
        v = kept[0]
        return v, v, v
    s = sorted(kept)
    best_gap = -1.0
    best_lo = s[0]
    best_hi = s[-1]
    for a, b in zip(s, s[1:]):
        gap = b - a
        if gap > best_gap:
            best_gap = gap
            best_lo = a
            best_hi = b
    return (best_lo + best_hi) / 2, best_lo, best_hi


# ---------- Bootstrap ----------


def _bootstrap(
    values: list[float],
    estimator,
    *,
    n: int = BOOTSTRAP_N,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float, float]:
    """Return (p2.5, p50, p97.5) of a scalar estimator across bootstrap resamples."""
    if not values:
        return 0.0, 0.0, 0.0
    rng = random.Random(seed)
    out: list[float] = []
    length = len(values)
    for _ in range(n):
        sample = [values[rng.randrange(length)] for _ in range(length)]
        out.append(estimator(sample))
    out.sort()
    m = len(out) - 1

    def pct(p: float) -> float:
        return out[max(0, min(m, int(round(p * m))))]

    return pct(0.025), pct(0.5), pct(0.975)


def _gap_midpoint(values: list[float]) -> float:
    return _largest_gap(values)[0]


# ---------- Coarse sweep (legacy, kept for human scanning) ----------


def _coarse_sweep(rows: list[dict[str, float]]) -> list[dict[str, Any]]:
    n = len(rows)
    results: list[dict[str, Any]] = []
    for t in COARSE_THRESHOLDS:
        matches = 0
        truncated = 0
        bloated = 0
        for r in rows:
            if not r["expected_present"] and not r["present"]:
                matches += 1
                continue
            if not r["expected_present"] or not r["present"]:
                continue
            if r["fuzzy_ratio"] >= t:
                matches += 1
                if r["length_ratio"] < TRUNCATION_MAX:
                    truncated += 1
                elif r["length_ratio"] > BLOAT_MIN:
                    bloated += 1
        results.append(
            {
                "threshold": t,
                "match_rate": matches / n if n else 0.0,
                "matches": matches,
                "truncated_matches": truncated,
                "bloated_matches": bloated,
            }
        )
    return results


# ---------- Rendering ----------


def _render_sweep(summary: list[dict[str, Any]], n_rows: int) -> str:
    lines = [
        f"Coarse sweep — {n_rows} rows",
        "",
        "| threshold | matches | match_rate | truncated (<0.5) | bloated (>1.5) |",
        "|-----------|---------|------------|------------------|----------------|",
    ]
    for row in summary:
        lines.append(
            f"| {row['threshold']:.2f}      | {row['matches']:>7} | "
            f"{row['match_rate']:>10.3f} | {row['truncated_matches']:>16} | "
            f"{row['bloated_matches']:>14} |"
        )
    return "\n".join(lines)


def _render_distribution(paired_fuzzy: list[float], paired_soft: list[float]) -> str:
    def qline(label: str, vals: list[float]) -> str:
        if not vals:
            return f"  {label}: (empty)"
        s = sorted(vals)
        n = len(s)

        def q(p: float) -> float:
            return s[max(0, min(n - 1, int(round(p * (n - 1)))))]

        return (
            f"  {label} (n={n}): mean={mean(vals):.3f} "
            f"p10={q(0.10):.3f} p25={q(0.25):.3f} p50={q(0.50):.3f} "
            f"p75={q(0.75):.3f} p90={q(0.90):.3f}"
        )

    return "Distribution of paired-row ratios:\n" + "\n".join(
        [qline("fuzzy_ratio (raw)      ", paired_fuzzy), qline("soft_ratio  (normalized)", paired_soft)]
    )


def _render_recommendation(
    paired_fuzzy: list[float],
    paired_soft: list[float],
) -> tuple[str, float, float]:
    """Return (rendered_block, fuzzy_recommendation, fuzzy_ci_width)."""
    fuzzy_mid, fuzzy_lo, fuzzy_hi = _largest_gap(paired_fuzzy)
    fuzzy_otsu = _otsu(paired_fuzzy)
    fuzzy_kde = _kde_valley(paired_fuzzy)
    fuzzy_ci = _bootstrap(paired_fuzzy, _gap_midpoint)

    soft_mid, soft_lo, soft_hi = _largest_gap(paired_soft)
    soft_otsu = _otsu(paired_soft)
    soft_kde = _kde_valley(paired_soft)
    soft_ci = _bootstrap(paired_soft, _gap_midpoint)

    def fmt_kde(v: float | None) -> str:
        return f"{v:.3f}" if v is not None else "—"

    fuzzy_ci_width = fuzzy_ci[2] - fuzzy_ci[0]
    soft_ci_width = soft_ci[2] - soft_ci[0]
    fuzzy_gap_width = fuzzy_hi - fuzzy_lo
    soft_gap_width = soft_hi - soft_lo

    def warn(width: float, label: str) -> str:
        if width <= STABILITY_CI_WIDTH:
            return ""
        return f"  ⚠ {label} CI > {STABILITY_CI_WIDTH:.2f}"

    block = (
        f"Recommendation — gap-midpoint above floor={MATCH_FLOOR:.2f} (primary)\n"
        f"  fuzzy_ratio (raw)       : "
        f"midpoint {fuzzy_mid:.3f}  "
        f"(gap [{fuzzy_lo:.3f}, {fuzzy_hi:.3f}], width {fuzzy_gap_width:.3f})  "
        f"95% CI [{fuzzy_ci[0]:.3f}, {fuzzy_ci[2]:.3f}]{warn(fuzzy_ci_width, 'bootstrap')}\n"
        f"  soft_ratio  (normalized): "
        f"midpoint {soft_mid:.3f}  "
        f"(gap [{soft_lo:.3f}, {soft_hi:.3f}], width {soft_gap_width:.3f})  "
        f"95% CI [{soft_ci[0]:.3f}, {soft_ci[2]:.3f}]{warn(soft_ci_width, 'bootstrap')}\n"
        "\nReference methods (for comparison, not the recommendation)\n"
        f"  fuzzy_ratio: Otsu {fuzzy_otsu:.3f}   KDE valley {fmt_kde(fuzzy_kde)}\n"
        f"  soft_ratio : Otsu {soft_otsu:.3f}   KDE valley {fmt_kde(soft_kde)}\n"
        f"\n  → commit ABSTRACT_MATCH_THRESHOLD = {fuzzy_mid:.2f}"
    )
    return block, fuzzy_mid, fuzzy_ci_width


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        type=Path,
        default=None,
        help="Path to run JSON under eval/runs/. Default: most recent.",
    )
    args = parser.parse_args(argv)

    if args.run is None:
        latest = _latest_run(_runs_dir())
        if latest is None:
            print("No runs found under eval/runs/", file=sys.stderr)
            return 2
        args.run = latest

    run_json = json.loads(args.run.read_text(encoding="utf-8"))
    rows = _extract(run_json)
    if not rows:
        print(f"No scoreable rows in {args.run}", file=sys.stderr)
        return 1

    paired_fuzzy = _paired(rows, "fuzzy_ratio")
    paired_soft = _paired(rows, "soft_ratio")

    print(f"Source run: {args.run.name}")
    print(_render_sweep(_coarse_sweep(rows), len(rows)))
    print()
    print(_render_distribution(paired_fuzzy, paired_soft))
    print()
    block, recommended, ci_width = _render_recommendation(paired_fuzzy, paired_soft)
    print(block)

    if ci_width > STABILITY_CI_WIDTH:
        print(
            "\nNOTE: fuzzy_ratio bootstrap CI is wider than "
            f"{STABILITY_CI_WIDTH:.2f}. The recommendation is unstable at this "
            "sample size — retune after gold expands.",
            file=sys.stderr,
        )
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
