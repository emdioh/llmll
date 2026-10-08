import { useCallback, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router";
import { explainGrammar, getGrammar, type Explanation } from "../api/client";
import { useApi } from "../useApi";
import { ExplanationExamples } from "./cards/ExplanationPanel";
import Markdown from "./Markdown";

function AskBox({ id }: { id: string }) {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [answer, setAnswer] = useState<Explanation | null>(null);

  async function ask(e: FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q || busy) return;
    setBusy(true);
    setError(null);
    try {
      setAnswer(await explainGrammar(id, q));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="form" onSubmit={ask}>
      <label className="field">
        <span>Fai una domanda</span>
        <textarea
          className="text-input multiline"
          rows={3}
          maxLength={1000}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
      </label>
      <button
        type="submit"
        className="btn primary"
        disabled={busy || !question.trim()}
      >
        {busy ? "Asking…" : "Ask"}
      </button>
      {error && <p role="alert">{error}</p>}
      {answer && (
        <div className="explanation">
          <Markdown>{answer.markdown}</Markdown>
          <ExplanationExamples examples={answer.examples} />
        </div>
      )}
    </form>
  );
}

export default function GrammarDetailView() {
  const { id = "" } = useParams();
  const load = useCallback(() => getGrammar(id), [id]);
  const state = useApi(id, load);

  return (
    <section>
      <p>
        <Link to="/grammar">← Grammar</Link>
      </p>
      {state.status === "loading" && <p role="status">Loading…</p>}
      {state.status === "error" && (
        <p role="alert">Could not load the entry: {state.error.message}</p>
      )}
      {state.status === "ok" && (
        <>
          <h1>{state.data.title_it}</h1>
          <p className="muted">
            {state.data.title_en} · {state.data.level}
          </p>
          {/* The reference text already contains its examples section. */}
          <p>
            <Link to={`/progress/items/${encodeURIComponent(id)}`}>
              Your progress on this point
            </Link>
          </p>
          <Markdown>{state.data.reference_it}</Markdown>
          <AskBox id={id} />
        </>
      )}
    </section>
  );
}
