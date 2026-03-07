import React, { useState, useEffect, useCallback, lazy, Suspense } from "react";
import { getParisHour, getParisDay } from "./utils/format";
import AgentSidebar from "./components/AgentSidebar";
import AgentOverview from "./components/AgentOverview";
import AgentDetail from "./components/AgentDetail";

// (F1) Lazy-load tab components for code splitting
const Dashboard = lazy(() => import("./components/Dashboard"));
const Journal = lazy(() => import("./components/Journal"));
const History = lazy(() => import("./components/History"));
const Performance = lazy(() => import("./components/Performance"));

const TABS = [
  { id: "dashboard", label: "Dashboard", shortcut: "1" },
  { id: "journal", label: "Journal", shortcut: "2" },
  { id: "history", label: "Historique", shortcut: "3" },
  { id: "performance", label: "Performance", shortcut: "4" },
];

const TAB_IDS = TABS.map((t) => t.id);

// (C3) Hash routing — read initial tab from URL hash
function getTabFromHash() {
  const hash = window.location.hash.replace("#", "");
  if (hash.startsWith("agent:")) return "dashboard";
  return TAB_IDS.includes(hash) ? hash : "dashboard";
}

function getAgentFromHash() {
  const hash = window.location.hash.replace("#", "");
  if (hash.startsWith("agent:")) return hash.split(":")[1];
  return null;
}

