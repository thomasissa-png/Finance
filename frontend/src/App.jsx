import React, { useState, useEffect, useCallback, lazy, Suspense } from "react";
import { getParisHour, getParisDay } from "./utils/format";
import NotificationCenter from "./components/NotificationCenter";

// Lazy-load pages
const DashboardPage = lazy(() => import("./components/DashboardPage"));
const TeamPage = lazy(() => import("./components/TeamPage"));
const NewsPage = lazy(() => import("./components/NewsPage"));
const PerformancePage = lazy(() => import("./components/PerformancePage"));
const AuditorPage = lazy(() => import("./components/AuditorPage"));

const PAGES = ["dashboard", "team1", "team2", "team3", "team4", "news", "performance", "auditor"];

function getPageFromHash() {
  const hash = window.location.hash.replace("#", "");
  return PAGES.includes(hash) ? hash : "dashboard";
}

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }
  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="error-boundary-container">
          <div style={{ fontSize: 28, marginBottom: 12, opacity: 0.4 }}>!</div>
          <div style={{ fontSize: 15, marginBottom: 8 }}>Erreur de rendu</div>
          <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 16 }}>
            {this.state.error?.message || "Une erreur inattendue est survenue"}
          </div>
          <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
            <button className="trigger-btn" onClick={() => this.setState({ hasError: false, error: null })}>
              Reessayer
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
  </div>
);

function getHeaderStatus() {
  const day = getParisDay();
  if (day === 0 || day === 6) return { cls: "weekend", text: "Ferme" };
  const hour = getParisHour();
  if (hour >= 7 && hour < 20) return { cls: "online", text: "Ouvert" };
  return { cls: "offline", text: "Hors session" };
}

const NAV_ITEMS = [
  { id: "dashboard", label: "Dashboard" },
  { id: "team1", label: "Equipe 1" },
  { id: "team2", label: "Equipe 2" },
  { id: "team3", label: "Equipe 3" },
  { id: "team4", label: "Equipe 4" },
  { id: "news", label: "News" },
  { id: "performance", label: "Performance" },
  { id: "auditor", label: "Audit" },
];

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

  // Fetch notifications
  const fetchNotifications = useCallback(() => {
    const agentNames = [
      "news", "scoring", "scoring_2", "scoring_3", "scoring_4",
      "trader_1", "trader_2", "trader_3", "trader_4",
      "journal", "journal_2", "journal_3", "journal_4",
      "learning", "learning_2", "learning_3", "learning_4",
      "auditor",
    ];
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
      const combined = results.flat().sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
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

  const unreadCount = notifications.filter(
    (n) => new Date(n.timestamp).getTime() > lastNotifCheck
  ).length;

  const markAllRead = useCallback(() => { setLastNotifCheck(Date.now()); }, []);

  const navigate = useCallback((pageId) => {
    setActivePage(pageId);
    setShowNotifications(false);
    window.location.hash = pageId;
  }, []);

  useEffect(() => {
    const onHash = () => setActivePage(getPageFromHash());
    window.addEventListener("hashchange", onHash);
    if (!window.location.hash) window.location.hash = "dashboard";
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    const interval = setInterval(() => setStatus(getHeaderStatus()), 60_000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => { window.scrollTo({ top: 0, behavior: "smooth" }); }, [activePage]);

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

  return (
    <div className="app-layout">
      {/* Top Navbar */}
      <nav className="top-navbar">
        <div className="nav-left">
          <span className="nav-brand">PLATEFORME TRADING</span>
          <div className="nav-links">
            {NAV_ITEMS.map((item) => (
              <button
                key={item.id}
                className={`nav-link ${activePage === item.id ? "active" : ""}`}
                onClick={() => navigate(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
        <div className="nav-right">
          <div className="nav-status">
            <span className={`status-dot ${status.cls}`} />
            {status.text}
          </div>
          <button
            className="nav-alert-btn"
            onClick={() => {
              setShowNotifications(!showNotifications);
              if (!showNotifications) markAllRead();
            }}
          >
            {"🔔"}
            {unreadCount > 0 && (
              <span className="nav-alert-badge">{unreadCount > 9 ? "9+" : unreadCount}</span>
            )}
          </button>
        </div>
      </nav>

      {/* Main content */}
      <div className="app-main">
        {!backendUp && (
          <div className="disconnect-banner">
            <span className="status-dot offline" />
            Backend deconnecte — tentative de reconnexion...
          </div>
        )}

        <ErrorBoundary>
          <Suspense fallback={<LoadingFallback />}>
            {activePage === "dashboard" && <DashboardPage isActive agents={agents} />}
            {activePage === "team1" && <TeamPage teamId="1" isActive agents={agents} />}
            {activePage === "team2" && <TeamPage teamId="2" isActive agents={agents} />}
            {activePage === "team3" && <TeamPage teamId="3" isActive agents={agents} />}
            {activePage === "team4" && <TeamPage teamId="4" isActive agents={agents} />}
            {activePage === "news" && <NewsPage isActive />}
            {activePage === "performance" && <PerformancePage isActive agents={agents} />}
            {activePage === "auditor" && <AuditorPage isActive />}
          </Suspense>
        </ErrorBoundary>
      </div>

      {/* Notification panel */}
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
