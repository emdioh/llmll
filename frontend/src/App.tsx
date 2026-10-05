import { NavLink, Route, Routes } from "react-router";
import LearnerGate from "./LearnerGate";
import LlmBanner from "./LlmBanner";
import CorpusView from "./views/CorpusView";
import GrammarDetailView from "./views/GrammarDetailView";
import GrammarView from "./views/GrammarView";
import ItemDetailView from "./views/ItemDetailView";
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

export default function App() {
  return (
    <div className="shell">
      <header className="topbar">LLMLL</header>
      <LearnerGate>
        <nav className="nav" aria-label="Main">
          {tabs.map((t) => (
            <NavLink key={t.to} to={t.to} end={t.end} className="nav-link">
              {t.label}
            </NavLink>
          ))}
        </nav>
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
            <Route path="/settings" element={<SettingsView />} />
          </Routes>
        </main>
      </LearnerGate>
    </div>
  );
}
