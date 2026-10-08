import { useState } from "react";
import { optinItem, optoutItem } from "../../api/client";

/** "Learn this" / "Don't teach me this" for an item that is not yet learned. */
export default function OptControls({
  itemId,
  status,
  source,
}: {
  itemId: string;
  status: string | null;
  source: string | null;
}) {
  const [override, setOverride] = useState<{
    id: string;
    status: string;
    source: string | null;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function opt(action: typeof optinItem) {
    setBusy(true);
    setError(null);
    try {
      const r = await action(itemId);
      setOverride({ id: itemId, status: r.status, source: r.candidate_source });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const eff =
    override?.id === itemId
      ? { status: override.status, source: override.source }
      : { status, source };
  // Items that are already learned or presumed known cannot be opted in or out.
  if (eff.status === "introduced" || eff.status === "presumed_known")
    return null;
  const queuedByYou = eff.status === "candidate" && eff.source === "optin";
  return (
    <div className="row">
      {eff.status === "suspended" && (
        <p className="muted">You asked not to be taught this.</p>
      )}
      {queuedByYou && <p className="muted">Queued: you chose to learn this.</p>}
      {!queuedByYou && (
        <button
          type="button"
          className="btn primary"
          disabled={busy}
          onClick={() => opt(optinItem)}
        >
          Learn this
        </button>
      )}
      {eff.status !== "suspended" && (
        <button
          type="button"
          className="btn"
          disabled={busy}
          onClick={() => opt(optoutItem)}
        >
          Don&apos;t teach me this
        </button>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
