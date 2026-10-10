import { Navigate, NavLink, Route, Routes, useParams } from "react-router";
import AuthGate from "./AuthGate";
import LearnerGate from "./LearnerGate";
import DebugDrawer, { DebugSpacer } from "./debug/DebugDrawer";
import { useDebug } from "./debug/debugContext";
import DebugProvider from "./debug/DebugProvider";
import LlmBanner from "./LlmBanner";
import TimezoneSync from "./TimezoneSync";
import DebugView from "./views/DebugView";
import { useMediaQuery, WIDE_SCREEN } from "./useMediaQuery";
import DrillView from "./views/DrillView";
import GrammarDetailView from "./views/GrammarDetailView";
import GrammarView from "./views/GrammarView";
import PlacementView from "./views/PlacementView";
import { ReadingDetailView, SessionDetailView } from "./views/progress/History";
import ItemDetail from "./views/progress/ItemDetail";
import ProgressView from "./views/progress/ProgressView";
import ReaderView from "./views/ReaderView";
import ReadingView from "./views/ReadingView";
import SessionView from "./views/SessionView";
import SettingsView from "./views/SettingsView";

const tabs = [
  { to: "/", label: "Session", end: true },
  { to: "/reading", label: "Reading", end: false },
  { to: "/progress", label: "Progress", end: false },
  { to: "/grammar", label: "Grammar", end: false },
  { to: "/settings", label: "Settings", end: false },
];

function Nav() {
  const { enabled } = useDebug();
  // Debug is a developer tool: listed on wide screens only (still reachable at /debug).
  const wide = useMediaQuery(WIDE_SCREEN);
  const all =
    enabled && wide
      ? [...tabs, { to: "/debug", label: "Debug", end: false }]
      : tabs;
  return (
    <nav className="nav" aria-label="Main">
      {all.map((t) => (
        <NavLink key={t.to} to={t.to} end={t.end} className="nav-link">
          {t.label}
        </NavLink>
      ))}
    </nav>
  );
}

/** Old corpus links (`/corpus/:id`) now open the progress detail. */
function CorpusItemRedirect() {
  const { id = "" } = useParams();
  return <Navigate to={`/progress/items/${encodeURIComponent(id)}`} replace />;
}

export default function App() {
  return (
    <div className="shell">
      <header className="topbar">LLMLL</header>
      <AuthGate>
        <DebugProvider>
          <LearnerGate>
            <TimezoneSync />
            <Nav />
            <main className="content">
              <LlmBanner />
              <Routes>
                <Route path="/" element={<SessionView />} />
                <Route path="/reading" element={<ReadingView />} />
                <Route path="/reading/:textId" element={<ReaderView />} />
                <Route path="/progress" element={<ProgressView />} />
                <Route path="/progress/items/:id" element={<ItemDetail />} />
                <Route
                  path="/progress/history/session/:id"
                  element={<SessionDetailView />}
                />
                <Route
                  path="/progress/history/reading/:id"
                  element={<ReadingDetailView />}
                />
                <Route
                  path="/corpus"
                  element={<Navigate to="/progress?tab=words" replace />}
                />
                <Route path="/corpus/:id" element={<CorpusItemRedirect />} />
                <Route path="/grammar" element={<GrammarView />} />
                <Route path="/grammar/:id" element={<GrammarDetailView />} />
                <Route path="/grammar/:id/drill" element={<DrillView />} />
                <Route path="/placement" element={<PlacementView />} />
                <Route path="/settings" element={<SettingsView />} />
                <Route path="/debug" element={<DebugView />} />
              </Routes>
              <DebugSpacer />
            </main>
          </LearnerGate>
          <DebugDrawer />
        </DebugProvider>
      </AuthGate>
    </div>
  );
}
