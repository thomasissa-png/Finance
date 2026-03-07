import React from "react";

const AGENT_ICONS = {
  news: "📡",
  scoring: "🎯",
  trader_1: "💹",
  journal: "📊",
  learning: "🧠",
  ux: "🎨",
};

const AGENT_LABELS = {
  news: "News",
  scoring: "Scoring",
  trader_1: "Trader",
  journal: "Journal",
  learning: "Learning",
  ux: "UX",
};

const STATUS_COLORS = {
  idle: "var(--text-muted)",
  working: "var(--cyan)",
  error: "var(--red)",
  disabled: "var(--text-muted)",
};

export default function AgentSidebar({ agents, selectedAgent, onSelectAgent }) {
  return (
    <aside className="agent-sidebar">
      <div className="agent-sidebar-title">AGENTS</div>
      {(agents || []).map((agent) => {
        const isSelected = selectedAgent === agent.name;
        const statusColor = STATUS_COLORS[agent.status] || STATUS_COLORS.idle;
        const isWorking = agent.status === "working";

        return (
          <button
            key={agent.name}
            className={`agent-sidebar-item ${isSelected ? "selected" : ""} ${isWorking ? "working" : ""}`}
            onClick={() => onSelectAgent(isSelected ? null : agent.name)}
          >
            <span className="agent-sidebar-icon">
              {AGENT_ICONS[agent.name] || "⚙️"}
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
    </aside>
  );
}