// (F5) ErrorBoundary — catches render errors in child components
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error("ErrorBoundary caught:", error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="no-trade">
          <div className="no-trade-icon">!</div>
          <div className="no-trade-title">Erreur de rendu</div>
          <div className="no-trade-reason">
            {this.state.error?.message || "Une erreur inattendue est survenue"}
          </div>
          <div style={{ display: "flex", gap: 8, justifyContent: "center", marginTop: 12 }}>
            <button
              className="trigger-btn"
              onClick={() => this.setState({ hasError: false, error: null })}
            >
              Réessayer
            </button>
            <button
              className="trigger-btn export"
              onClick={() => window.location.reload()}
            >
              Recharger la page
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

// (O8) Skeleton loading fallback
const LoadingFallback = () => (
  <div>
    <div className="skeleton skeleton-card" />
    <div className="skeleton skeleton-card" />
    <div className="skeleton skeleton-card" style={{ height: 80 }} />
  </div>
);

// (M7) Timezone-aware status — uses Europe/Paris
function getHeaderStatus() {
  const day = getParisDay();
  if (day === 0 || day === 6) {
    return { cls: "weekend", text: "Marchés fermés" };
  }
  const hour = getParisHour();
  if (hour >= 7 && hour < 20) {
    return { cls: "online", text: "Marchés ouverts" };
  }
  return { cls: "offline", text: "Hors session" };
}

export default function App() {
  const [activeTab, setActiveTab] = useState(getTabFromHash);
  const [selectedAgent, setSelectedAgent] = useState(getAgentFromHash);
  const [status, setStatus] = useState(getHeaderStatus);
  const [backendUp, setBackendUp] = useState(true);
  const [agents, setAgents] = useState([]);

  // Fetch agents status
  const fetchAgents = useCallback(() => {
    fetch("/api/agents")
      .then((r) => r.json())
      .then((data) => {
        if (Array.isArray(data)) setAgents(data);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetchAgents();
    const id = setInterval(fetchAgents, 5000);
    return () => clearInterval(id);
  }, [fetchAgents]);

  // Tab switching
  const switchTab = useCallback((tabId) => {
    setActiveTab(tabId);
    setSelectedAgent(null);
    window.location.hash = tabId;
  }, []);

  // Agent selection
  const selectAgent = useCallback((agentName) => {
    setSelectedAgent(agentName);
    if (agentName) {
      window.location.hash = `agent:${agentName}`;
    } else {
      window.location.hash = activeTab;
    }
  }, [activeTab]);

  // (C3) Listen to popstate (back/forward)
  useEffect(() => {
    const onHash = () => {
      setActiveTab(getTabFromHash());
      setSelectedAgent(getAgentFromHash());
    };
    window.addEventListener("hashchange", onHash);
    if (!window.location.hash) {
      window.location.hash = "dashboard";
    }
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Update header status every minute
  useEffect(() => {
    const interval = setInterval(() => setStatus(getHeaderStatus()), 60_000);
    return () => clearInterval(interval);
  }, []);

  // Fix 4: Force dark theme
  useEffect(() => {
    document.body.classList.remove("light");
    localStorage.removeItem("theme");
  }, []);

  // (D12) Scroll to top on tab change
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [activeTab, selectedAgent]);

  // (M3) Backend health check
  useEffect(() => {
    let mounted = true;
    const check = () => {
      fetch("/api/health")
        .then((r) => { if (mounted) setBackendUp(r.ok); })
        .catch(() => { if (mounted) setBackendUp(false); });
    };
    check();
    const id = setInterval(check, 30_000);
    return () => { mounted = false; clearInterval(id); };
  }, []);

  // (N4) Keyboard shortcuts: 1-4 for tabs, Escape to close agent detail
  useEffect(() => {
    const onKey = (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT" || e.target.tagName === "TEXTAREA") return;
      if (e.key === "Escape" && selectedAgent) {
        selectAgent(null);
        return;
      }
      const idx = parseInt(e.key, 10);
      if (idx >= 1 && idx <= 4) {
        switchTab(TABS[idx - 1].id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [switchTab, selectAgent, selectedAgent]);

  // Count working agents for header
  const workingCount = agents.filter((a) => a.status === "working").length;
  const errorCount = agents.filter((a) => a.status === "error").length;

  // Get selected agent data
  const selectedAgentData = agents.find((a) => a.name === selectedAgent);

  return (
    <div className="app app-with-sidebar">
      {/* Agent Sidebar */}
      <AgentSidebar
        agents={agents}
        selectedAgent={selectedAgent}
        onSelectAgent={selectAgent}
      />

      {/* Main content */}
      <div className="app-main">
        <header className="app-header">
          <div>
            <div className="app-title">ONESHOT NEWS TRADING</div>
            <div className="app-subtitle">
              6 agents autonomes
              {workingCount > 0 && (
                <span className="header-agents-working">
                  {" "}&mdash; {workingCount} en cours
                </span>
              )}
              {errorCount > 0 && (
                <span className="header-agents-error">
                  {" "}&mdash; {errorCount} erreur{errorCount > 1 ? "s" : ""}
                </span>
              )}
            </div>
          </div>
          <div className="header-right">
            <div className="header-status">
              <span className={`status-dot ${status.cls}`} />
              {status.text}
            </div>
          </div>
        </header>

        {/* Backend disconnect banner */}
        {!backendUp && (
          <div className="disconnect-banner">
            <span className="status-dot offline" />
            Backend déconnecté — tentative de reconnexion...
          </div>
        )}

        {/* Show agent detail if selected */}
        {selectedAgent ? (
          <ErrorBoundary>
            <AgentDetail
              agentName={selectedAgent}
              agentData={selectedAgentData}
              isActive={true}
            />
          </ErrorBoundary>
        ) : (
          <>
            {/* Agent Overview cards on dashboard */}
            {activeTab === "dashboard" && (
              <ErrorBoundary>
                <AgentOverview agents={agents} onSelectAgent={selectAgent} />
              </ErrorBoundary>
            )}

            <nav className="tabs">
              {TABS.map((tab) => (
                <button
                  key={tab.id}
                  className={`tab ${activeTab === tab.id ? "active" : ""}`}
                  onClick={() => switchTab(tab.id)}
                >
                  {tab.label}
                  <span className="tab-shortcut">{tab.shortcut}</span>
                </button>
              ))}
            </nav>

            <ErrorBoundary>
              <Suspense fallback={<LoadingFallback />}>
                <div className="tab-content" style={{ display: activeTab === "dashboard" ? "block" : "none" }}>
                  <Dashboard isActive={activeTab === "dashboard"} />
                </div>
                <div className="tab-content" style={{ display: activeTab === "journal" ? "block" : "none" }}>
                  <Journal isActive={activeTab === "journal"} />
                </div>
                <div className="tab-content" style={{ display: activeTab === "history" ? "block" : "none" }}>
                  <History isActive={activeTab === "history"} />
                </div>
                <div className="tab-content" style={{ display: activeTab === "performance" ? "block" : "none" }}>
                  <Performance isActive={activeTab === "performance"} />
                </div>
              </Suspense>
            </ErrorBoundary>
          </>
        )}
      </div>
    </div>
  );
}
