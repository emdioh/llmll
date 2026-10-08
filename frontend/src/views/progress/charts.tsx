import { useState, type KeyboardEvent } from "react";
import type {
  ActivityDay,
  FacetDetail,
  ForecastDay,
  WeekAccuracy,
} from "../../api/client";
import { fmtDay, pct, parseDay, STATE_LABELS } from "./util";

/* Inline-SVG charts. Colours come from the --viz-* tokens in index.css (validated with the
 * dataviz palette script, light and dark). Every chart has a text readout (hover, tap or
 * keyboard) and a "Table" disclosure, so no information depends on colour or pointer. */

function Legend({
  items,
}: {
  items: { label: string; color: string; shape?: "line" | "box" | "diamond" }[];
}) {
  return (
    <ul className="legend" aria-label="Legend">
      {items.map((it) => (
        <li key={it.label}>
          <svg width="16" height="10" aria-hidden="true">
            {it.shape === "line" ? (
              <line
                x1="0"
                x2="16"
                y1="5"
                y2="5"
                stroke={it.color}
                strokeWidth="2"
                strokeLinecap="round"
              />
            ) : it.shape === "diamond" ? (
              <path d="M8 0 L13 5 L8 10 L3 5 Z" fill={it.color} />
            ) : (
              <rect width="10" height="10" rx="2" fill={it.color} />
            )}
          </svg>{" "}
          {it.label}
        </li>
      ))}
    </ul>
  );
}

