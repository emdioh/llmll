import { useEffect, useRef, useState } from "react";

export type ApiState<T> =
  | { status: "loading" }
  | { status: "ok"; data: T }
  | { status: "error"; error: Error };

/**
 * Runs `load` whenever `key` changes. Results are tagged with the request key
 * so a stale response is never shown for a newer key. `load` may be an inline
 * function: only `key` triggers a request, so it must include everything
 * `load` depends on.
 */
export function useApi<T>(key: string, load: () => Promise<T>): ApiState<T> {
  const [settled, setSettled] = useState<{
    key: string;
    result: ApiState<T>;
  } | null>(null);

  const loadRef = useRef(load);
  useEffect(() => {
    loadRef.current = load;
  });

  useEffect(() => {
    let cancelled = false;
    loadRef.current().then(
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
  }, [key]);

  return settled && settled.key === key
    ? settled.result
    : { status: "loading" };
}
