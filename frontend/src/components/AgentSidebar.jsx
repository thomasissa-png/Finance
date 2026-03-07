import React from "react";

const AGENT_ICONS = {
  news: "\ud83d\udce1",
  scoring: "\ud83c\udfaf",
  trader_1: "\ud83d\udcb9",
  journal: "\ud83d\udcd3",
  learning: "\ud83e\udde0",
  auditor: "\ud83d\udd0d",
  ux: "\ud83c\udfa8",
};

const AGENT_LABELS = {
  news: "News",
  scoring: "Scoring",
  trader_1: "Trader",
  journal: "Journal",
  learning: "Learning",
  auditor: "Auditeur",
  ux: "UX",
};

// Map agent names to page routes
const AGENT_TO_PAGE = {
  news: "news",
  scoring: "scoring",
  trader_1: "trader",
  journal: "journal",
  learning: "learning",
  auditor: "auditor",
};

const STATUS_COLORS = {
  idle: "var(--text-muted)",
  working: "var(--cyan)",
  error: "var(--red)",
  disabled: "var(--text-muted)",
};

export default function AgentSidebar({ agents, activePage, onNavigate, notificationCount, onToggleNotifications }) {
  return (
    <aside className="agent-sidebar">
      <div className="agent-sidebar-title">ONESHOT</div>

      {/* Dashboard nav */}
      <button
        className={`agent-sidebar-item ${activePage === "dashboard" ? "selected" : ""}`}
        onClick={() => onNavigate("dashboard")}
      >
        <span className="agent-sidebar-icon">{"\ud83d\udcca"}</span>
        <span className="agent-sidebar-label">Dashboard</span>
      </button>

      <div className="agent-sidebar-divider" />

      {/* Agent navigation */}
      {(agents || [])
        .filter((a) => a.name !== "ux")
        .map((agent) => {
          const page = AGENT_TO_PAGE[agent.name];
          const isActive = activePage === page;
          const statusColor = STATUS_COLORS[agent.status] || STATUS_COLORS.idle;
          const isWorking = agent.status === "working";

          return (
            <button
              key={agent.name}
              className={`agent-sidebar-item ${isActive ? "selected" : ""} ${isWorking ? "working" : ""}`}
              onClick={() => onNavigate(page)}
            >
              <span className="agent-sidebar-icon">
                {AGENT_ICONS[agent.name] || "\u2699\ufe0f"}
              </span>
              <span className="agent-sidebar-label">
                {AGENT_LABELS[agent.name] || agent.name}
              </span>
              <span
                className={`agent-status-dot ${agent.status}`}
                style={{ backgroundColor: statusColor }}
              />
            </button>
          );
        })}

      <div className="agent-sidebar-divider" />

      {/* Notifications */}
      <button
        className="agent-sidebar-item agent-sidebar-notif"
        onClick={onToggleNotifications}
      >
        <span className="agent-sidebar-icon">{"\ud83d\udd14"}</span>
        <span className="agent-sidebar-label">Alertes</span>
        {notificationCount > 0 && (
          <span className="notif-badge">{notificationCount > 9 ? "9+" : notificationCount}</span>
        )}
      </button>
    </aside>
  );
}
