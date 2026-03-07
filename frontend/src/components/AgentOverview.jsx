import React from "react";

const AGENT_CONFIG = {
  news: {
    icon: "📡",
    label: "Agent News",
    desc: "Collecte & curation",
    metricsDisplay: (m) => [
      { label: "Collectés", value: m.last_collect_count || 0 },
      { label: "Erreurs sources", value: m.source_errors || 0, warn: (m.source_errors || 0) > 0 },
      { label: "Total collectés", value: m.total_collected || 0 },
    ],
  },
  scoring: {
    icon: "🎯",
    label: "Agent Scoring",
    desc: "Notation edge & impact",
    metricsDisplay: (m) => [
      { label: "Scorés", value: m.last_scored_count || 0 },
      { label: "Zero-edge filtrés", value: m.zero_edge_filtered || 0 },
      { label: "Tokens utilisés", value: m.total_tokens_used ? `${(m.total_tokens_used / 1000).toFixed(1)}k` : "0" },
    ],
  },
  trader_1: {
    icon: "💹",
    label: "Agent Trader",
    desc: "Décision d'investissement",
    metricsDisplay: (m) => [
      { label: "Trades aujourd'hui", value: m.trades_today || 0 },
      { label: "Rejets", value: m.rejections_today || 0 },
      { label: "Dernier", value: m.last_trade_ticker ? `${m.last_trade_direction} ${m.last_trade_ticker}` : "—" },
    ],
  },
  journal: {
    icon: "📊",
    label: "Agent Journal",
    desc: "Documentation & analyse",
    metricsDisplay: (m) => [
      { label: "Fermés", value: m.last_trades_closed || 0 },
      { label: "TP/SL/EXP", value: `${m.last_tp || 0}/${m.last_sl || 0}/${m.last_expired || 0}` },
      { label: "PnL", value: m.last_pnl_sum ? `${m.last_pnl_sum > 0 ? "+" : ""}${m.last_pnl_sum.toFixed(2)}%` : "—", positive: (m.last_pnl_sum || 0) > 0 },
    ],
  },
  learning: {
    icon: "🧠",
    label: "Agent Learning",
    desc: "ML & optimisation",
    metricsDisplay: (m) => [
      { label: "Ajustements", value: m.last_adjustment_count || 0 },
      { label: "Anomalies", value: (m.anomalies || []).length, warn: (m.anomalies || []).length > 0 },
      { label: "Recalculs", value: m.total_recalculations || 0 },
    ],
  },
  ux: {
    icon: "🎨",
    label: "Agent UX",
    desc: "Frontend & dashboard",
    metricsDisplay: () => [
      { label: "Statut", value: "En ligne" },
    ],
  },
};

const STATUS_LABELS = {
  idle: "En attente",
  working: "En cours",
  error: "Erreur",
  disabled: "Désactivé",
};

export default function AgentOverview({ agents, onSelectAgent }) {
  if (!agents || agents.length === 0) {
    return (
      <div className="agent-overview-empty">
        Chargement des agents...
      </div>
    );
  }

  return (
    <div className="agent-overview">
      <h2 className="agent-overview-title">Agents autonomes</h2>
      <div className="agent-cards-grid">
        {agents.map((agent) => {
          const config = AGENT_CONFIG[agent.name] || {
            icon: "⚙️",
            label: agent.name,
            desc: agent.description,
            metricsDisplay: () => [],
          };
          const metrics = config.metricsDisplay(agent.metrics || {});
          const isWorking = agent.status === "working";
          const isError = agent.status === "error";

          return (
            <div
              key={agent.name}
              className={`agent-card ${isWorking ? "working" : ""} ${isError ? "error" : ""}`}
              onClick={() => onSelectAgent(agent.name)}
              role="button"
              tabIndex={0}
            >
              <div className="agent-card-header">
                <span className="agent-card-icon">{config.icon}</span>
                <div className="agent-card-title">
                  <span className="agent-card-name">{config.label}</span>
                  <span className="agent-card-desc">{config.desc}</span>
                </div>
                <span className={`agent-status-badge ${agent.status}`}>
                  {isWorking
                    ? (agent.last_action || "...").slice(0, 25)
                    : STATUS_LABELS[agent.status] || agent.status}
                </span>
              </div>

              <div className="agent-card-metrics">
                {metrics.map((m, i) => (
                  <div key={i} className="agent-card-metric">
                    <span
                      className={`agent-card-metric-value ${
                        m.warn ? "warn" : m.positive ? "positive" : ""
                      }`}
                    >
                      {m.value}
                    </span>
                    <span className="agent-card-metric-label">{m.label}</span>
                  </div>
                ))}
              </div>

              {agent.last_action && agent.status !== "working" && (
                <div className="agent-card-last-action">
                  {agent.last_action.slice(0, 50)}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
