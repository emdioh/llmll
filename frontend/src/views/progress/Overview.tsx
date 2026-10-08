import {
  getProgressActivity,
  getProgressForecast,
  getProgressLevels,
  getProgressSummary,
  type ProgressSummary,
} from "../../api/client";
import { useApi } from "../../useApi";
import { minutesLabel, pct } from "./util";
import {
  AccuracyChart,
  ForecastBars,
  Heatmap,
  StackedBars,
  type StackRow,
} from "./charts";
import { levelRows } from "./stack";

function Loading({ what }: { what: string }) {
  return (
    <p role="status" className="muted">
      Loading {what}…
    </p>
  );
}

function Failed({ what, error }: { what: string; error: Error }) {
  return (
    <p role="alert">
      Could not load {what}: {error.message}
    </p>
  );
}

function delta(a: number, b: number): string {
  const d = a - b;
  return d === 0
    ? "="
    : d > 0
      ? `+${Math.round(d * 10) / 10}`
      : `${Math.round(d * 10) / 10}`;
}

type Period = ProgressSummary["this_week"];

function WeekTable({ a, b }: { a: Period; b: Period }) {
  const rows: [string, number, number, (n: number) => string][] = [
    ["Study days", a.study_days, b.study_days, String],
    ["Reviews", a.reviews, b.reviews, String],
    ["Exercises", a.exercises, b.exercises, String],
    ["Readings", a.readings, b.readings, String],
    ["Time", a.minutes, b.minutes, (n) => minutesLabel(n)],
  ];
  return (
    <table className="compare">
      <caption className="chart-title" style={{ textAlign: "left" }}>
        Last 7 days vs the 7 before
      </caption>
      <thead>
        <tr>
          <th scope="col">
            <span className="sr-only">Measure</span>
          </th>
          <th scope="col">This week</th>
          <th scope="col">Last week</th>
          <th scope="col">Change</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, x, y, f]) => (
          <tr key={label}>
            <th scope="row">{label}</th>
            <td>{f(x)}</td>
            <td>{f(y)}</td>
            <td>{delta(x, y)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SummarySection() {
  const s = useApi("progress-summary", getProgressSummary);
  const f = useApi("progress-forecast", () => getProgressForecast(14));
  if (s.status === "loading") return <Loading what="summary" />;
  if (s.status === "error")
    return <Failed what="the summary" error={s.error} />;
  const d = s.data;
  const retentionOk =
    d.retention.observed !== null && d.retention.observed >= d.retention.target;
  const states = (c: typeof d.states): StackRow["counts"] => ({
    mature: c.mature,
    young: c.young,
    learning: c.learning,
    presumed_known: c.presumed_known,
    new: c.new,
  });
  return (
    <>
      <div className="hero">
        <span className="hero-number" aria-label="Current streak in days">
          {d.streak.current}
        </span>
        <span>
          <strong>day streak</strong>
          <br />
          <span className="muted">
            Longest {d.streak.longest} ·{" "}
            {d.streak.studied_today ? "studied today" : "not studied yet today"}
          </span>
        </span>
      </div>
      <WeekTable a={d.this_week} b={d.last_week} />
      <dl className="stat-grid">
        <div className="stat">
          <dt>Retention (observed / target)</dt>
          <dd>
            {pct(d.retention.observed)}{" "}
            <small>
              / {pct(d.retention.target)}
              {d.retention.observed !== null &&
                (retentionOk ? " · on target" : " · below target")}
            </small>
          </dd>
          <dd>
            <small>{d.retention.n_reviews} reviews measured</small>
          </dd>
        </div>
        <div className="stat">
          <dt>Due now / today</dt>
          <dd>
            {d.due_now} <small>/ {d.due_today}</small>
          </dd>
        </div>
        <div className="stat">
          <dt>All time</dt>
          <dd>
            {d.study_days_total} <small>study days</small>
          </dd>
          <dd>
            <small>
              {d.totals.reviews} reviews · {d.totals.exercises} exercises ·{" "}
              {d.totals.readings} readings
            </small>
          </dd>
        </div>
        <div className="stat">
          <dt>Introduced / time</dt>
          <dd>
            {d.totals.items_introduced} <small>items</small>
          </dd>
          <dd>
            <small>{minutesLabel(d.totals.minutes)} studied</small>
          </dd>
        </div>
      </dl>
      <StackedBars
        title="Memory state"
        rows={[
          { label: "Words", counts: states(d.states_words) },
          { label: "Gram.", counts: states(d.states_grammar) },
        ]}
      />
      {f.status === "ok" && <ForecastBars days={f.data} />}
      {f.status === "error" && <Failed what="the forecast" error={f.error} />}
    </>
  );
}

function ActivitySection() {
  const a = useApi("progress-activity", () => getProgressActivity(140));
  if (a.status === "loading") return <Loading what="activity" />;
  if (a.status === "error") return <Failed what="activity" error={a.error} />;
  return (
    <>
      <Heatmap days={a.data.days} />
      <AccuracyChart weeks={a.data.weeks} />
    </>
  );
}

function LevelsSection() {
  const l = useApi("progress-levels", getProgressLevels);
  if (l.status === "loading") return <Loading what="levels" />;
  if (l.status === "error") return <Failed what="levels" error={l.error} />;
  return (
    <>
      <h2>Levels</h2>
      <StackedBars
        title="Words by CEFR level"
        rows={levelRows(l.data, ["lemma"])}
      />
      <StackedBars
        title="Grammar by CEFR level"
        rows={levelRows(l.data, ["grammar", "construction"])}
        showLegend={false}
      />
    </>
  );
}

export default function Overview() {
  return (
    <div className="overview">
      <SummarySection />
      <ActivitySection />
      <LevelsSection />
    </div>
  );
}
