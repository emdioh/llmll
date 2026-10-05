import { useCallback, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import {
  ApiError,
  createText,
  generateText,
  listTexts,
  type TextOut,
} from "../api/client";
import { useApi } from "../useApi";

type Tab = "url" | "paste";

function failureMessage(err: unknown): string {
  if (err instanceof ApiError && (err.status === 502 || err.status === 503))
    return `The text service is unavailable right now (${err.message}). Please try again.`;
  return err instanceof Error ? err.message : String(err);
}

export default function ReadingView() {
  const navigate = useNavigate();
  const texts = useApi(
    "texts",
    useCallback(() => listTexts(), []),
  );
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<Tab>("url");
  const [url, setUrl] = useState("");
  const [pasted, setPasted] = useState("");
  const [topic, setTopic] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(
    job: () => Promise<TextOut>,
    onFail?: (e: unknown) => void,
  ) {
    if (busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const out = await job();
      void navigate(`/reading/${out.id}`);
    } catch (err) {
      if (onFail) onFail(err);
      else setError(failureMessage(err));
      setBusy(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    if (tab === "url") {
      void run(
        () => createText({ url: url.trim() }),
        (err) => {
          if (err instanceof ApiError && err.status === 422) {
            setTab("paste");
            setNotice(
              `Could not read the article: ${err.message} Paste the text instead.`,
            );
          } else setError(failureMessage(err));
        },
      );
    } else {
      void run(() => createText({ text: pasted }));
    }
  }

  function generate(e: FormEvent) {
    e.preventDefault();
    void run(() => generateText(topic.trim() || undefined));
  }

  const canSubmit = tab === "url" ? url.trim() !== "" : pasted.trim() !== "";

  return (
    <section>
      <h1>Reading</h1>
      {busy && (
        <p role="status">
          <span className="spinner" aria-hidden="true" />
          Simplifying for your level… this can take up to a minute
        </p>
      )}
      {!busy && !open && (
        <div className="row">
          <button
            type="button"
            className="btn primary"
            onClick={() => setOpen(true)}
          >
            New text
          </button>
        </div>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {open && !busy && (
        <div className="card">
          <div className="tabs" role="tablist" aria-label="Text source">
            {(["url", "paste"] as const).map((t) => (
              <button
                key={t}
                type="button"
                role="tab"
                aria-selected={tab === t}
                className={`tab ${tab === t ? "active" : ""}`}
                onClick={() => setTab(t)}
              >
                {t === "url" ? "URL" : "Paste text"}
              </button>
            ))}
          </div>
          <form className="form" onSubmit={submit}>
            {notice && <p role="alert">{notice}</p>}
            {tab === "url" ? (
              <label className="field">
                Article URL
                <input
                  type="url"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://…"
                  autoComplete="off"
                />
              </label>
            ) : (
              <label className="field">
                Text
                <textarea
                  lang="de"
                  rows={8}
                  value={pasted}
                  onChange={(e) => setPasted(e.target.value)}
                  placeholder="Paste a German text here"
                />
              </label>
            )}
            <div className="row">
              <button
                type="submit"
                className="btn primary"
                disabled={!canSubmit}
              >
                {error ? "Try again" : "Simplify"}
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => setOpen(false)}
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}
      {!busy && (
        <form className="form" onSubmit={generate}>
          <label className="field">
            Generate a text (optional topic)
            <input
              type="text"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="e.g. football, cooking"
            />
          </label>
          <div className="row">
            <button type="submit" className="btn">
              Generate a text
            </button>
          </div>
        </form>
      )}
      <h2>Your texts</h2>
      {texts.status === "loading" && <p className="muted">Loading…</p>}
      {texts.status === "error" && (
        <p role="alert">Could not load texts: {texts.error.message}</p>
      )}
      {texts.status === "ok" && texts.data.length === 0 && (
        <p className="muted">No texts yet. Add one to start reading.</p>
      )}
      {texts.status === "ok" && texts.data.length > 0 && (
        <ul className="text-list" aria-label="Texts">
          {texts.data.map((t) => (
            <li key={t.id}>
              <Link to={`/reading/${t.id}`} className="text-link">
                <strong lang="de">{t.title}</strong>
                <span className="muted">
                  {new Date(t.created_at).toLocaleDateString(undefined, {
                    day: "numeric",
                    month: "short",
                  })}
                  {" · "}
                  {t.level} · {Math.round(t.coverage * 100)}% known words
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
