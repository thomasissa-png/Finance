import React, { useState } from "react";

const AGENT_LABELS = {
  news: "News",
  scoring: "Scoring 1",
  scoring_2: "Scoring 2",
  scoring_3: "Scoring 3",
  scoring_4: "Scoring 4",
  trader_1: "Trader 1",
  trader_2: "Trader 2",
  trader_3: "Trader 3",
  trader_4: "Trader 4",
  journal: "Journal 1",
  journal_2: "Journal 2",
  journal_3: "Journal 3",
  journal_4: "Journal 4",
  learning: "Learning 1",
  learning_2: "Learning 2",
  learning_3: "Learning 3",
  learning_4: "Learning 4",
  auditor: "Auditeur",
  infrastructure: "Infra",
  performance: "Performance",
};

const AGENT_TO_PAGE = {
  news: "news",
  scoring: "team1",
  scoring_2: "team2",
  scoring_3: "team3",
  scoring_4: "team4",
  trader_1: "team1",
  trader_2: "team2",
  trader_3: "team3",
  trader_4: "team4",
  journal: "team1",
  journal_2: "team2",
  journal_3: "team3",
  journal_4: "team4",
  learning: "team1",
  learning_2: "team2",
  learning_3: "team3",
  learning_4: "team4",
  auditor: "auditor",
  infrastructure: "performance",
  performance: "performance",
};

const LEVEL_STYLES = {
  WARN: { color: "var(--yellow)", icon: "!" },
  ERROR: { color: "var(--red)", icon: "x" },
  INFO: { color: "var(--text-secondary)", icon: "i" },
  DECISION: { color: "var(--accent)", icon: ">" },
};

export default function NotificationCenter({ notifications, onClose, onNavigate }) {
  const [filterAgent, setFilterAgent] = useState("all");
  const [filterLevel, setFilterLevel] = useState("all");

  const filtered = notifications.filter((n) => {
    if (filterAgent !== "all" && n.agent !== filterAgent) return false;
    if (filterLevel !== "all" && n.level !== filterLevel) return false;
    return true;
  });

  const agentNames = [...new Set(notifications.map((n) => n.agent))].sort();

  return (
    <div className="notif-overlay" onClick={onClose}>
      <div className="notif-panel" onClick={(e) => e.stopPropagation()}>
        <div className="notif-header">
          <h3>Alertes & Notifications</h3>
          <button className="notif-close" onClick={onClose}>&times;</button>
        </div>

        <div className="notif-filters">
          <select value={filterAgent} onChange={(e) => setFilterAgent(e.target.value)} className="notif-filter-select">
            <option value="all">Tous les agents</option>
            {agentNames.map((name) => (
              <option key={name} value={name}>{AGENT_LABELS[name] || name}</option>
            ))}
          </select>
          <select value={filterLevel} onChange={(e) => setFilterLevel(e.target.value)} className="notif-filter-select">
            <option value="all">Tous niveaux</option>
            <option value="ERROR">Erreurs</option>
            <option value="WARN">Avertissements</option>
          </select>
        </div>

        <div className="notif-list">
          {filtered.length === 0 ? (
            <div className="notif-empty">Aucune alerte récente</div>
          ) : (
            filtered.map((n, i) => {
              const style = LEVEL_STYLES[n.level] || LEVEL_STYLES.INFO;
              return (
                <div
                  key={`${n.timestamp}-${n.agent}-${i}`}
                  className={`notif-item notif-${(n.level || "info").toLowerCase()}`}
                  onClick={() => {
                    const page = AGENT_TO_PAGE[n.agent];
                    if (page) { onNavigate(page); onClose(); }
                  }}
                >
                  <div className="notif-item-header">
                    <span className="notif-item-icon">{style.icon}</span>
                    <span className="notif-item-agent">
                      {AGENT_LABELS[n.agent] || n.agent}
                    </span>
                    <span className="notif-item-time">
                      {n.timestamp
                        ? new Date(n.timestamp).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })
                        : ""}
                    </span>
                    <span className="notif-item-date">
                      {n.timestamp
                        ? new Date(n.timestamp).toLocaleDateString("fr-FR", { day: "2-digit", month: "short" })
                        : ""}
                    </span>
                  </div>
                  <div className="notif-item-action" style={{ color: style.color }}>
                    {n.action}
                  </div>
                  {n.details && Object.keys(n.details).length > 0 && (
                    <div className="notif-item-details">
                      {Object.entries(n.details).slice(0, 3).map(([k, v]) => (
                        <span key={k} className="notif-detail">
                          {k}: {typeof v === "object" ? JSON.stringify(v).slice(0, 80) : String(v).slice(0, 80)}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
