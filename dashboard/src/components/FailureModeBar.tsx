import type { PerFailureMode } from "../lib/schema";
import { failureColor } from "../lib/palette";
import { pct } from "../lib/format";

interface Props {
  data: PerFailureMode;
}

type Entry = PerFailureMode[string];

function authorsLine(stats: Entry): string {
  const p = stats.authors_precision_soft;
  const r = stats.authors_recall_soft;
  if (p !== undefined && r !== undefined) {
    return `Authors P ${pct(p)} / R ${pct(r)}`;
  }
  return `Authors F1 ${pct(stats.authors_f1_soft)}`;
}

function abstractLine(stats: Entry): string {
  const m = stats.abstract_match_rate;
  if (m !== undefined) return `Abstract match ${pct(m)}`;
  return `Abstract ratio ${pct(stats.abstract_ratio_fuzzy)}`;
}

function pdfLine(stats: Entry): string {
  const p = stats.pdf_url_precision;
  const r = stats.pdf_url_recall;
  if (p !== undefined && r !== undefined) {
    return `PDF P ${pct(p)} / R ${pct(r)}`;
  }
  return `PDF ${pct(stats.pdf_url_accuracy)}`;
}

export function FailureModeBar({ data }: Props) {
  const entries = Object.entries(data).sort((a, b) => b[1].rows - a[1].rows);
  const total = entries.reduce((s, [, v]) => s + v.rows, 0);

  if (total === 0) return <p className="empty-state">No failure-mode data.</p>;

  return (
    <div>
      <div
        className="failure-bar"
        role="img"
        aria-label={`Failure-mode distribution across ${total} rows`}
      >
        {entries.map(([mode, stats]) => {
          const share = stats.rows / total;
          if (share === 0) return null;
          return (
            <div
              key={mode}
              style={{
                flex: `${stats.rows} 0 0`,
                background: failureColor(mode),
              }}
              title={`${mode}: ${stats.rows} rows (${pct(share)})  ·  ${authorsLine(stats)}`}
            >
              {share > 0.08 ? mode.replace("_", " ") : null}
            </div>
          );
        })}
      </div>
      <ul className="failure-legend" style={{ listStyle: "none", padding: 0, margin: 0 }}>
        {entries.map(([mode, stats]) => (
          <li key={mode}>
            <span className="swatch" style={{ background: failureColor(mode) }} />
            <span style={{ color: "var(--ink-soft)", marginRight: "0.5em" }}>
              {mode.replace(/_/g, " ")}
            </span>
            <span>
              {stats.rows}  ·  {authorsLine(stats)}  ·  {abstractLine(stats)}  ·  {pdfLine(stats)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
