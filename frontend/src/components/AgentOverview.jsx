import React from "react";

const AGENT_CONFIG = {
  news: {
    icon: "\ud83d\udce1",
    label: "Agent News",
    desc: "Collecte & curation",
    team: "shared",
    metricsDisplay: (m) => [
      { label: "Collect\u00e9s", value: m.last_collect_count || 0 },
      { label: "Erreurs sources", value: m.source_errors || 0, warn: (m.source_errors || 0) > 0 },
      { label: "Total collect\u00e9s", value: m.total_collected || 0 },
    ],
  },
  scoring: {
    icon: "\ud83c\udfaf",
    label: "Agent Scoring",
    desc: "Notation edge & impact",
    team: "shared",
    metricsDisplay: (m) => [
      { label: "Scor\u00e9s", value: m.last_scored_count || 0 },
      { label: "Zero-edge filtr\u00e9s", value: m.zero_edge_filtered || 0 },
      { label: "Tokens", value: m.total_tokens_used ? `${(m.total_tokens_used / 1000).toFixed(1)}k` : "0" },
    ],
  },
  scoring_2: {
    icon: "\ud83d\udccf",
    label: "Agent Scoring 2",
    desc: "Re-pond\u00e9ration trend",
    team: "team2",
    metricsDisplay: (m) => [
      { label: "Items analys\u00e9s", value: m.total_items_analyzed || 0 },
      { label: "Pertinents trend", value: m.relevant_for_trend || 0 },
      { label: "Score moyen", value: m.avg_trend_score ? m.avg_trend_score.toFixed(1) : "\u2014" },
    ],
  },
  trader_1: {
    icon: "\ud83d\udcb9",
    label: "Agent Trader 1",
    desc: "Day trading intraday",
    team: "team1",
    metricsDisplay: (m) => [
      { label: "Trades aujourd'hui", value: m.trades_today || 0 },
      { label: "Rejets", value: m.rejections_today || 0 },
      { label: "Dernier", value: m.last_trade_ticker ? `${m.last_trade_direction} ${m.last_trade_ticker}` : "\u2014" },
    ],
  },
  trader_2: {
    icon: "\ud83d\udcc8",
    label: "Agent Trader 2",
    desc: "Trend following commodities",
    team: "team2",
    metricsDisplay: (m) => [
      { label: "Positions", value: m.active_positions || 0 },
      { label: "P&L r\u00e9alis\u00e9", value: m.realized_pnl != null ? `${m.realized_pnl > 0 ? "+" : ""}${m.realized_pnl.toFixed(2)}%` : "\u2014", positive: (m.realized_pnl || 0) > 0 },
      { label: "Flips", value: m.total_flips || 0 },
    ],
  },
  journal: {
    icon: "\ud83d\udcd3",
    label: "Agent Journal 1",
    desc: "Cl\u00f4ture trades & P&L",
    team: "team1",
    metricsDisplay: (m) => [
      { label: "Ferm\u00e9s", value: m.last_trades_closed || 0 },
      { label: "TP/SL/EXP", value: `${m.last_tp || 0}/${m.last_sl || 0}/${m.last_expired || 0}` },
      { label: "PnL", value: m.last_pnl_sum ? `${m.last_pnl_sum > 0 ? "+" : ""}${m.last_pnl_sum.toFixed(2)}%` : "\u2014", positive: (m.last_pnl_sum || 0) > 0 },
    ],
  },
  journal_2: {
    icon: "\ud83d\udcd4",
    label: "Agent Journal 2",
    desc: "Journal des flips trend",
    team: "team2",
    metricsDisplay: (m) => [
      { label: "Flips journalis\u00e9s", value: m.flips_recorded || 0 },
      { label: "MAE moyen", value: m.avg_mae != null ? `${m.avg_mae.toFixed(2)}%` : "\u2014", warn: (m.avg_mae || 0) < -5 },
      { label: "Snapshots", value: m.snapshots_today || 0 },
    ],
  },
  learning: {
    icon: "\ud83e\udde0",
    label: "Agent Learning 1",
    desc: "6 dimensions ML",
    team: "team1",
    metricsDisplay: (m) => [
      { label: "Ajustements", value: m.last_adjustment_count || 0 },
      { label: "Anomalies", value: (m.anomalies || []).length, warn: (m.anomalies || []).length > 0 },
      { label: "Recalculs", value: m.total_recalculations || 0 },
    ],
  },
  learning_2: {
    icon: "\ud83d\udca1",
    label: "Agent Learning 2",
    desc: "4 dimensions trend",
    team: "team2",
    metricsDisplay: (m) => [
      { label: "Ajustements", value: m.last_adjustment_count || 0 },
      { label: "Seuil adj", value: m.threshold_adj != null ? m.threshold_adj.toFixed(3) : "\u2014" },
      { label: "Anomalies", value: (m.anomalies || []).length, warn: (m.anomalies || []).length > 0 },
    ],
  },
  infrastructure: {
    icon: "\ud83d\udee0\ufe0f",
    label: "Agent Infrastructure",
    desc: "Sant\u00e9 PG & maintenance",
    team: "infra",
    metricsDisplay: (m) => [
      { label: "PG status", value: m.pg_connected ? "OK" : "DOWN", warn: !m.pg_connected },
      { label: "Dernier VACUUM", value: m.last_vacuum || "\u2014" },
      { label: "Tables", value: m.table_count || "\u2014" },
    ],
  },
  performance: {
    icon: "\ud83d\udcca",
    label: "Agent Performance",
    desc: "KPIs & tendances",
    team: "infra",
    metricsDisplay: (m) => [
      { label: "Snapshots", value: m.total_snapshots || 0 },
      { label: "Rapports", value: m.total_reports || 0 },
      { label: "Alertes", value: m.active_alerts || 0, warn: (m.active_alerts || 0) > 0 },
    ],
  },
  auditor: {
    icon: "\ud83d\udd0d",
    label: "Agent Auditeur",
    desc: "Audit en profondeur",
    team: "infra",
    metricsDisplay: (m) => [
      { label: "Audits", value: m.total_audits || 0 },
      { label: "Dernier", value: m.last_audit_target || "\u2014" },
      { label: "Score", value: m.last_audit_score != null ? `${m.last_audit_score}/10` : "\u2014" },
    ],
  },
  ux: {
    icon: "\ud83c\udfa8",
    label: "Agent UX",
    desc: "Frontend & dashboard",
    team: "infra",
    metricsDisplay: () => [
      { label: "Statut", value: "En ligne" },
    ],
  },
};

const STATUS_LABELS = {
  idle: "En attente",
  working: "En cours",
  error: "Erreur",
  disabled: "D\u00e9sactiv\u00e9",
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
            icon: "\u2699\ufe0f",
            label: agent.name,
            desc: agent.description || "",
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
                  <span className="agent-card-name">
                    {config.label}
                    {agent.version && (
                      <span className="agent-card-version">v{agent.version}</span>
                    )}
                  </span>
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
