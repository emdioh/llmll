import { useEffect, useState } from "react";
import { getHealth } from "./api/client";

const KEY = "llmll.fakeLlmBannerDismissed";

function wasDismissed(): boolean {
  try {
    return sessionStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

/** Warns when the backend simulates the LLM. Dismissal lasts for the browser session. */
export default function LlmBanner() {
  const [fake, setFake] = useState(false);
  const [dismissed, setDismissed] = useState(wasDismissed);

  useEffect(() => {
    let cancelled = false;
    getHealth().then(
      (h) => {
        if (!cancelled) setFake(h.llm === "fake");
      },
      () => {},
    );
    return () => {
      cancelled = true;
    };
  }, []);

  if (!fake || dismissed) return null;
  return (
    <div className="banner" role="status">
      <span>
        Running without an LLM API key: exercises and grading are simulated.
      </span>
      <button
        type="button"
        className="btn"
        onClick={() => {
          try {
            sessionStorage.setItem(KEY, "1");
          } catch {
            // storage unavailable: dismiss for this page load only
          }
          setDismissed(true);
        }}
      >
        Dismiss
      </button>
    </div>
  );
}
