import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router";
import {
  ApiError,
  finishReading,
  getText,
  glossToken,
  optinToken,
  startReading,
  submitAnswer,
  type FinishResult,
  type Gloss,
  type ProductionAnswer,
  type TextOut,
  type TextToken,
} from "../api/client";
import ProductionExerciseCard from "./cards/ProductionExerciseCard";
import ProductionFeedback from "./cards/ProductionFeedback";

const UNDERLINE_KEY = "llmll.reader.underline";
const UNDERLINED = new Set(["auto_candidate", "optin", "optin_unlisted"]);

function loadUnderline(): boolean {
  try {
    return localStorage.getItem(UNDERLINE_KEY) === "1";
  } catch {
    return false;
  }
}

function saveUnderline(on: boolean) {
  try {
    localStorage.setItem(UNDERLINE_KEY, on ? "1" : "0");
  } catch {
    // storage unavailable: the option just is not remembered
  }
}

function message(err: unknown): string {
  if (err instanceof ApiError && (err.status === 503 || err.status === 502))
    return "The service is unavailable right now. Please try again.";
  return err instanceof Error ? err.message : String(err);
}

interface Paragraph {
  start: number;
  end: number;
}

function paragraphs(body: string): Paragraph[] {
  const out: Paragraph[] = [];
  const re = /\n\s*\n/g;
  let pos = 0;
  for (const m of body.matchAll(re)) {
    if (m.index > pos) out.push({ start: pos, end: m.index });
    pos = m.index + m[0].length;
  }
  if (pos < body.length) out.push({ start: pos, end: body.length });
  return out;
}

function renderParagraph(
  body: string,
  p: Paragraph,
  tokens: TextToken[],
  underline: boolean,
  selected: number | null,
  onTap: (t: TextToken) => void,
): ReactNode[] {
  const nodes: ReactNode[] = [];
  let pos = p.start;
  for (const t of tokens) {
    if (t.start < pos || t.end > p.end || !t.is_alpha) continue;
    if (t.start > pos) nodes.push(body.slice(pos, t.start));
    const cls = [
      "tok",
      underline && UNDERLINED.has(t.word_class) ? "new" : "",
      selected === t.i ? "selected" : "",
    ]
      .filter(Boolean)
      .join(" ");
    nodes.push(
      <button
        key={t.i}
        type="button"
        className={cls}
        data-class={t.word_class}
        onClick={() => onTap(t)}
      >
        {body.slice(t.start, t.end)}
      </button>,
    );
    pos = t.end;
  }
  if (pos < p.end) nodes.push(body.slice(pos, p.end));
  return nodes;
}

type Sheet =
  | { kind: "loading"; token: TextToken }
  | { kind: "error"; token: TextToken; error: string }
  | { kind: "ok"; token: TextToken; gloss: Gloss };

type Stage =
  | { kind: "reading" }
  | { kind: "finishing" }
  | { kind: "exercise"; result: FinishResult }
  | {
      kind: "graded";
      result: FinishResult;
      answer: string;
      grade: ProductionAnswer;
    };

interface Loaded {
  text: TextOut;
  readingId: number;
}

