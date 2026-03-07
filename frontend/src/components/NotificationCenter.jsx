import React, { useState } from "react";

const AGENT_ICONS = {
  news: "\ud83d\udce1",
  scoring: "\ud83c\udfaf",
  trader_1: "\ud83d\udcb9",
  journal: "\ud83d\udcd3",
  learning: "\ud83e\udde0",
  auditor: "\ud83d\udd0d",
};

const LEVEL_STYLES = {
  WARN: { color: "var(--yellow)", icon: "\u26a0\ufe0f" },
  ERROR: { color: "var(--red)", icon: "\u274c" },
  INFO: { color: "var(--text-secondary)", icon: "\u2139\ufe0f" },
  DECISION: { color: "var(--cyan)", icon: "\u26a1" },
};

const AGENT_PAGES = {
  news: "news",
  scoring: "scoring",
  trader_1: "trader",
  journal: "journal",
  learning: "learning",
  auditor: "auditor",
};

export default function NotificationCenter({ notifications, onClose, onNavigate }) {
  const [filterAgent, setFilterAgent] = useState("all");
  const [filterLevel, setFilterLevel] = useState("all");

  const filtered = notifications.filter((n) => {
    if (filterAgent !== "all" && n.agent !== filterAgent) return false;
    if (filterLevel !== "all" && n.level !== filterLevel) return false;
    return true;
  });

  return (
    <div className="notif-overlay" onClick={onClose}>
      <div className="notif-panel" onClick={(e) => e.stopPropagation()}>
        <div className="notif-header">
          <h3>Alertes & Notifications</h3>
          <button className="notif-close" onClick={onClose}>&times;</button>
        </div>

        {/* Filters */}
        <div className="notif-filters">
          <select value={filterAgent} onChange={(e) => setFilterAgent(e.target.value)} className="notif-filter-select">
            <option value="all">Tous les agents</option>
            {Object.entries(AGENT_ICONS).map(([k, icon]) => (
              <option key={k} value={k}>{icon} {k}</option>
            ))}
          </select>
          <select value={filterLevel} onChange={(e) => setFilterLevel(e.target.value)} className="notif-filter-select">
            <option value="all">Tous niveaux</option>
            <option value="ERROR">Erreurs</option>
            <option value="WARN">Avertissements</option>
          </select>
        </div>

        {/* Notification list */}
        <div className="notif-list">
          {filtered.length === 0 ? (
            <div className="notif-empty">Aucune alerte r\u00e9cente</div>
          ) : (
            filtered.map((n, i) => {
              const style = LEVEL_STYLES[n.level] || LEVEL_STYLES.INFO;
              return (
                <div
                  key={`${n.timestamp}-${n.agent}-${i}`}
                  className={`notif-item notif-${(n.level || "info").toLowerCase()}`}
                  onClick={() => {
                    const page = AGENT_PAGES[n.agent];
                    if (page) { onNavigate(page); onClose(); }
                  }}
                >
                  <div className="notif-item-header">
                    <span className="notif-item-icon">{style.icon}</span>
                    <span className="notif-item-agent">
                      {AGENT_ICONS[n.agent] || ""} {n.agent}
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
