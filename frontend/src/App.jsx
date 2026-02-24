import React, { useState } from "react";
import Dashboard from "./components/Dashboard";
import Journal from "./components/Journal";
import History from "./components/History";
import Performance from "./components/Performance";

const TABS = [
  { id: "dashboard", label: "Dashboard" },
  { id: "journal", label: "Journal" },
  { id: "history", label: "Historique" },
  { id: "performance", label: "Performance" },
];

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

      {activeTab === "dashboard" && <Dashboard />}
      {activeTab === "journal" && <Journal />}
      {activeTab === "history" && <History />}
      {activeTab === "performance" && <Performance />}
    </div>
  );
}
