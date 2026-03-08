import React from "react";

const AGENT_ICONS = {
  news: "\ud83d\udce1", scoring: "\ud83c\udfaf",
  scoring_2: "\ud83d\udccf", scoring_3: "\ud83d\udcc9", scoring_4: "\ud83e\udde9",
  trader_1: "\ud83d\udcb9", trader_2: "\ud83d\udcc8", trader_3: "\ud83d\udcc0", trader_4: "\ud83c\udfb0",
  journal: "\ud83d\udcd3", journal_2: "\ud83d\udcd4", journal_3: "\ud83d\udcd2", journal_4: "\ud83d\udcd5",
  learning: "\ud83e\udde0", learning_2: "\ud83d\udca1", learning_3: "\ud83e\uddea", learning_4: "\ud83e\uddf2",
  auditor: "\ud83d\udd0d", infrastructure: "\ud83d\udee0\ufe0f", performance: "\ud83d\udcca", ux: "\ud83c\udfa8",
};

const AGENT_LABELS = {
  news: "News", scoring: "Scoring",
  scoring_2: "Scoring", scoring_3: "Scoring", scoring_4: "Scoring",
  trader_1: "Trader", trader_2: "Trader", trader_3: "Trader", trader_4: "Trader",
  journal: "Journal", journal_2: "Journal", journal_3: "Journal", journal_4: "Journal",
  learning: "Learning", learning_2: "Learning", learning_3: "Learning", learning_4: "Learning",
  auditor: "Auditeur", infrastructure: "Infra", performance: "Performance", ux: "UX",
};

// Map agent names to page routes
const AGENT_TO_PAGE = {
  news: "news", scoring: "scoring",
  scoring_2: "scoring2", scoring_3: "scoring3", scoring_4: "scoring4",
  trader_1: "trader", trader_2: "trader2", trader_3: "trader3", trader_4: "trader4",
  journal: "journal", journal_2: "journal2", journal_3: "journal3", journal_4: "journal4",
  learning: "learning", learning_2: "learning2", learning_3: "learning3", learning_4: "learning4",
  auditor: "auditor",
};

const STATUS_COLORS = {
  idle: "var(--text-muted)",
  working: "var(--cyan)",
  error: "var(--red)",
  disabled: "var(--text-muted)",
};

// Grouped agent layout by team — extensible for N teams
const SIDEBAR_STRUCTURE = [
  { type: "section", label: "Partag\u00e9" },
  { type: "agent", name: "news" },
  { type: "agent", name: "scoring" },
  { type: "section", label: "\u00c9q. 1 \u2014 Intraday" },
  { type: "agent", name: "trader_1" },
  { type: "agent", name: "journal" },
  { type: "agent", name: "learning" },
  { type: "section", label: "\u00c9q. 2 \u2014 Tendance" },
  { type: "agent", name: "scoring_2" },
  { type: "agent", name: "trader_2" },
  { type: "agent", name: "journal_2" },
  { type: "agent", name: "learning_2" },
  { type: "section", label: "\u00c9q. 3 \u2014 Technique" },
  { type: "agent", name: "scoring_3" },
  { type: "agent", name: "trader_3" },
  { type: "agent", name: "journal_3" },
  { type: "agent", name: "learning_3" },
  { type: "section", label: "\u00c9q. 4 \u2014 Meta" },
  { type: "agent", name: "scoring_4" },
  { type: "agent", name: "trader_4" },
  { type: "agent", name: "journal_4" },
  { type: "agent", name: "learning_4" },
  { type: "divider" },
  { type: "agent", name: "auditor" },
];

function AgentButton({ agent, activePage, onNavigate }) {
  if (!agent) return null;
  const page = AGENT_TO_PAGE[agent.name];
  if (!page) return null;
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
        {agent.version && (
          <span className="agent-version-badge">v{agent.version}</span>
        )}
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

      <button
        className={`agent-sidebar-item ${activePage === "dashboard" ? "selected" : ""}`}
        onClick={() => onNavigate("dashboard")}
      >
        <span className="agent-sidebar-icon">{"\ud83c\udfe0"}</span>
        <span className="agent-sidebar-label">Dashboard</span>
      </button>

      <button
        className={`agent-sidebar-item ${activePage === "team" ? "selected" : ""}`}
        onClick={() => onNavigate("team")}
      >
        <span className="agent-sidebar-icon">{"\ud83d\udcda"}</span>
        <span className="agent-sidebar-label">Vue d'ensemble</span>
      </button>

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
