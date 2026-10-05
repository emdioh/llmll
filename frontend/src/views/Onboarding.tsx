import { useState, type FormEvent } from "react";
import { createLearner, type Learner, type Level } from "../api/client";

const LEVELS: Level[] = ["A1", "A2", "B1", "B2", "C1"];
const LANGUAGES = [
  { code: "it", label: "Italian" },
  { code: "en", label: "English" },
  { code: "fr", label: "French" },
  { code: "es", label: "Spanish" },
];

export default function Onboarding({
  onDone,
}: {
  onDone: (learner: Learner) => void;
}) {
  const [level, setLevel] = useState<Level>("A1");
  const [known, setKnown] = useState<string[]>(["it", "en"]);
  const [explanation, setExplanation] = useState("it");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggle(code: string, checked: boolean) {
    setKnown((prev) =>
      checked ? [...prev, code] : prev.filter((c) => c !== code),
    );
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const learner = await createLearner({
        level,
        known_languages: known,
        explanation_language: explanation,
      });
      onDone(learner);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <section>
      <h1>Welcome</h1>
      <p>Tell us a little about yourself to set up your German course.</p>
      <form className="form" onSubmit={submit}>
        <label className="field">
          <span>Your German level</span>
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
        <fieldset className="field">
          <legend>Languages you know</legend>
          {LANGUAGES.map((l) => (
            <label key={l.code} className="check">
              <input
                type="checkbox"
                checked={known.includes(l.code)}
                onChange={(e) => toggle(l.code, e.target.checked)}
              />
              <span>{l.label}</span>
            </label>
          ))}
        </fieldset>
        <label className="field">
          <span>Explanation language</span>
          <select
            value={explanation}
            onChange={(e) => setExplanation(e.target.value)}
          >
            {LANGUAGES.filter((l) => l.code === "it" || l.code === "en").map(
              (l) => (
                <option key={l.code} value={l.code}>
                  {l.label}
                </option>
              ),
            )}
          </select>
        </label>
        {error && <p role="alert">{error}</p>}
        <button type="submit" className="btn primary" disabled={busy}>
          {busy ? "Saving…" : "Start learning"}
        </button>
      </form>
    </section>
  );
}
