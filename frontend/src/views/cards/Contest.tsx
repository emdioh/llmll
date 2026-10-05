import { useState, type FormEvent } from "react";
import {
  ApiError,
  contestAttempt,
  contestEvaluation,
  type ContestResult,
} from "../../api/client";

/** Small inline form: optional reason, send/cancel. One contest per evaluation. */
export function ContestForm({
  evaluationId,
  attemptId,
  itemIds,
  onResolved,
  onCancel,
}: {
  /** Evaluation to contest; when null, `attemptId` (pre-M4 flashcard attempts) is used. */
  evaluationId?: number | null;
  attemptId?: number | null;
  /** Contested items; empty means the whole answer. */
  itemIds: string[];
  onResolved: (result: ContestResult) => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      if (evaluationId != null)
        onResolved(await contestEvaluation(evaluationId, itemIds, reason));
      else if (attemptId != null)
        onResolved(await contestAttempt(attemptId, itemIds, reason));
      else throw new Error("Nothing to contest");
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 409
          ? "Already contested"
          : err instanceof Error
            ? err.message
            : String(err),
      );
      setBusy(false);
    }
  }

  return (
    <form className="contest-form" onSubmit={submit}>
      <label className="field">
        <span>Why was it right? (optional)</span>
        <input
          type="text"
          maxLength={500}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </label>
      {error && <p role="alert">{error}</p>}
      <div className="row">
        <button type="submit" className="btn primary" disabled={busy}>
          {busy ? "Sending…" : "Send contest"}
        </button>
        <button
          type="button"
          className="btn"
          disabled={busy}
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
