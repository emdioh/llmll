import { NavLink, Route, Routes } from "react-router";
import AuthGate from "./AuthGate";
import LearnerGate from "./LearnerGate";
import DebugDrawer, { DebugSpacer } from "./debug/DebugDrawer";
import { useDebug } from "./debug/debugContext";
import DebugProvider from "./debug/DebugProvider";
import LlmBanner from "./LlmBanner";
import CorpusView from "./views/CorpusView";
import DebugView from "./views/DebugView";
import GrammarDetailView from "./views/GrammarDetailView";
import GrammarView from "./views/GrammarView";
import ItemDetailView from "./views/ItemDetailView";
import PlacementView from "./views/PlacementView";
import ReaderView from "./views/ReaderView";
import ReadingView from "./views/ReadingView";
import SessionView from "./views/SessionView";
import SettingsView from "./views/SettingsView";

const tabs = [
  { to: "/", label: "Session", end: true },
  { to: "/reading", label: "Reading", end: false },
  { to: "/corpus", label: "Corpus", end: false },
  { to: "/grammar", label: "Grammar", end: false },
  { to: "/settings", label: "Settings", end: false },
];

function Nav() {
  const { enabled } = useDebug();
  const all = enabled
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

export default function App() {
  return (
    <div className="shell">
      <header className="topbar">LLMLL</header>
      <AuthGate>
        <DebugProvider>
          <LearnerGate>
            <Nav />
            <main className="content">
              <LlmBanner />
              <Routes>
                <Route path="/" element={<SessionView />} />
                <Route path="/reading" element={<ReadingView />} />
                <Route path="/reading/:textId" element={<ReaderView />} />
                <Route path="/corpus" element={<CorpusView />} />
                <Route path="/corpus/:id" element={<ItemDetailView />} />
                <Route path="/grammar" element={<GrammarView />} />
                <Route path="/grammar/:id" element={<GrammarDetailView />} />
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
