import { useEffect, useState } from "react";

export type ApiState<T> =
  | { status: "loading" }
  | { status: "ok"; data: T }
  | { status: "error"; error: Error };

/**
 * Runs `load` whenever `key` or `load` changes. Results are tagged with the
 * request key so a stale response is never shown for a newer key. Callers
 * must memoise `load` (useCallback) and include everything it uses in `key`.
 */
export function useApi<T>(key: string, load: () => Promise<T>): ApiState<T> {
  const [settled, setSettled] = useState<{
    key: string;
    result: ApiState<T>;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    load().then(
      (data) => {
        if (!cancelled) setSettled({ key, result: { status: "ok", data } });
      },
      (err: unknown) => {
        if (!cancelled)
          setSettled({
            key,
            result: {
              status: "error",
              error: err instanceof Error ? err : new Error(String(err)),
            },
          });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [key, load]);

  return settled && settled.key === key
    ? settled.result
    : { status: "loading" };
}
