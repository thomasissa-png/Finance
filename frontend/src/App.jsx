import React, { useState, lazy, Suspense } from "react";

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
            {this.state.error?.message || "Une erreur inattendue est survenue."}
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

export default function App() {
  const [activeTab, setActiveTab] = useState("dashboard");

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <div className="app-title">ONESHOT</div>
          <div className="app-subtitle">News Trading — 2 scans / jour</div>
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

      <ErrorBoundary>
        <Suspense fallback={<LoadingFallback />}>
          {activeTab === "dashboard" && <Dashboard />}
          {activeTab === "journal" && <Journal />}
          {activeTab === "history" && <History />}
          {activeTab === "performance" && <Performance />}
        </Suspense>
      </ErrorBoundary>
    </div>
  );
}
