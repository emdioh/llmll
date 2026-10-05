import { useCallback } from "react";
import { getStats } from "../api/client";
import { useApi } from "../useApi";

const pct = (v: number | null) => (v == null ? "–" : `${Math.round(v * 100)}%`);

/** Scheduler health: activity numbers and a plain calibration table (no chart library). */
export default function StatsSection() {
  const load = useCallback(() => getStats(), []);
  const state = useApi("stats", load);

  return (
    <section aria-labelledby="stats-heading">
      <h2 id="stats-heading">Stats</h2>
      {state.status === "loading" && <p role="status">Loading stats…</p>}
      {state.status === "error" && (
        <p role="alert">Stats unavailable: {state.error.message}</p>
      )}
      {state.status === "ok" && (
        <>
          <dl className="stats-grid">
            {[
              ["Reviews, 7 days", state.data.reviews_7d],
              ["Reviews, 30 days", state.data.reviews_30d],
              ["Sessions, 7 days", state.data.sessions_7d],
              ["Readings, 7 days", state.data.readings_7d],
              ["New items, 7 days", state.data.new_items_7d],
              ["Backlog (due)", state.data.backlog],
              ["Observed retention", pct(state.data.observed_retention)],
              ["Target retention", pct(state.data.target_retention)],
            ].map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
          <h3>Calibration</h3>
          {state.data.calibration.length === 0 ? (
            <p>Not enough reviews yet.</p>
          ) : (
            <div className="table-wrap">
              <table className="calibration">
                <thead>
                  <tr>
                    <th scope="col">Bucket</th>
                    <th scope="col">Predicted</th>
                    <th scope="col">Observed</th>
                    <th scope="col">Reviews</th>
                  </tr>
                </thead>
                <tbody>
                  {state.data.calibration.map((b) => (
                    <tr key={b.bucket_low}>
                      <td>
                        {pct(b.bucket_low)}–{pct(b.bucket_high)}
                      </td>
                      <td>{pct(b.predicted)}</td>
                      <td>{pct(b.observed)}</td>
                      <td>{b.n}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}
