import { Link } from "react-router";
import { useEffect, useState, type FormEvent } from "react";
import {
  getHealth,
  updateLearner,
  updateSettings,
  type Level,
  type Settings,
} from "../api/client";
import { useAuth } from "../authContext";
import { useLearner } from "../learnerContext";
import StatsSection from "./StatsSection";
import type { Schemas } from "../api/client";

type HealthResponse = Schemas["HealthResponse"];

type HealthState =
  | { kind: "loading" }
  | { kind: "ok"; health: HealthResponse }
  | { kind: "error"; message: string };

const LEVELS: Level[] = ["A1", "A2", "B1", "B2", "C1", "C2"];

interface Field {
  key: keyof Settings;
  label: string;
  min: number;
  max: number;
  integer: boolean;
  step: string;
}

// Bounds mirror backend/app/api/settings.py (SettingsUpdate).
const FIELDS: Field[] = [
  {
    key: "weekly_new_lemmas",
    label: "New words per week",
    min: 0,
    max: 500,
    integer: true,
    step: "1",
  },
  {
    key: "weekly_new_grammar",
    label: "New grammar points per week",
    min: 0,
    max: 50,
    integer: true,
    step: "1",
  },
  {
    key: "new_per_session",
    label: "New words per session",
    min: 0,
    max: 20,
    integer: true,
    step: "1",
  },
  {
    key: "production_slots",
    label: "Written exercises per session",
    min: 0,
    max: 5,
    integer: true,
    step: "1",
  },
  {
    key: "review_cap",
    label: "Reviews per session (cap)",
    min: 1,
    max: 500,
    integer: true,
    step: "1",
  },
  {
    key: "desired_retention",
    label: "Desired retention",
    min: 0.7,
    max: 0.97,
    integer: false,
    step: "0.01",
  },
];

function validate(f: Field, raw: string): string | null {
  const text = raw.trim();
  if (text === "" || Number.isNaN(Number(text))) return "Enter a number";
  const n = Number(text);
  if (f.integer && !Number.isInteger(n)) return "Must be a whole number";
  if (n < f.min || n > f.max) return `Must be between ${f.min} and ${f.max}`;
  return null;
}

export default function SettingsView() {
  const { learner, setLearner } = useLearner();
  const auth = useAuth();
  const [level, setLevel] = useState<Level>(learner.level);
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      FIELDS.map((f) => [f.key, String(learner.settings[f.key])]),
    ),
  );
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    getHealth()
      .then((h) => {
        if (!cancelled) setHealth({ kind: "ok", health: h });
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setHealth({
            kind: "error",
            message: err instanceof Error ? err.message : String(err),
          });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function save(e: FormEvent) {
    e.preventDefault();
    setSaved(false);
    setSaveError(null);
    const found: Record<string, string> = {};
    for (const f of FIELDS) {
      const msg = validate(f, values[f.key] ?? "");
      if (msg) found[f.key] = msg;
    }
    setErrors(found);
    if (Object.keys(found).length > 0) return;

    setSaving(true);
    try {
      const body = Object.fromEntries(
        FIELDS.map((f) => [f.key, Number(values[f.key])]),
      );
      let next = learner;
      if (level !== learner.level) next = await updateLearner({ level });
      const settings = await updateSettings(body);
      setLearner({ ...next, settings });
      setSaved(true);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section>
      <h1>Settings</h1>
      <form className="form" onSubmit={save} noValidate>
        <label className="field">
          <span>Level</span>
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value as Level)}
          >
            {LEVELS.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </label>
        {FIELDS.map((f) => (
          <label key={f.key} className="field">
            <span>{f.label}</span>
            <input
              type="number"
              inputMode={f.integer ? "numeric" : "decimal"}
              step={f.step}
              min={f.min}
              max={f.max}
              value={values[f.key] ?? ""}
              aria-invalid={errors[f.key] ? true : undefined}
              onChange={(e) =>
                setValues((v) => ({ ...v, [f.key]: e.target.value }))
              }
            />
            {errors[f.key] && (
              <span className="error" role="alert">
                {errors[f.key]}
              </span>
            )}
          </label>
        ))}
        {saveError && <p role="alert">{saveError}</p>}
        {saved && <p role="status">Settings saved.</p>}
        <button type="submit" className="btn primary" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
      </form>
      <h2>Placement test</h2>
      <p>A short test (≤5 min) to re-estimate your level.</p>
      <Link to="/placement" className="btn">
        Re-run placement test
      </Link>
      <StatsSection />
      {auth.enabled && (
        <>
          <h2>Access</h2>
          <button type="button" className="btn" onClick={auth.logout}>
            Log out
          </button>
        </>
      )}
      <p className="status" role="status">
        {health.kind === "loading" && "Backend: checking…"}
        {health.kind === "ok" &&
          `Backend: ${health.health.status} (v${health.health.version}), database: ${health.health.database}`}
        {health.kind === "error" && `Backend: unreachable (${health.message})`}
      </p>
    </section>
  );
}
