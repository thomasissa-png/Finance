import React, { useState, useEffect, useCallback } from "react";
import Journal from "./Journal";

const LEVEL_ICONS = { INFO: "\u2139\ufe0f", WARN: "\u26a0\ufe0f", ERROR: "\u274c", DECISION: "\u26a1" };
const LEVEL_COLORS = { INFO: "var(--text-secondary)", WARN: "var(--yellow)", ERROR: "var(--red)", DECISION: "var(--cyan)" };

export default function JournalPage({ isActive }) {
  const [learning, setLearning] = useState(null);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState(null);

  const fetchExtra = useCallback(async () => {
    try {
      const [lRes, logRes] = await Promise.all([
        fetch("/api/learning").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch(`/api/agents/journal/logs?limit=30${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`)
          .then((r) => r.ok ? r.json() : []).catch(() => []),
      ]);
      setLearning(lRes);
      setLogs(Array.isArray(logRes) ? logRes : []);
      setFetchError(null);
    } catch (err) {
      setFetchError(err.message || "Erreur de chargement");
    } finally {
      setLoading(false);
    }
  }, [logFilter]);

  useEffect(() => {
    if (isActive) fetchExtra();
    const id = setInterval(fetchExtra, 60_000);
    return () => clearInterval(id);
  }, [isActive, fetchExtra]);

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>{"\ud83d\udcd3"} Agent Journal</h2>
        <span className="agent-page-desc">Documentation, clôture des trades, analyse P&L, MAE/MFE</span>
      </div>

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement des données...</div>}
      {fetchError && <div className="agent-error-banner">Erreur : {fetchError}</div>}

      {/* Existing Journal component */}
      <Journal isActive={isActive} />

      {/* Learning feedback used by journal */}
      {learning && (
        <div className="section-card" style={{ marginTop: 24 }}>
          <h3>Contexte Learning injecté au journal</h3>
          <div className="learning-dimensions">
            {learning.session_adj && Object.keys(learning.session_adj).length > 0 && (
              <div className="learning-dim">
                <span className="learning-dim-label">Ajustements par session</span>
                {Object.entries(learning.session_adj).map(([k, v]) => (
                  <span key={k} className="learning-dim-value" style={{ color: v >= 1 ? "var(--green)" : "var(--red)" }}>
                    {k}: {v.toFixed(3)}
                  </span>
                ))}
              </div>
            )}
            {learning.regime_adj && Object.keys(learning.regime_adj).length > 0 && (
              <div className="learning-dim">
                <span className="learning-dim-label">Ajustements par régime VIX</span>
                {Object.entries(learning.regime_adj).map(([k, v]) => (
                  <span key={k} className="learning-dim-value" style={{ color: v >= 1 ? "var(--green)" : "var(--red)" }}>
                    {k}: {v.toFixed(3)}
                  </span>
                ))}
              </div>
            )}
            {learning.delay_bias_adj != null && (
              <div className="learning-dim">
                <span className="learning-dim-label">Delay Bias global</span>
                <span className="learning-dim-value" style={{ color: learning.delay_bias_adj >= 1 ? "var(--green)" : "var(--red)" }}>
                  {learning.delay_bias_adj.toFixed(3)}
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Journal agent logs */}
      <div className="section-card" style={{ marginTop: 24 }}>
        <div className="section-header">
          <h3>Logs Agent Journal</h3>
          <div className="log-filter-row">
            {["ALL", "DECISION", "INFO", "WARN", "ERROR"].map((level) => (
              <button key={level} className={`log-filter-btn ${logFilter === level ? "active" : ""}`}
                onClick={() => setLogFilter(level)}>
                {level}
              </button>
            ))}
          </div>
        </div>
        <div className="agent-logs compact-logs">
          {logs.length === 0 ? (
            <div className="agent-logs-empty">Aucun log</div>
          ) : (
            logs.slice(0, 15).map((log, i) => (
              <div key={`${log.timestamp}-${i}`} className={`agent-log-entry ${(log.level || "info").toLowerCase()}`}>
                <div className="agent-log-header">
                  <span className="agent-log-icon">{LEVEL_ICONS[log.level] || "\u2139\ufe0f"}</span>
                  <span className="agent-log-action" style={{ color: LEVEL_COLORS[log.level] }}>{log.action}</span>
                  <span className="agent-log-time">
                    {log.timestamp ? new Date(log.timestamp).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : ""}
                  </span>
                </div>
                {log.details && Object.keys(log.details).length > 0 && (
                  <div className="agent-log-details">
                    {Object.entries(log.details).slice(0, 3).map(([k, v]) => (
                      <span key={k} className="agent-log-detail">
                        <span className="agent-log-detail-key">{k}:</span>{" "}
                        {typeof v === "object" ? JSON.stringify(v).slice(0, 80) : String(v).slice(0, 80)}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
