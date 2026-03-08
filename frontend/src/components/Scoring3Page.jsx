import React, { useState, useEffect, useCallback } from "react";

const LEVEL_ICONS = { INFO: "\u2139\ufe0f", WARN: "\u26a0\ufe0f", ERROR: "\u274c", DECISION: "\u26a1" };
const LEVEL_COLORS = { INFO: "var(--text-secondary)", WARN: "var(--yellow)", ERROR: "var(--red)", DECISION: "var(--cyan)" };

const DIR_COLORS = { LONG: "var(--green)", SHORT: "var(--red)", NEUTRAL: "var(--text-muted)" };
const DIR_ARROWS = { LONG: "\u2191", SHORT: "\u2193", NEUTRAL: "\u2022" };

export default function Scoring3Page({ isActive }) {
  const [data, setData] = useState(null);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState("ALL");
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const [resultRes, logRes] = await Promise.all([
        fetch("/api/scoring3/result").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch(`/api/agents/scoring_3/logs?limit=50${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`)
          .then((r) => r.ok ? r.json() : []).catch(() => []),
      ]);
      setData(resultRes);
      setLogs(Array.isArray(logRes) ? logRes : []);
    } catch { /* ignore */ } finally {
      setLoading(false);
    }
  }, [logFilter]);

  useEffect(() => {
    if (isActive) fetchData();
    const id = setInterval(fetchData, 60_000);
    return () => clearInterval(id);
  }, [isActive, fetchData]);

  const stats = data?.stats || {};
  const setups = data?.scored_setups || [];

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>{"\ud83d\udcca"} Agent Scoring 3 &mdash; Indicateurs Techniques</h2>
        <span className="agent-page-desc">
          D&eacute;tection et scoring de setups techniques &mdash; strat&eacute;gies, timeframes, signaux directionnels
        </span>
      </div>

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement...</div>}

      {/* KPIs */}
      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-value">{stats.setups_detected || 0}</div>
          <div className="kpi-label">Setups d&eacute;tect&eacute;s</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{stats.avg_tech_score || 0}</div>
          <div className="kpi-label">Score tech moyen</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{stats.top_strategy || "\u2014"}</div>
          <div className="kpi-label">Meilleure strat&eacute;gie</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{stats.top_timeframe || "\u2014"}</div>
          <div className="kpi-label">Meilleur timeframe</div>
        </div>
      </div>

      {/* Scored technical setups */}
      <div className="section-card">
        <h3>Setups techniques scor&eacute;s ({setups.length})</h3>
        {setups.length > 0 ? (
          <table className="compact-table">
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Strat&eacute;gie</th>
                <th>Timeframe</th>
                <th>Direction</th>
                <th>Score</th>
                <th>Entr&eacute;e</th>
                <th>Target</th>
                <th>Stop</th>
              </tr>
            </thead>
            <tbody>
              {setups.slice(0, 50).map((s, i) => (
                <tr key={i}>
                  <td style={{ fontWeight: 600 }}>{s.ticker}</td>
                  <td>{s.strategy}</td>
                  <td>{s.timeframe}</td>
                  <td style={{ color: DIR_COLORS[s.direction] }}>
                    {DIR_ARROWS[s.direction] || "\u2022"} {s.direction}
                  </td>
                  <td style={{ fontWeight: 600, color: "var(--cyan)" }}>{s.score}</td>
                  <td>{s.entry_price != null ? s.entry_price.toFixed(2) : "\u2014"}</td>
                  <td style={{ color: "var(--green)" }}>{s.target_price != null ? s.target_price.toFixed(2) : "\u2014"}</td>
                  <td style={{ color: "var(--red)" }}>{s.stop_price != null ? s.stop_price.toFixed(2) : "\u2014"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          !loading && <div className="trend-no-data">Aucun setup d&eacute;tect&eacute; &mdash; l'agent sera aliment&eacute; au prochain scan</div>
        )}
      </div>

      {/* Logs */}
      <div className="section-card">
        <h3>Logs Agent Scoring 3</h3>
        <div className="log-filter-row">
          {["ALL", "DECISION", "INFO", "WARN", "ERROR"].map((level) => (
            <button key={level} className={`log-filter-btn ${logFilter === level ? "active" : ""}`}
              onClick={() => setLogFilter(level)}>
              {level}
            </button>
          ))}
        </div>
        <div className="agent-logs-list">
          {logs.slice(0, 20).map((log, i) => (
            <div key={i} className="agent-log-entry" style={{ borderLeftColor: LEVEL_COLORS[log.level] }}>
              <div className="agent-log-header">
                <span>{LEVEL_ICONS[log.level] || ""} {log.action}</span>
                <span className="agent-log-time">
                  {new Date(log.timestamp).toLocaleString("fr-FR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}
                </span>
              </div>
              {log.details && (
                <div className="agent-log-details">
                  {typeof log.details === "object" ? JSON.stringify(log.details) : log.details}
                </div>
              )}
            </div>
          ))}
          {logs.length === 0 && <div className="trend-no-data">Aucun log</div>}
        </div>
      </div>
    </div>
  );
}
