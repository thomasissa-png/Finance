import React, { useState, useEffect, useRef, lazy, Suspense } from "react";

// (F1) Lazy-load tab components for code splitting
const Dashboard = lazy(() => import("./components/Dashboard"));
const Journal = lazy(() => import("./components/Journal"));
const History = lazy(() => import("./components/History"));
const Performance = lazy(() => import("./components/Performance"));

const TABS = [
  { id: "dashboard", label: "Dashboard" },
  { id: "journal", label: "Journal" },
  { id: "history", label: "Historique" },
  { id: "performance", label: "Performance" },
];

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
          <button
            className="trigger-btn"
            style={{ marginTop: 12 }}
            onClick={() => this.setState({ hasError: false, error: null })}
          >
            Recharger
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

const LoadingFallback = () => (
  <div className="no-trade">
    <div className="no-trade-icon">...</div>
    <div className="no-trade-title">Chargement...</div>
  </div>
);

// (D1) Determine header status based on current day/time
function getHeaderStatus() {
  const now = new Date();
  const day = now.getDay(); // 0=Sunday, 6=Saturday
  if (day === 0 || day === 6) {
    return { cls: "weekend", text: "Marchés fermés" };
  }
  // Paris-ish approximation: trading hours 07:00-20:00 CET
  const hour = now.getHours();
  if (hour >= 7 && hour < 20) {
    return { cls: "online", text: "Marchés ouverts" };
  }
  return { cls: "offline", text: "Hors session" };
}

export default function App() {
  const [activeTab, setActiveTab] = useState("dashboard");
  const [status, setStatus] = useState(getHeaderStatus);
  const contentRef = useRef(null);

  // Update header status every minute
  useEffect(() => {
    const interval = setInterval(() => setStatus(getHeaderStatus()), 60_000);
    return () => clearInterval(interval);
  }, []);

  // (D12) Scroll to top on tab change
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [activeTab]);

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <div className="app-title">NEWS TRADING</div>
          <div className="app-subtitle">4 scans / jour</div>
        </div>
        {/* (D1) Live status indicator */}
        <div className="header-status">
          <span className={`status-dot ${status.cls}`} />
          {status.text}
        </div>
      </header>

      <nav className="tabs">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={`tab ${activeTab === tab.id ? "active" : ""}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      {/* (D11) tab-content wrapper for fade-in animation */}
      <ErrorBoundary>
        <Suspense fallback={<LoadingFallback />}>
          <div className="tab-content" key={activeTab} ref={contentRef}>
            {activeTab === "dashboard" && <Dashboard />}
            {activeTab === "journal" && <Journal />}
            {activeTab === "history" && <History />}
            {activeTab === "performance" && <Performance />}
          </div>
        </Suspense>
      </ErrorBoundary>
    </div>
  );
}
