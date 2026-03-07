import React, { useState, useEffect, useCallback, lazy, Suspense } from "react";
import { getParisHour, getParisDay } from "./utils/format";
import AgentSidebar from "./components/AgentSidebar";
import NotificationCenter from "./components/NotificationCenter";

// Lazy-load all pages
const DashboardPage = lazy(() => import("./components/DashboardPage"));
const TraderPage = lazy(() => import("./components/TraderPage"));
const ScoringPage = lazy(() => import("./components/ScoringPage"));
const JournalPage = lazy(() => import("./components/JournalPage"));
const NewsPage = lazy(() => import("./components/NewsPage"));
const LearningPage = lazy(() => import("./components/LearningPage"));
const AuditorPage = lazy(() => import("./components/AuditorPage"));

const PAGES = [
  "dashboard", "news", "scoring", "trader", "journal", "learning", "auditor",
];

function getPageFromHash() {
  const hash = window.location.hash.replace("#", "");
  return PAGES.includes(hash) ? hash : "dashboard";
}

// ErrorBoundary
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }
  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }
  componentDidCatch(error, info) {
    console.error("ErrorBoundary:", error, info);
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
            <button className="trigger-btn" onClick={() => this.setState({ hasError: false, error: null })}>
              Réessayer
            </button>
            <button className="trigger-btn export" onClick={() => window.location.reload()}>
              Recharger
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

const LoadingFallback = () => (
  <div>
    <div className="skeleton skeleton-card" />
    <div className="skeleton skeleton-card" />
    <div className="skeleton skeleton-card" style={{ height: 80 }} />
  </div>
);

function getHeaderStatus() {
  const day = getParisDay();
  if (day === 0 || day === 6) return { cls: "weekend", text: "Marchés fermés" };
  const hour = getParisHour();
  if (hour >= 7 && hour < 20) return { cls: "online", text: "Marchés ouverts" };
  return { cls: "offline", text: "Hors session" };
}

export default function App() {
  const [activePage, setActivePage] = useState(getPageFromHash);
  const [status, setStatus] = useState(getHeaderStatus);
  const [backendUp, setBackendUp] = useState(true);
  const [agents, setAgents] = useState([]);
  const [showNotifications, setShowNotifications] = useState(false);
  const [notifications, setNotifications] = useState([]);
  const [lastNotifCheck, setLastNotifCheck] = useState(Date.now());

  // Fetch agents status
  const fetchAgents = useCallback(() => {
    fetch("/api/agents")
      .then((r) => r.json())
      .then((data) => { if (Array.isArray(data)) setAgents(data); })
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetchAgents();
    const id = setInterval(fetchAgents, 5000);
    return () => clearInterval(id);
  }, [fetchAgents]);

  // Fetch notifications (WARN/ERROR logs from all agents) — single flat Promise.all
  const fetchNotifications = useCallback(() => {
    const agentNames = ["news", "scoring", "trader_1", "journal", "learning", "auditor"];
    const requests = agentNames.flatMap((name) => [
      fetch(`/api/agents/${name}/logs?limit=20&level=WARN`)
        .then((r) => r.json())
        .then((logs) => (Array.isArray(logs) ? logs.map((l) => ({ ...l, agent: name })) : []))
        .catch(() => []),
      fetch(`/api/agents/${name}/logs?limit=10&level=ERROR`)
        .then((r) => r.json())
        .then((logs) => (Array.isArray(logs) ? logs.map((l) => ({ ...l, agent: name })) : []))
        .catch(() => []),
    ]);
    Promise.all(requests).then((results) => {
      const combined = results.flat()
        .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
      const seen = new Set();
      const deduped = combined.filter((n) => {
        const key = `${n.timestamp}-${n.agent}-${n.action}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      }).slice(0, 50);
      setNotifications(deduped);
    });
  }, []);

  useEffect(() => {
    fetchNotifications();
    const id = setInterval(fetchNotifications, 30000);
    return () => clearInterval(id);
  }, [fetchNotifications]);

  // Count unread notifications
  const unreadCount = notifications.filter(
    (n) => new Date(n.timestamp).getTime() > lastNotifCheck
  ).length;

  const markAllRead = useCallback(() => {
    setLastNotifCheck(Date.now());
  }, []);

  // Navigate
  const navigate = useCallback((pageId) => {
    setActivePage(pageId);
    setShowNotifications(false);
    window.location.hash = pageId;
  }, []);

  // Hash change listener
  useEffect(() => {
    const onHash = () => setActivePage(getPageFromHash());
    window.addEventListener("hashchange", onHash);
    if (!window.location.hash) window.location.hash = "dashboard";
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Header status update
  useEffect(() => {
    const interval = setInterval(() => setStatus(getHeaderStatus()), 60_000);
    return () => clearInterval(interval);
  }, []);

  // Force dark theme
  useEffect(() => {
    document.body.classList.remove("light");
    localStorage.removeItem("theme");
  }, []);

  // Scroll to top on page change
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [activePage]);

  // Backend health check
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

  // Keyboard shortcuts
  useEffect(() => {
    const onKey = (e) => {
      if (["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) return;
      if (e.key === "Escape") {
        if (showNotifications) setShowNotifications(false);
        else navigate("dashboard");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate, showNotifications]);

  const workingCount = agents.filter((a) => a.status === "working").length;
  const errorCount = agents.filter((a) => a.status === "error").length;

  return (
    <div className="app app-with-sidebar">
      <AgentSidebar
        agents={agents}
        activePage={activePage}
        onNavigate={navigate}
        notificationCount={unreadCount}
        onToggleNotifications={() => {
          setShowNotifications(!showNotifications);
          if (!showNotifications) markAllRead();
        }}
      />

      <div className="app-main">
        <header className="app-header">
          <div>
            <div className="app-title">ONESHOT NEWS TRADING</div>
            <div className="app-subtitle">
              7 agents autonomes
              {workingCount > 0 && (
                <span className="header-agents-working"> &mdash; {workingCount} en cours</span>
              )}
              {errorCount > 0 && (
                <span className="header-agents-error"> &mdash; {errorCount} erreur{errorCount > 1 ? "s" : ""}</span>
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

        {!backendUp && (
          <div className="disconnect-banner">
            <span className="status-dot offline" />
            Backend déconnecté — tentative de reconnexion...
          </div>
        )}

        <ErrorBoundary>
          <Suspense fallback={<LoadingFallback />}>
            {activePage === "dashboard" && <DashboardPage isActive={true} agents={agents} />}
            {activePage === "news" && <NewsPage isActive={true} />}
            {activePage === "scoring" && <ScoringPage isActive={true} />}
            {activePage === "trader" && <TraderPage isActive={true} />}
            {activePage === "journal" && <JournalPage isActive={true} />}
            {activePage === "learning" && <LearningPage isActive={true} />}
            {activePage === "auditor" && <AuditorPage isActive={true} />}
          </Suspense>
        </ErrorBoundary>
      </div>

      {showNotifications && (
        <NotificationCenter
          notifications={notifications}
          onClose={() => setShowNotifications(false)}
          onNavigate={navigate}
        />
      )}
    </div>
  );
}
