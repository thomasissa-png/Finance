import React from "react";

const AGENT_ICONS = {
  news: "\ud83d\udce1",
  scoring: "\ud83c\udfaf",
  scoring_2: "\ud83d\udccf",
  trader_1: "\ud83d\udcb9",
  trader_2: "\ud83d\udcc8",
  journal: "\ud83d\udcd3",
  journal_2: "\ud83d\udcd4",
  learning: "\ud83e\udde0",
  learning_2: "\ud83d\udca1",
  auditor: "\ud83d\udd0d",
  ux: "\ud83c\udfa8",
};

const AGENT_LABELS = {
  news: "News",
  scoring: "Scoring",
  scoring_2: "Scoring",
  trader_1: "Trader",
  trader_2: "Trader",
  journal: "Journal",
  journal_2: "Journal",
  learning: "Learning",
  learning_2: "Learning",
  auditor: "Auditeur",
  ux: "UX",
};

// Map agent names to page routes
const AGENT_TO_PAGE = {
  news: "news",
  scoring: "scoring",
  scoring_2: "scoring2",
  trader_1: "trader",
  trader_2: "trader2",
  journal: "journal",
  journal_2: "journal2",
  learning: "learning",
  learning_2: "learning2",
  auditor: "auditor",
};

const STATUS_COLORS = {
  idle: "var(--text-muted)",
  working: "var(--cyan)",
  error: "var(--red)",
  disabled: "var(--text-muted)",
};

// Grouped agent layout by team
const SIDEBAR_STRUCTURE = [
  { type: "section", label: "Partag\u00e9" },
  { type: "agent", name: "news" },
  { type: "agent", name: "scoring" },
  { type: "section", label: "\u00c9quipe 1 \u2014 Intraday" },
  { type: "agent", name: "trader_1" },
  { type: "agent", name: "journal" },
  { type: "agent", name: "learning" },
  { type: "section", label: "\u00c9quipe 2 \u2014 Tendance" },
  { type: "agent", name: "scoring_2" },
  { type: "agent", name: "trader_2" },
  { type: "agent", name: "journal_2" },
  { type: "agent", name: "learning_2" },
  { type: "divider" },
  { type: "agent", name: "auditor" },
];

function AgentButton({ agent, activePage, onNavigate }) {
  if (!agent) return null;
  const page = AGENT_TO_PAGE[agent.name];
  const isActive = activePage === page;
  const statusColor = STATUS_COLORS[agent.status] || STATUS_COLORS.idle;
  const isWorking = agent.status === "working";

  return (
    <button
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
}

export default function AgentSidebar({ agents, activePage, onNavigate, notificationCount, onToggleNotifications }) {
  const agentMap = {};
  (agents || []).forEach((a) => { agentMap[a.name] = a; });

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

      {/* Team-grouped agent navigation */}
      {SIDEBAR_STRUCTURE.map((item, idx) => {
        if (item.type === "section") {
          return (
            <div key={idx} className="agent-sidebar-section">
              {item.label}
            </div>
          );
        }
        if (item.type === "divider") {
          return <div key={idx} className="agent-sidebar-divider" />;
        }
        if (item.type === "agent") {
          return (
            <AgentButton
              key={item.name}
              agent={agentMap[item.name]}
              activePage={activePage}
              onNavigate={onNavigate}
            />
          );
        }
        return null;
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
