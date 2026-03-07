import React, { useState, useEffect, useCallback } from "react";
import { formatTimeAgo } from "../utils/format";

const LEVEL_COLORS = {
  INFO: "var(--text-secondary)",
  WARN: "var(--yellow)",
  ERROR: "var(--red)",
  DECISION: "var(--cyan)",
};

const LEVEL_ICONS = {
  INFO: "ℹ️",
  WARN: "⚠️",
  ERROR: "❌",
  DECISION: "⚡",
};

export default function AgentDetail({ agentName, agentData, isActive }) {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("ALL");

  const fetchLogs = useCallback(() => {
    if (!agentName || !isActive) return;
    const levelParam = filter !== "ALL" ? `&level=${filter}` : "";
    fetch(`/api/agents/${agentName}/logs?limit=100${levelParam}`)
      .then((r) => r.json())
      .then((data) => {
        setLogs(Array.isArray(data) ? data : []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [agentName, isActive, filter]);

  useEffect(() => {
    fetchLogs();
    const id = setInterval(fetchLogs, 5000);
    return () => clearInterval(id);
  }, [fetchLogs]);

  if (!agentData) return null;

  const metrics = agentData.metrics || {};
  const statusLabel =
    agentData.status === "working"
      ? agentData.last_action || "En cours..."
      : agentData.status === "error"
        ? agentData.last_error || "Erreur"
        : agentData.last_action || "En attente";

  return (
    <div className="agent-detail">
      {/* Header */}
      <div className="agent-detail-header">
        <div>
          <h2 className="agent-detail-name">
            Agent {agentData.description}
          </h2>
          <div className="agent-detail-status">
            <span className={`agent-status-dot ${agentData.status}`} />
            <span className="agent-detail-status-text">{statusLabel}</span>
          </div>
        </div>
        <div className="agent-detail-meta">
          {agentData.last_action_time && (
            <span className="agent-detail-time">
              {formatTimeAgo(agentData.last_action_time)}
            </span>
          )}
          <span className="agent-detail-count">
            {agentData.action_count || 0} actions
          </span>
        </div>
      </div>

      {/* Metrics */}
      {Object.keys(metrics).length > 0 && (
        <div className="agent-metrics-grid">
          {Object.entries(metrics).map(([key, value]) => {
            if (value === null || value === undefined) return null;
            // Skip array/object metrics in the grid
            if (typeof value === "object") return null;
            const label = key
              .replace(/_/g, " ")
              .replace(/\b\w/g, (c) => c.toUpperCase());
            return (
              <div key={key} className="agent-metric-card">
                <div className="agent-metric-value">
                  {typeof value === "number"
                    ? value % 1 !== 0
                      ? value.toFixed(2)
                      : value.toLocaleString()
                    : String(value)}
                </div>
                <div className="agent-metric-label">{label}</div>
              </div>
            );
          })}
        </div>
      )}

      {/* Log filter */}
      <div className="agent-log-filter">
        {["ALL", "DECISION", "INFO", "WARN", "ERROR"].map((level) => (
          <button
            key={level}
            className={`agent-log-filter-btn ${filter === level ? "active" : ""}`}
            onClick={() => setFilter(level)}
          >
            {level}
          </button>
        ))}
        <button className="agent-log-refresh" onClick={fetchLogs}>
          ↻
        </button>
      </div>

      {/* Logs timeline */}
      <div className="agent-logs">
        {loading ? (
          <div className="agent-logs-loading">Chargement...</div>
        ) : logs.length === 0 ? (
          <div className="agent-logs-empty">Aucun log disponible</div>
        ) : (
          logs.map((log, i) => (
            <div
              key={`${log.timestamp}-${i}`}
              className={`agent-log-entry ${log.level?.toLowerCase() || "info"}`}
            >
              <div className="agent-log-header">
                <span className="agent-log-icon">
                  {LEVEL_ICONS[log.level] || "ℹ️"}
                </span>
                <span
                  className="agent-log-action"
                  style={{ color: LEVEL_COLORS[log.level] || LEVEL_COLORS.INFO }}
                >
                  {log.action}
                </span>
                <span className="agent-log-time">
                  {log.timestamp
                    ? new Date(log.timestamp).toLocaleTimeString("fr-FR", {
                        hour: "2-digit",
                        minute: "2-digit",
                        second: "2-digit",
                      })
                    : ""}
                </span>
                {log.duration_ms != null && (
                  <span className="agent-log-duration">{log.duration_ms}ms</span>
                )}
              </div>
              {log.details && Object.keys(log.details).length > 0 && (
                <div className="agent-log-details">
                  {Object.entries(log.details).map(([k, v]) => (
                    <span key={k} className="agent-log-detail">
                      <span className="agent-log-detail-key">{k}:</span>{" "}
                      {typeof v === "object"
                        ? JSON.stringify(v).slice(0, 120)
                        : String(v).slice(0, 120)}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