function Table({
  caption,
  head,
  rows,
}: {
  caption: string;
  head: string[];
  rows: (string | number)[][];
}) {
  return (
    <details>
      <summary>Table</summary>
      <table>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {head.map((h, i) => (
              <th key={i} scope="col">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((c, j) => (
                <td key={j}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

/* ---------- study calendar heatmap ---------- */

const CELL = 12;
const GAP = 3;
const STEP = CELL + GAP;
const LEFT = 22;
const TOP = 14;

function intensity(d: ActivityDay): number {
  const n = d.reviews + d.exercises + d.readings;
  if (!d.active || n === 0) return 0;
  if (n < 10) return 1;
  if (n < 25) return 2;
  if (n < 50) return 3;
  return 4;
}

function dayDescription(d: ActivityDay): string {
  const parts = [
    `${d.reviews} reviews`,
    `${d.exercises} exercises`,
    `${d.readings} readings`,
  ];
  return d.active
    ? `${fmtDay(d.date, true)}: ${parts.join(", ")}, ${Math.round(d.minutes)} min`
    : `${fmtDay(d.date, true)}: no study`;
}

export function Heatmap({ days }: { days: ActivityDay[] }) {
  const [sel, setSel] = useState<ActivityDay | null>(null);
  const head = days[0];
  if (!head) return null;
  // Monday-based columns; pad the first week with empty slots.
  const first = (parseDay(head.date).getUTCDay() + 6) % 7;
  const slots: (ActivityDay | null)[] = [
    ...Array<null>(first).fill(null),
    ...days,
  ];
  const cols = Math.ceil(slots.length / 7);
  const width = LEFT + cols * STEP;
  const height = TOP + 7 * STEP;
  const fills: [string, string, string, string, string] = [
    "var(--viz-track)",
    "var(--viz-seq-1)",
    "var(--viz-seq-2)",
    "var(--viz-seq-3)",
    "var(--viz-seq-4)",
  ];
  const monthLabels: { x: number; text: string }[] = [];
  let lastMonth = -1;
  let lastX = -100;
  slots.forEach((d, i) => {
    if (!d || i % 7 !== 0) return;
    const m = parseDay(d.date).getUTCMonth();
    const x = LEFT + Math.floor(i / 7) * STEP;
    if (m !== lastMonth) {
      lastMonth = m;
      // Skip a label that would collide with the previous one.
      if (x - lastX < 28) return;
      lastX = x;
      monthLabels.push({
        x: LEFT + Math.floor(i / 7) * STEP,
        text: parseDay(d.date).toLocaleDateString(undefined, {
          month: "short",
          timeZone: "UTC",
        }),
      });
    }
  });
  const active = days.filter((d) => d.active);
  return (
    <figure className="chart">
      <figcaption>Study calendar</figcaption>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`Study calendar, last ${days.length} days: ${active.length} study days`}
        onPointerLeave={() => setSel(null)}
      >
        {monthLabels.map((m) => (
          <text key={m.x} x={m.x} y={9}>
            {m.text}
          </text>
        ))}
        {["Mon", "Wed", "Fri"].map((t, i) => (
          <text key={t} x={0} y={TOP + i * 2 * STEP + CELL - 2}>
            {t}
          </text>
        ))}
        {slots.map((d, i) =>
          d ? (
            <rect
              key={d.date}
              data-testid="heat-cell"
              data-level={intensity(d)}
              x={LEFT + Math.floor(i / 7) * STEP}
              y={TOP + (i % 7) * STEP}
              width={CELL}
              height={CELL}
              rx={2}
              fill={fills[intensity(d)] ?? fills[0]}
              onPointerEnter={() => setSel(d)}
              onClick={() => setSel(d)}
            />
          ) : null,
        )}
      </svg>
      <p className="readout" aria-live="polite">
        {sel ? dayDescription(sel) : "Tap a day for details."}
      </p>
      <Legend
        items={[
          { label: "No study", color: fills[0] },
          { label: "Light", color: fills[1] },
          { label: "Medium", color: fills[3] },
          { label: "Heavy", color: fills[4] },
        ]}
      />
      <Table
        caption="Study days"
        head={["Day", "Reviews", "Exercises", "Readings", "Min"]}
        rows={[...active]
          .reverse()
          .map((d) => [
            fmtDay(d.date, true),
            d.reviews,
            d.exercises,
            d.readings,
            Math.round(d.minutes),
          ])}
      />
    </figure>
  );
}

/* ---------- due forecast ---------- */

export function ForecastBars({ days }: { days: ForecastDay[] }) {
  const [sel, setSel] = useState<number | null>(null);
  if (days.length === 0) return null;
  const selDay = sel === null ? undefined : days[sel];
  const slot = 24;
  const w = days.length * slot;
  const H = 80;
  const max = Math.max(1, ...days.map((d) => d.due));
  const barW = 16;
  return (
    <figure className="chart">
      <figcaption>Reviews due, next {days.length} days</figcaption>
      <svg
        viewBox={`0 0 ${w} ${H + 16}`}
        role="group"
        aria-label="Forecast of reviews due per day"
        onPointerLeave={() => setSel(null)}
      >
        <line className="grid" x1={0} x2={w} y1={H} y2={H} />
        {days.map((d, i) => {
          const h = d.due === 0 ? 0 : Math.max(3, (d.due / max) * (H - 14));
          const x = i * slot + (slot - barW) / 2;
          const label = `${fmtDay(d.date, true)}${i === 0 ? " (incl. overdue)" : ""}: ${d.due} due`;
          return (
            <g
              key={d.date}
              tabIndex={0}
              role="img"
              aria-label={label}
              onFocus={() => setSel(i)}
              onPointerEnter={() => setSel(i)}
              onClick={() => setSel(i)}
            >
              <rect
                className="hit"
                x={i * slot}
                y={0}
                width={slot}
                height={H + 16}
              />
              {h > 0 && (
                <path
                  d={`M${x} ${H} V${H - h + 4} a4 4 0 0 1 4 -4 h${barW - 8} a4 4 0 0 1 4 4 V${H} Z`}
                  fill={sel === i ? "var(--viz-seq-4)" : "var(--viz-seq-3)"}
                />
              )}
              <text x={i * slot + slot / 2} y={H + 11} textAnchor="middle">
                {parseDay(d.date).toLocaleDateString(undefined, {
                  weekday: "narrow",
                  timeZone: "UTC",
                })}
              </text>
            </g>
          );
        })}
        <text x={0} y={8}>
          {max}
        </text>
      </svg>
      <p className="readout" aria-live="polite">
        {selDay
          ? `${fmtDay(selDay.date, true)}: ${selDay.due} due`
          : "Tap a bar for details. Day 1 includes overdue cards."}
      </p>
      <Table
        caption="Reviews due per day"
        head={["Day", "Due"]}
        rows={days.map((d) => [fmtDay(d.date, true), d.due])}
      />
    </figure>
  );
}

/* ---------- generic line chart (weekly accuracy, mastery trajectory) ---------- */

export interface LinePoint {
  x: number;
  y: number | null;
  marker?: boolean;
  note?: string;
}
export interface LineSeries {
  name: string;
  color: string;
  points: LinePoint[];
}

const LW = 300;
const LH = 130;
const PAD = { l: 26, r: 10, t: 8, b: 18 };

function LineChart({
  series,
  label,
  xLabels,
  describe,
  markerName,
}: {
  series: LineSeries[];
  label: string;
  xLabels: { x: number; text: string; anchor?: "start" | "end" | "middle" }[];
  describe: (s: LineSeries, p: LinePoint) => string;
  markerName?: string;
}) {
  const [sel, setSel] = useState<{ s: number; p: number } | null>(null);
  const xs = series.flatMap((s) => s.points.map((p) => p.x));
  if (xs.length === 0) return null;
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const sx = (x: number) =>
    x1 === x0
      ? PAD.l + (LW - PAD.l - PAD.r) / 2
      : PAD.l + ((x - x0) / (x1 - x0)) * (LW - PAD.l - PAD.r);
  const sy = (y: number) => PAD.t + (1 - y) * (LH - PAD.t - PAD.b);

  // Flat list of points for keyboard navigation and nearest-point lookup.
  const flat = series.flatMap((s, si) =>
    s.points.map((p, pi) => ({ si, pi, p })).filter((e) => e.p.y !== null),
  );
  flat.sort((a, b) => a.p.x - b.p.x || a.si - b.si);

  function nearest(clientX: number, el: SVGSVGElement) {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0) return;
    const vx = ((clientX - rect.left) / rect.width) * LW;
    let best: (typeof flat)[number] | null = null;
    for (const e of flat)
      if (!best || Math.abs(sx(e.p.x) - vx) < Math.abs(sx(best.p.x) - vx))
        best = e;
    if (best) setSel({ s: best.si, p: best.pi });
  }

  function onKey(e: KeyboardEvent<SVGSVGElement>) {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const cur = flat.findIndex((f) => sel && f.si === sel.s && f.pi === sel.p);
    const next =
      e.key === "ArrowRight"
        ? Math.min(flat.length - 1, cur + 1)
        : Math.max(0, cur < 0 ? flat.length - 1 : cur - 1);
    const f = flat[next];
    if (f) setSel({ s: f.si, p: f.pi });
  }

  const selSeries = sel ? series[sel.s] : undefined;
  const selPoint = sel ? selSeries?.points[sel.p] : undefined;
  return (
    <>
      <svg
        viewBox={`0 0 ${LW} ${LH}`}
        role="group"
        tabIndex={0}
        aria-label={`${label}. Use the left and right arrow keys to read values.`}
        onPointerMove={(e) => nearest(e.clientX, e.currentTarget)}
        onClick={(e) => nearest(e.clientX, e.currentTarget)}
        onPointerLeave={() => setSel(null)}
        onKeyDown={onKey}
      >
        {[0, 0.5, 1].map((t) => (
          <g key={t}>
            <line
              className="grid"
              x1={PAD.l}
              x2={LW - PAD.r}
              y1={sy(t)}
              y2={sy(t)}
            />
            <text x={PAD.l - 4} y={sy(t) + 3} textAnchor="end">
              {Math.round(t * 100)}
            </text>
          </g>
        ))}
        {xLabels.map((l) => (
          <text
            key={l.text + l.x}
            x={sx(l.x)}
            y={LH - 4}
            textAnchor={l.anchor ?? "middle"}
          >
            {l.text}
          </text>
        ))}
        {selPoint && (
          <line
            x1={sx(selPoint.x)}
            x2={sx(selPoint.x)}
            y1={PAD.t}
            y2={LH - PAD.b}
            stroke="var(--muted)"
            strokeWidth={1}
          />
        )}
        {series.map((s) => {
          // Break the line at missing values.
          const segs: LinePoint[][] = [[]];
          for (const p of s.points) {
            if (p.y === null) segs.push([]);
            else segs[segs.length - 1]?.push(p);
          }
          return (
            <g key={s.name}>
              {segs
                .filter((seg) => seg.length > 0)
                .map((seg, i) =>
                  seg.length === 1 ? null : (
                    <polyline
                      key={i}
                      fill="none"
                      stroke={s.color}
                      strokeWidth={2}
                      strokeLinejoin="round"
                      strokeLinecap="round"
                      points={seg
                        .map((p) => `${sx(p.x)},${sy(p.y ?? 0)}`)
                        .join(" ")}
                    />
                  ),
                )}
              {s.points.map((p, i) =>
                p.y === null ? null : p.marker ? (
                  <path
                    key={i}
                    data-testid="error-marker"
                    d={`M${sx(p.x)} ${sy(p.y) - 5} l5 5 l-5 5 l-5 -5 Z`}
                    fill="var(--viz-bad)"
                    stroke="var(--viz-surface)"
                    strokeWidth={2}
                  />
                ) : null,
              )}
              {(() => {
                // End dot: the last defined point.
                const last = [...s.points].reverse().find((p) => p.y !== null);
                return last && last.y !== null ? (
                  <circle
                    cx={sx(last.x)}
                    cy={sy(last.y)}
                    r={4}
                    fill={s.color}
                    stroke="var(--viz-surface)"
                    strokeWidth={2}
                  />
                ) : null;
              })()}
            </g>
          );
        })}
        {selSeries && selPoint && selPoint.y !== null && (
          <circle
            cx={sx(selPoint.x)}
            cy={sy(selPoint.y)}
            r={5}
            fill={selSeries.color}
            stroke="var(--viz-surface)"
            strokeWidth={2}
          />
        )}
      </svg>
      <p className="readout" aria-live="polite">
        {selSeries && selPoint
          ? describe(selSeries, selPoint)
          : "Hover, tap or use the arrow keys to read values."}
      </p>
      <Legend
        items={[
          ...series.map((s) => ({
            label: s.name,
            color: s.color,
            shape: "line" as const,
          })),
          ...(markerName
            ? [
                {
                  label: markerName,
                  color: "var(--viz-bad)",
                  shape: "diamond" as const,
                },
              ]
            : []),
        ]}
      />
    </>
  );
}

/* ---------- weekly accuracy ---------- */

export function AccuracyChart({ weeks }: { weeks: WeekAccuracy[] }) {
  const enough = weeks.some(
    (w) =>
      w.flashcards_correct_rate !== null || w.production_correct_rate !== null,
  );
  if (!enough)
    return (
      <figure className="chart">
        <figcaption>Weekly accuracy</figcaption>
        <p className="muted">Not enough answers yet.</p>
      </figure>
    );
  const t = (w: WeekAccuracy) => parseDay(w.week_start).getTime();
  const series: LineSeries[] = [
    {
      name: "Flashcards",
      color: "var(--viz-1)",
      points: weeks.map((w) => ({
        x: t(w),
        y: w.flashcards_correct_rate,
        note: `${w.flashcards_n} answers`,
      })),
    },
    {
      name: "Written exercises",
      color: "var(--viz-2)",
      points: weeks.map((w) => ({
        x: t(w),
        y: w.production_correct_rate,
        note: `${w.production_n} answers`,
      })),
    },
  ];
  const first = weeks[0];
  const last = weeks[weeks.length - 1];
  if (!first || !last) return null;
  return (
    <figure className="chart">
      <figcaption>Weekly accuracy (% correct)</figcaption>
      <LineChart
        series={series}
        label="Weekly accuracy, flashcards and written exercises"
        xLabels={[
          { x: t(first), text: fmtDay(first.week_start), anchor: "start" },
          { x: t(last), text: fmtDay(last.week_start), anchor: "end" },
        ]}
        describe={(s, p) =>
          `Week of ${fmtDay(new Date(p.x).toISOString().slice(0, 10))} · ${s.name}: ${pct(p.y)} (${p.note})`
        }
      />
      <Table
        caption="Weekly accuracy"
        head={["Week of", "Flashcards", "n", "Written", "n"]}
        rows={weeks.map((w) => [
          fmtDay(w.week_start),
          pct(w.flashcards_correct_rate),
          w.flashcards_n,
          pct(w.production_correct_rate),
          w.production_n,
        ])}
      />
    </figure>
  );
}

/* ---------- mastery trajectory of an item ---------- */

const FACET_COLORS = ["var(--viz-1)", "var(--viz-2)"];

export function TrajectoryChart({ facets }: { facets: FacetDetail[] }) {
  const withPoints = facets.filter((f) => f.trajectory.length > 0);
  if (withPoints.length === 0) return <p className="muted">No history yet.</p>;
  const series: LineSeries[] = withPoints.map((f, i) => ({
    name: f.facet,
    color: FACET_COLORS[i % FACET_COLORS.length] ?? "var(--viz-1)",
    points: f.trajectory.map((p) => ({
      x: new Date(p.ts).getTime(),
      y: p.mastery,
      marker: p.outcome === "error",
      note: p.outcome ?? p.kind,
    })),
  }));
  const times = series.flatMap((s) => s.points.map((p) => p.x));
  const lo = Math.min(...times);
  const hi = Math.max(...times);
  const d = (x: number) =>
    new Date(x).toLocaleDateString(undefined, {
      day: "numeric",
      month: "short",
    });
  return (
    <figure className="chart">
      <figcaption>Mastery over time (%)</figcaption>
      <LineChart
        series={series}
        label="Mastery over time"
        markerName="Error"
        xLabels={
          hi === lo
            ? [{ x: lo, text: d(lo) }]
            : [
                { x: lo, text: d(lo), anchor: "start" },
                { x: hi, text: d(hi), anchor: "end" },
              ]
        }
        describe={(s, p) =>
          `${new Date(p.x).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })} · ${s.name}: ${pct(p.y)} (${p.note})`
        }
      />
      <Table
        caption="Mastery after each event"
        head={["When", "Facet", "Mastery", "Event"]}
        rows={withPoints.flatMap((f) =>
          f.trajectory.slice(-30).map((p) => [
            new Date(p.ts).toLocaleDateString(undefined, {
              day: "numeric",
              month: "short",
            }),
            f.facet,
            pct(p.mastery),
            p.outcome ?? p.kind,
          ]),
        )}
      />
    </figure>
  );
}

/* ---------- stacked bars: composition by memory state ---------- */

type StateKey = keyof typeof STATE_LABELS;
const STATE_ORDER: StateKey[] = [
  "mature",
  "young",
  "learning",
  "presumed_known",
  "new",
];
const STATE_COLORS: Record<StateKey, string> = {
  mature: "var(--viz-mature)",
  young: "var(--viz-young)",
  learning: "var(--viz-learning)",
  presumed_known: "var(--viz-known)",
  new: "var(--viz-track)",
};

export interface StackRow {
  label: string;
  counts: Record<StateKey, number>;
}

export function StackedBars({
  rows,
  title,
  showLegend = true,
}: {
  rows: StackRow[];
  title: string;
  showLegend?: boolean;
}) {
  const labelW = 34;
  const totalW = 40;
  const W = 300;
  const barW = W - labelW - totalW;
  const rowH = 22;
  const shown = rows.filter((r) => STATE_ORDER.some((k) => r.counts[k] > 0));
  if (shown.length === 0) return null;
  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <svg
        viewBox={`0 0 ${W} ${shown.length * rowH}`}
        role="img"
        aria-label={`${title}: ${shown
          .map(
            (r) =>
              `${r.label} ` +
              STATE_ORDER.filter((k) => r.counts[k] > 0)
                .map((k) => `${r.counts[k]} ${STATE_LABELS[k].toLowerCase()}`)
                .join(", "),
          )
          .join("; ")}`}
      >
        {shown.map((r, i) => {
          const total = STATE_ORDER.reduce((s, k) => s + r.counts[k], 0);
          let x = labelW;
          const y = i * rowH + 4;
          return (
            <g key={r.label}>
              <text x={0} y={y + 10}>
                {r.label}
              </text>
              {STATE_ORDER.map((k) => {
                const n = r.counts[k];
                if (n === 0) return null;
                const w = (n / total) * barW;
                const seg = (
                  <rect
                    key={k}
                    x={x}
                    y={y}
                    width={Math.max(0, w - 2)}
                    height={14}
                    rx={2}
                    fill={STATE_COLORS[k]}
                  >
                    <title>{`${r.label}: ${n} ${STATE_LABELS[k].toLowerCase()}`}</title>
                  </rect>
                );
                x += w;
                return seg;
              })}
              <text x={W} y={y + 10} textAnchor="end">
                {total}
              </text>
            </g>
          );
        })}
      </svg>
      {showLegend && (
        <Legend
          items={STATE_ORDER.map((k) => ({
            label: STATE_LABELS[k],
            color: STATE_COLORS[k],
          }))}
        />
      )}
      <Table
        caption={title}
        head={["", ...STATE_ORDER.map((k) => STATE_LABELS[k])]}
        rows={shown.map((r) => [
          r.label,
          ...STATE_ORDER.map((k) => r.counts[k]),
        ])}
      />
    </figure>
  );
}