export default function ReaderView() {
  const params = useParams();
  const textId = Number(params.textId);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [underline, setUnderline] = useState(loadUnderline);
  const [showOriginal, setShowOriginal] = useState(false);
  const [sheet, setSheet] = useState<Sheet | null>(null);
  const [optedIn, setOptedIn] = useState<ReadonlySet<string>>(new Set());
  const [optinBusy, setOptinBusy] = useState(false);
  const [optinError, setOptinError] = useState<string | null>(null);
  const [stage, setStage] = useState<Stage>({ kind: "reading" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const startedAt = useRef(0);
  // One reading session per mounted text, even when effects run twice (StrictMode).
  const starting = useRef<{ id: number; p: Promise<Loaded> } | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (starting.current?.id !== textId) {
      starting.current = {
        id: textId,
        p: (async () => {
          const text = await getText(textId);
          const reading = await startReading(textId);
          return { text, readingId: reading.id };
        })(),
      };
    }
    starting.current.p.then(
      (l) => {
        if (!cancelled) setLoaded(l);
      },
      (err: unknown) => {
        if (!cancelled) setLoadError(message(err));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [textId]);

  function toggleUnderline(on: boolean) {
    setUnderline(on);
    saveUnderline(on);
  }

  async function tap(token: TextToken) {
    if (!loaded) return;
    setSheet({ kind: "loading", token });
    setOptinError(null);
    try {
      const gloss = await glossToken(loaded.readingId, token.i);
      setSheet((prev) =>
        prev?.token.i === token.i ? { kind: "ok", token, gloss } : prev,
      );
    } catch (err) {
      setSheet((prev) =>
        prev?.token.i === token.i
          ? { kind: "error", token, error: message(err) }
          : prev,
      );
    }
  }

  async function optin(token: TextToken, gloss: Gloss) {
    if (!loaded || optinBusy) return;
    setOptinBusy(true);
    setOptinError(null);
    try {
      await optinToken(loaded.readingId, token.i);
      setOptedIn((prev) => new Set(prev).add(gloss.lemma));
    } catch (err) {
      setOptinError(message(err));
    } finally {
      setOptinBusy(false);
    }
  }

  async function finish() {
    if (!loaded || busy) return;
    setBusy(true);
    setError(null);
    setSheet(null);
    try {
      const result = await finishReading(loaded.readingId);
      startedAt.current = performance.now();
      setStage({ kind: "exercise", result });
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  async function answer(result: FinishResult, text: string) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const out = await submitAnswer(result.session_id, {
        exercise_id: result.exercise.exercise_id,
        answer: { text },
        used_hint: false,
        duration_ms: Math.round(performance.now() - startedAt.current),
      });
      if (out.kind === "production")
        setStage({ kind: "graded", result, answer: text, grade: out });
    } catch (err) {
      setError(
        err instanceof ApiError && (err.status === 503 || err.status === 502)
          ? "Grading is unavailable right now. Your answer is kept: try again."
          : message(err),
      );
    } finally {
      setBusy(false);
    }
  }

  if (loadError)
    return (
      <section>
        <p role="alert">{loadError}</p>
        <Link to="/reading">Back to texts</Link>
      </section>
    );
  if (!loaded)
    return (
      <section>
        <p role="status">
          <span className="spinner" aria-hidden="true" />
          Loading…
        </p>
      </section>
    );

  const { text } = loaded;
  const version = text.version;

  if (stage.kind === "exercise" || stage.kind === "graded") {
    const { result } = stage;
    return (
      <section>
        <h1>Well done</h1>
        <p role="status">
          {result.implicit_events} words reviewed, {result.candidates.length}{" "}
          new words added
        </p>
        <div className="card">
          {stage.kind === "exercise" ? (
            <ProductionExerciseCard
              card={result.exercise}
              busy={busy}
              error={error}
              onSubmit={(t) => void answer(result, t)}
            />
          ) : (
            <>
              <ProductionFeedback answer={stage.answer} result={stage.grade} />
              <Link to="/reading" className="btn primary">
                Back to texts
              </Link>
            </>
          )}
        </div>
      </section>
    );
  }

  const paras = paragraphs(version.body);
  const gloss = sheet?.kind === "ok" ? sheet.gloss : null;

  return (
    <section className={`reader ${sheet ? "sheet-open" : ""}`}>
      <p>
        <Link to="/reading">← Texts</Link>
      </p>
      <h1 lang="de">{version.title}</h1>
      <p className="muted">{Math.round(version.coverage * 100)}% known words</p>
      <div className="row reader-options">
        <label className="check">
          <input
            type="checkbox"
            checked={underline}
            onChange={(e) => toggleUnderline(e.target.checked)}
          />
          Underline new words
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={showOriginal}
            onChange={(e) => setShowOriginal(e.target.checked)}
          />
          Show original
        </label>
      </div>
      {showOriginal ? (
        <div className="reader-text original" aria-label="Original text">
          {text.original ? (
            text.original.split(/\n\s*\n/).map((p, i) => <p key={i}>{p}</p>)
          ) : (
            <p className="muted">This text was generated: no original.</p>
          )}
        </div>
      ) : (
        <div className="reader-text" lang="de">
          {paras.map((p) => (
            <p key={p.start}>
              {renderParagraph(
                version.body,
                p,
                version.tokens,
                underline,
                sheet?.token.i ?? null,
                (t) => void tap(t),
              )}
            </p>
          ))}
        </div>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="row">
        <button
          type="button"
          className="btn primary"
          onClick={() => void finish()}
          disabled={busy}
        >
          {busy ? "Finishing…" : "Finish"}
        </button>
      </div>
      {sheet && (
        <div className="sheet" role="dialog" aria-label="Word gloss">
          <div className="row sheet-head">
            <strong lang="de" className="sheet-word">
              {sheet.token.lemma}
            </strong>
            <button
              type="button"
              className="btn"
              aria-label="Close"
              onClick={() => setSheet(null)}
            >
              ×
            </button>
          </div>
          {sheet.kind === "loading" && (
            <p role="status">
              <span className="spinner" aria-hidden="true" />
              Looking up…
            </p>
          )}
          {sheet.kind === "error" && <p role="alert">{sheet.error}</p>}
          {gloss && (
            <>
              <p className="sheet-translation">{gloss.translation}</p>
              <p className="muted" lang="de">
                {[
                  gloss.gender ? `${gloss.gender}` : null,
                  gloss.lemma,
                  gloss.plural ? `pl. ${gloss.plural}` : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
              {gloss.note && <p className="note">{gloss.note}</p>}
              {optinError && <p role="alert">{optinError}</p>}
              {gloss.can_optin && (
                <button
                  type="button"
                  className="btn primary"
                  disabled={optinBusy || optedIn.has(gloss.lemma)}
                  onClick={() => void optin(sheet.token, gloss)}
                >
                  Aggiungi alle parole da imparare
                </button>
              )}
              {optedIn.has(gloss.lemma) && (
                <p role="status">Aggiunta alle parole da imparare.</p>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
