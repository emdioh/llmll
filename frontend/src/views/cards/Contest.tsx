import { useState, type FormEvent } from "react";
import {
  ApiError,
  contestEvaluation,
  type ContestResult,
} from "../../api/client";

/** Small inline form: optional reason, send/cancel. One contest per evaluation. */
export function ContestForm({
  evaluationId,
  itemIds,
  onResolved,
  onCancel,
}: {
  evaluationId: number;
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
      onResolved(await contestEvaluation(evaluationId, itemIds, reason));
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
