import type { IndexEntry } from "../lib/schema";
import { pct } from "../lib/format";

interface Props {
  runs: IndexEntry[];
}

const METRICS = [
  // Two lines per field: precision + recall where available, else the legacy metric.
  { key: "authors_precision_soft", fallback: "authors_f1_soft", label: "Authors P", stroke: "var(--accent)", dash: "" },
  { key: "authors_recall_soft", fallback: "authors_f1_soft", label: "Authors R", stroke: "var(--accent)", dash: "4 3" },
  { key: "pdf_url_precision", fallback: "pdf_url_accuracy", label: "PDF P", stroke: "var(--ok)", dash: "" },
  { key: "pdf_url_recall", fallback: "pdf_url_accuracy", label: "PDF R", stroke: "var(--ok)", dash: "4 3" },
  { key: "abstract_match_rate", fallback: "abstract_ratio_fuzzy", label: "Abstract match", stroke: "var(--amber)", dash: "" },
] as const;

type Summary = Record<string, number | undefined>;

function resolve(summary: Summary, metric: (typeof METRICS)[number]): number {
  const v = summary[metric.key];
  if (typeof v === "number") return v;
  const fb = summary[metric.fallback];
  return typeof fb === "number" ? fb : 0;
}

export function TrendChart({ runs }: Props) {
  const ordered = [...runs].reverse();
  if (ordered.length < 2) {
    return (
      <p className="empty-state">
        Trend requires ≥ 2 runs. Currently {ordered.length}.
      </p>
    );
  }

  const W = 900;
  const H = 180;
  const padL = 44;
  const padR = 16;
  const padT = 16;
  const padB = 30;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const xFor = (i: number) => padL + (plotW * i) / Math.max(1, ordered.length - 1);
  const yFor = (v: number) => padT + plotH * (1 - Math.max(0, Math.min(1, v)));

  return (
    <div className="trend">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="Score trend over runs">
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => (
          <g key={tick}>
            <line
              x1={padL}
              x2={W - padR}
              y1={yFor(tick)}
              y2={yFor(tick)}
              stroke="var(--hairline-soft)"
              strokeWidth={1}
            />
            <text
              x={padL - 8}
              y={yFor(tick) + 4}
              textAnchor="end"
              fontSize={10}
              fill="var(--ink-faint)"
              fontFamily="var(--font-mono)"
            >
              {Math.round(tick * 100)}%
            </text>
          </g>
        ))}
        {METRICS.map((metric) => {
          const pts = ordered.map((r, i) => {
            const v = resolve(r.summary as Summary, metric);
            return `${xFor(i)},${yFor(v)}`;
          });
          return (
            <g key={metric.label}>
              <polyline
                fill="none"
                stroke={metric.stroke}
                strokeWidth={2}
                strokeDasharray={metric.dash || undefined}
                points={pts.join(" ")}
                vectorEffect="non-scaling-stroke"
              />
              {ordered.map((r, i) => {
                const v = resolve(r.summary as Summary, metric);
                return (
                  <circle
                    key={i}
                    cx={xFor(i)}
                    cy={yFor(v)}
                    r={3}
                    fill="var(--paper-raised)"
                    stroke={metric.stroke}
                    strokeWidth={2}
                  >
                    <title>
                      {metric.label}: {pct(v)} · {r.label ?? r.run_id}
                    </title>
                  </circle>
                );
              })}
            </g>
          );
        })}
        {ordered.map((r, i) => (
          <text
            key={i}
            x={xFor(i)}
            y={H - 10}
            textAnchor="middle"
            fontSize={10}
            fill="var(--ink-faint)"
            fontFamily="var(--font-mono)"
          >
            {r.label ?? (r.run_id ?? "").slice(4, 9)}
          </text>
        ))}
      </svg>
      <div className="legend">
        {METRICS.map((m) => (
          <span key={m.label}>
            <span
              className="swatch"
              style={{
                display: "inline-block",
                width: 18,
                height: 2,
                background: m.stroke,
                marginRight: "0.5em",
                verticalAlign: "middle",
                borderTop: m.dash ? `2px dashed ${"var(--paper-raised)"}` : undefined,
                // Visual hint for dashed recall line without extra CSS.
                opacity: m.dash ? 0.7 : 1,
              }}
            />
            {m.label}
          </span>
        ))}
      </div>
    </div>
  );
}
