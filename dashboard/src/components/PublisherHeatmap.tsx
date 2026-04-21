import type { PerPublisher } from "../lib/schema";
import { heatColor, heatTextColor } from "../lib/palette";
import { pct } from "../lib/format";

interface Props {
  data: PerPublisher;
  minRows?: number;
}

type PublisherRow = PerPublisher[string];

interface FieldGroup {
  label: string;
  metrics: { key: keyof PublisherRow; short: "P" | "R" | "•"; fallback: keyof PublisherRow }[];
}

// Each field gets two cells (Precision, Recall) when the run carries them,
// falling back to the single legacy metric if not.
const GROUPS: FieldGroup[] = [
  {
    label: "Authors",
    metrics: [
      { key: "authors_precision_soft", short: "P", fallback: "authors_f1_soft" },
      { key: "authors_recall_soft", short: "R", fallback: "authors_f1_soft" },
    ],
  },
  {
    label: "Affiliations",
    metrics: [
      { key: "affiliations_precision_fuzzy", short: "P", fallback: "affiliations_f1_fuzzy" },
      { key: "affiliations_recall_fuzzy", short: "R", fallback: "affiliations_f1_fuzzy" },
    ],
  },
  {
    label: "Abstract",
    metrics: [
      { key: "abstract_match_rate", short: "•", fallback: "abstract_ratio_fuzzy" },
    ],
  },
  {
    label: "PDF URL",
    metrics: [
      { key: "pdf_url_precision", short: "P", fallback: "pdf_url_accuracy" },
      { key: "pdf_url_recall", short: "R", fallback: "pdf_url_accuracy" },
    ],
  },
];

export function PublisherHeatmap({ data, minRows = 2 }: Props) {
  const rows = Object.entries(data)
    .filter(([, stats]) => stats.rows >= minRows)
    .sort((a, b) => b[1].rows - a[1].rows)
    .slice(0, 18);

  if (rows.length === 0) {
    return <p className="empty-state">No publishers with ≥ {minRows} gold-standard rows.</p>;
  }

  return (
    <div className="heatmap card">
      <table>
        <thead>
          <tr>
            <th rowSpan={2} style={{ width: "26%" }}>Publisher domain</th>
            {GROUPS.map((g) => (
              <th
                key={g.label}
                colSpan={g.metrics.length}
                style={{ textAlign: "center", borderBottom: "1px solid var(--hairline-soft)" }}
              >
                {g.label}
              </th>
            ))}
            <th rowSpan={2} style={{ textAlign: "right", paddingRight: "var(--space-3)" }}>N</th>
          </tr>
          <tr>
            {GROUPS.flatMap((g) =>
              g.metrics.map((m) => (
                <th
                  key={`${g.label}-${m.short}-${String(m.key)}`}
                  style={{
                    textAlign: "center",
                    fontSize: "var(--text-micro)",
                    color: "var(--ink-faint)",
                    letterSpacing: "0.08em",
                    textTransform: "uppercase",
                    fontWeight: 500,
                  }}
                >
                  {m.short === "•" ? "match" : m.short}
                </th>
              ))
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map(([domain, stats]) => (
            <tr key={domain}>
              <td className="label">{domain || "—"}</td>
              {GROUPS.flatMap((g) =>
                g.metrics.map((m) => {
                  const raw = stats[m.key] as number | undefined;
                  const v = typeof raw === "number" ? raw : ((stats[m.fallback] as number) ?? 0);
                  const title =
                    `${domain} · ${g.label} · ${m.short === "•" ? "match" : m.short} · ${pct(v)}` +
                    (raw === undefined ? " (legacy fallback)" : "");
                  return (
                    <td
                      key={`${domain}-${g.label}-${m.short}-${String(m.key)}`}
                      className="cell"
                      style={{ background: heatColor(v), color: heatTextColor(v) }}
                      title={title}
                    >
                      {pct(v)}
                    </td>
                  );
                })
              )}
              <td
                style={{
                  textAlign: "right",
                  fontFamily: "var(--font-mono)",
                  color: "var(--ink-muted)",
                  paddingRight: "var(--space-3)",
                  fontSize: "var(--text-micro)",
                }}
              >
                {stats.rows}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
