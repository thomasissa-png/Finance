import React, { useState, useEffect, useCallback, lazy, Suspense } from "react";
import { getParisHour, getParisDay } from "./utils/format";
import NotificationCenter from "./components/NotificationCenter";

// Lazy-load pages
const DashboardPage = lazy(() => import("./components/DashboardPage"));
const TeamsOverviewPage = lazy(() => import("./components/TeamsOverviewPage"));
const TeamPage = lazy(() => import("./components/TeamPage"));
const NewsPage = lazy(() => import("./components/NewsPage"));
const PerformancePage = lazy(() => import("./components/PerformancePage"));
const AuditorPage = lazy(() => import("./components/AuditorPage"));
const AdminPage = lazy(() => import("./components/AdminPage"));

const PAGES = ["dashboard", "equipes", "team1", "team2", "team3", "team4", "news", "performance", "auditor", "admin"];

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
  </div>
);

function getHeaderStatus() {
  const day = getParisDay();
  if (day === 0 || day === 6) return { cls: "weekend", text: "Fermé" };
  const hour = getParisHour();
  if (hour >= 7 && hour < 20) return { cls: "online", text: "Ouvert" };
  return { cls: "offline", text: "Hors session" };
}

const NAV_ITEMS = [
  { id: "dashboard", label: "Dashboard" },
  { id: "equipes", label: "Équipes", matchIds: ["equipes", "team1", "team2", "team3", "team4"] },
  { id: "news", label: "News" },
  { id: "performance", label: "Performance" },
  { id: "auditor", label: "Audit" },
  { id: "admin", label: "Admin" },
];

/* SVG bell icon — matches flat fintech style */
const BellIcon = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M6 13.5a2 2 0 0 0 4 0" />
    <path d="M13 6A5 5 0 0 0 3 6c0 3.5-1.5 5-1.5 5h13S13 9.5 13 6z" />
  </svg>
);

export default function App() {
  const [activePage, setActivePage] = useState(getPageFromHash);
  const [status, setStatus] = useState(getHeaderStatus);
  const [backendUp, setBackendUp] = useState(true);
  const [agents, setAgents] = useState([]);
  const [showNotifications, setShowNotifications] = useState(false);
  const [notifications, setNotifications] = useState([]);
  const [lastNotifCheck, setLastNotifCheck] = useState(() => {
    try { return parseInt(localStorage.getItem("lastNotifCheck"), 10) || Date.now(); } catch { return Date.now(); }
  });
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

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

  // Fetch notifications — consolidated (1 request per known agent instead of 2×18)
  const fetchNotifications = useCallback(() => {
    const knownAgents = (agents || []).map((a) => a.name).filter(Boolean);
    if (knownAgents.length === 0) return;
    const requests = knownAgents.map((name) =>
      fetch(`/api/agents/${name}/logs?limit=15&level=WARN,ERROR&since_hours=48`)
        .then((r) => r.ok ? r.json() : [])
        .then((logs) => (Array.isArray(logs) ? logs.map((l) => ({ ...l, agent: name })) : []))
        .catch(() => [])
    );
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
  }, [agents]);

  useEffect(() => {
    fetchNotifications();
    const id = setInterval(fetchNotifications, 30000);
    return () => clearInterval(id);
  }, [fetchNotifications]);

  const unreadCount = notifications.filter(
    (n) => new Date(n.timestamp).getTime() > lastNotifCheck
  ).length;

  const markAllRead = useCallback(() => {
    const now = Date.now();
    setLastNotifCheck(now);
    try { localStorage.setItem("lastNotifCheck", String(now)); } catch { /* */ }
  }, []);

  const navigate = useCallback((pageId) => {
    setActivePage(pageId);
    setShowNotifications(false);
    setMobileMenuOpen(false);
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
        else if (mobileMenuOpen) setMobileMenuOpen(false);
        else navigate("dashboard");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate, showNotifications, mobileMenuOpen]);

  const isTeamPage = ["team1", "team2", "team3", "team4"].includes(activePage);

  return (
    <div className="app-layout">
      {/* Top Navbar */}
      <nav className="top-navbar">
        <div className="nav-left">
          <span className="nav-brand" onClick={() => navigate("dashboard")} style={{ cursor: "pointer" }}>
            PLATEFORME TRADING
          </span>

          {/* Hamburger for mobile */}
          <button
            className="hamburger-btn"
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            aria-label="Menu"
          >
            <span className={`hamburger-line ${mobileMenuOpen ? "open" : ""}`} />
            <span className={`hamburger-line ${mobileMenuOpen ? "open" : ""}`} />
            <span className={`hamburger-line ${mobileMenuOpen ? "open" : ""}`} />
          </button>

          <div className={`nav-links ${mobileMenuOpen ? "nav-links-open" : ""}`}>
            {NAV_ITEMS.map((item) => {
              const isActive = item.matchIds
                ? item.matchIds.includes(activePage)
                : activePage === item.id;
              return (
                <button
                  key={item.id}
                  className={`nav-link ${isActive ? "active" : ""}`}
                  onClick={() => navigate(item.id)}
                >
                  {item.label}
                </button>
              );
            })}
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
            aria-label="Notifications"
          >
            <BellIcon />
            {unreadCount > 0 && (
              <span className="nav-alert-badge">{unreadCount > 9 ? "9+" : unreadCount}</span>
            )}
          </button>
        </div>
      </nav>

      {/* Mobile menu overlay */}
      {mobileMenuOpen && <div className="mobile-menu-overlay" onClick={() => setMobileMenuOpen(false)} />}

      {/* Main content */}
      <div className="app-main">
        {!backendUp && (
          <div className="disconnect-banner">
            <span className="status-dot offline" />
            Backend déconnecté, tentative de reconnexion...
          </div>
        )}

        <ErrorBoundary>
          <Suspense fallback={<LoadingFallback />}>
            {activePage === "dashboard" && <DashboardPage isActive agents={agents} />}
            {activePage === "equipes" && <TeamsOverviewPage isActive agents={agents} onNavigate={navigate} />}
            {isTeamPage && <TeamPage teamId={activePage.replace("team", "")} isActive agents={agents} onNavigateBack={() => navigate("equipes")} />}
            {activePage === "news" && <NewsPage isActive />}
            {activePage === "performance" && <PerformancePage isActive agents={agents} />}
            {activePage === "auditor" && <AuditorPage isActive agents={agents} />}
            {activePage === "admin" && <AdminPage isActive />}
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
