import { lazy, Suspense } from "react";
import { useSearchParams } from "react-router";
import { HistoryTab } from "./History";
import Overview from "./Overview";

// The lists load their code and data only when their tab is opened.
const ItemsTab = lazy(() => import("./ItemsTab"));

const TABS = [
  { id: "grammar", label: "Grammar" },
  { id: "words", label: "Words" },
  { id: "history", label: "History" },
] as const;
type TabId = (typeof TABS)[number]["id"];

export default function ProgressView() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab");
  const tab: TabId = TABS.some((t) => t.id === raw)
    ? (raw as TabId)
    : "grammar";

  return (
    <section>
      <h1>Progress</h1>
      <Overview />
      <div className="tabs progress-tabs" role="tablist" aria-label="Details">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls="tab-panel"
            className={`tab${tab === t.id ? " active" : ""}`}
            onClick={() => setParams({ tab: t.id }, { replace: true })}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id="tab-panel" aria-labelledby={`tab-${tab}`}>
        <Suspense fallback={<p role="status">Loading…</p>}>
          {tab === "grammar" && <ItemsTab mode="grammar" />}
          {tab === "words" && <ItemsTab mode="words" />}
          {tab === "history" && <HistoryTab />}
        </Suspense>
      </div>
    </section>
  );
}
