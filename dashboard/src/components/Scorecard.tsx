import type { Overall } from "../lib/schema";
import { pct } from "../lib/format";
import { DeltaBadge } from "./DeltaBadge";

interface Props {
  current: Overall;
  previous: Overall | null;
}

interface StatCard {
  kicker: string;
  primary: { label: string; value: number | undefined }[]; // P / R (or a single ratio)
  subtitle: string;
  delta: { current: number; previous: number | null };
}

const fmt = (v: number | undefined): string =>
  v === undefined || !Number.isFinite(v) ? "—" : `${Math.round(v * 1000) / 10}`;

export function Scorecard({ current, previous }: Props) {
  // Prefer precision/recall when the run carries them; fall back to F1 / ratio
  // so pre-2026-04-21 runs still render without blanks.
  const stats: StatCard[] = [
    {
      kicker: "Authors · Soft P / R",
      primary: [
        { label: "P", value: current.authors_precision_soft ?? current.authors_f1_soft },
        { label: "R", value: current.authors_recall_soft ?? current.authors_f1_soft },
      ],
      subtitle:
        `F1 soft: ${pct(current.authors_f1_soft)}  ·  F1 strict: ${pct(current.authors_f1_strict)}  ·  ${current.authors_scored_rows} scored`,
      delta: {
        current: current.authors_f1_soft,
        previous: previous?.authors_f1_soft ?? null,
      },
    },
    {
      kicker: "Affiliations · Fuzzy P / R",
      primary: [
        { label: "P", value: current.affiliations_precision_fuzzy ?? current.affiliations_f1_fuzzy },
        { label: "R", value: current.affiliations_recall_fuzzy ?? current.affiliations_f1_fuzzy },
      ],
      subtitle:
        `F1 fuzzy: ${pct(current.affiliations_f1_fuzzy)}  ·  F1 soft: ${pct(current.affiliations_f1_soft)}  ·  F1 strict: ${pct(current.affiliations_f1_strict)}`,
      delta: {
        current: current.affiliations_f1_fuzzy,
        previous: previous?.affiliations_f1_fuzzy ?? null,
      },
    },
    {
      kicker: `Abstract · match @ ${
        current.abstract_match_threshold !== undefined
          ? (current.abstract_match_threshold * 100).toFixed(0) + "%"
          : "threshold"
      }`,
      primary: [
        {
          label: "match",
          value: current.abstract_match_rate ?? current.abstract_ratio_fuzzy,
        },
      ],
      subtitle:
        `Levenshtein: ${pct(current.abstract_ratio_fuzzy)}  ·  Exact: ${pct(current.abstract_strict_match_rate)}  ·  Present: ${pct(current.abstract_present_rate)}`,
      delta: {
        current: current.abstract_match_rate ?? current.abstract_ratio_fuzzy,
        previous:
          previous?.abstract_match_rate ?? previous?.abstract_ratio_fuzzy ?? null,
      },
    },
    {
      kicker: "PDF URL · micro P / R",
      primary: [
        { label: "P", value: current.pdf_url_precision ?? current.pdf_url_accuracy },
        { label: "R", value: current.pdf_url_recall ?? current.pdf_url_accuracy },
      ],
      subtitle:
        `Accuracy: ${pct(current.pdf_url_accuracy)}  ·  Divergent: ${pct(current.pdf_url_divergence_rate)}  ·  Errors: ${current.errors}`,
      delta: {
        current: current.pdf_url_accuracy,
        previous: previous?.pdf_url_accuracy ?? null,
      },
    },
  ];

  return (
    <section aria-label="Top-line scorecard" className="scorecard">
      {stats.map((s) => (
        <div key={s.kicker} className="stat">
          <div className="kicker">{s.kicker}</div>
          <div
            className="value"
            style={{
              display: "flex",
              alignItems: "baseline",
              gap: "var(--space-3)",
              flexWrap: "wrap",
            }}
          >
            {s.primary.map((p) => (
              <span key={p.label} style={{ display: "inline-flex", alignItems: "baseline", gap: "0.25em" }}>
                {s.primary.length > 1 && (
                  <span
                    style={{
                      fontSize: "var(--text-micro)",
                      color: "var(--ink-faint)",
                      letterSpacing: "0.08em",
                      textTransform: "uppercase",
                      marginRight: "0.15em",
                    }}
                  >
                    {p.label}
                  </span>
                )}
                <span>{fmt(p.value)}</span>
                <span className="unit">%</span>
              </span>
            ))}
          </div>
          <div className="detail">
            <DeltaBadge current={s.delta.current} previous={s.delta.previous} />
            <span>{s.subtitle}</span>
          </div>
        </div>
      ))}
    </section>
  );
}
