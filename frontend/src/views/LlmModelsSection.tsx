import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  getLlmSettings,
  updateLlmSettings,
  type LlmOverride,
  type LlmSettings,
  type LlmTask,
} from "../api/client";

type Output = "native" | "json";
interface Draft {
  provider: string;
  model: string;
  output: Output;
}

const LABELS: Record<string, string> = {
  grade_sentence: "Grading",
  explain: "Explanations",
  generate_exercise: "Exercise generation",
  simplify_text: "Text simplification",
  gloss: "Glosses",
};
const MAIN_TASKS = ["grade_sentence", "explain"];

/** What a row shows when nothing is edited: the override, else the .env route. */
function initialDraft(t: LlmTask): Draft {
  return t.override
    ? {
        provider: t.override.provider,
        model: t.override.model,
        output: (t.override.structured_output as Output | null) ?? "native",
      }
    : {
        provider: t.config_provider ?? t.provider,
        model: "",
        output: (t.structured_output as Output) ?? "native",
      };
}

const same = (a: Draft, b: Draft) =>
  a.provider === b.provider && a.model === b.model && a.output === b.output;

/** Provider and model per LLM task. Keys never reach the browser (backend/app/api/settings.py). */
export default function LlmModelsSection() {
  const [data, setData] = useState<LlmSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const apply = useCallback((next: LlmSettings, only?: string) => {
    setData(next);
    setDrafts((old) => {
      const fresh = Object.fromEntries(
        next.tasks.map((t) => [t.task, initialDraft(t)]),
      );
      const one = only ? fresh[only] : undefined;
      return only && one ? { ...old, [only]: one } : fresh;
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    getLlmSettings()
      .then((s) => {
        if (!cancelled) apply(s);
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setLoadError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [apply]);

  if (loadError)
    return (
      <LlmShell>
        <p role="alert">LLM models unavailable: {loadError}</p>
      </LlmShell>
    );
  if (!data)
    return (
      <LlmShell>
        <p role="status">Loading LLM models…</p>
      </LlmShell>
    );
  if (data.fake)
    return (
      <LlmShell>
        <p role="status">
          No API key is configured, so the simulated LLM is in use. Add a key to{" "}
          <code>.env</code> to choose models here.
        </p>
      </LlmShell>
    );

  const tasks = data.tasks;
  const edit = (task: string, patch: Partial<Draft>) =>
    setDrafts((d) => ({ ...d, [task]: { ...d[task]!, ...patch } }));

  async function send(body: Record<string, LlmOverride | null>, only?: string) {
    setBusy(true);
    setSaved(false);
    setError(null);
    try {
      apply(await updateLlmSettings({ tasks: body }), only);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    const body: Record<string, LlmOverride | null> = {};
    for (const t of tasks) {
      const draft = drafts[t.task];
      const base = initialDraft(t);
      if (!draft || same(draft, base)) continue;
      const model = draft.model.trim();
      if (model === "") {
        if (draft.provider !== base.provider) {
          setError(`Enter a model for ${LABELS[t.task] ?? t.task}.`);
          return;
        }
        if (t.override) body[t.task] = null;
        continue;
      }
      body[t.task] = {
        provider: draft.provider as LlmOverride["provider"],
        model,
        ...(draft.provider === "anthropic"
          ? {}
          : { structured_output: draft.output }),
      };
    }
    if (Object.keys(body).length === 0) {
      setError(null);
      setSaved(true);
      return;
    }
    if (await send(body)) setSaved(true);
  }

  const row = (t: LlmTask) => {
    const draft = drafts[t.task] ?? initialDraft(t);
    const label = LABELS[t.task] ?? t.task;
    const placeholder =
      draft.provider === t.config_provider && t.config_model
        ? t.config_model
        : "model id";
    return (
      <fieldset key={t.task} className="llm-row">
        <legend>{label}</legend>
        <label className="field">
          <span>Provider</span>
          <select
            aria-label={`${label} provider`}
            value={draft.provider}
            onChange={(e) =>
              edit(t.task, { provider: e.target.value, model: "" })
            }
          >
            {[...new Set([...data.available_providers, draft.provider])].map(
              (p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ),
            )}
          </select>
        </label>
        <label className="field">
          <span>Model</span>
          <input
            type="text"
            aria-label={`${label} model`}
            value={draft.model}
            placeholder={placeholder}
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => edit(t.task, { model: e.target.value })}
          />
        </label>
        {draft.provider !== "anthropic" && (
          <label className="field">
            <span>Output</span>
            <select
              aria-label={`${label} output`}
              value={draft.output}
              onChange={(e) =>
                edit(t.task, { output: e.target.value as Output })
              }
            >
              <option value="native">native</option>
              <option value="json">json</option>
            </select>
          </label>
        )}
        {t.override && (
          <>
            <span className="muted">
              from .env: {t.config_provider ?? "?"}/{t.config_model ?? "?"}
            </span>{" "}
            <button
              type="button"
              className="btn"
              disabled={busy}
              aria-label={`Reset ${label}`}
              onClick={() => void send({ [t.task]: null }, t.task)}
            >
              Reset
            </button>
          </>
        )}
      </fieldset>
    );
  };

  return (
    <LlmShell>
      {data.override_error && (
        <p role="alert">
          The saved model choices could not be applied, the .env configuration
          is in use: {data.override_error}
        </p>
      )}
      {!data.live_switch && (
        <p className="muted">
          Changes take effect after restarting the server.
        </p>
      )}
      <form className="form" onSubmit={save} noValidate>
        {tasks.filter((t) => MAIN_TASKS.includes(t.task)).map(row)}
        <details>
          <summary>Other tasks</summary>
          {tasks.filter((t) => !MAIN_TASKS.includes(t.task)).map(row)}
        </details>
        {error && <p role="alert">{error}</p>}
        {saved && <p role="status">LLM models saved.</p>}
        <button type="submit" className="btn primary" disabled={busy}>
          {busy ? "Saving…" : "Save models"}
        </button>
      </form>
    </LlmShell>
  );
}

function LlmShell({ children }: { children: React.ReactNode }) {
  return (
    <section aria-labelledby="llm-heading">
      <h2 id="llm-heading">LLM models</h2>
      <p className="muted">
        Choose the provider and model per task. API keys stay in{" "}
        <code>.env</code>.
      </p>
      {children}
    </section>
  );
}
