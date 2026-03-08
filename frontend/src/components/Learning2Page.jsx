import React, { useState, useEffect, useCallback } from "react";

const LEVEL_ICONS = { INFO: "\u2139\ufe0f", WARN: "\u26a0\ufe0f", ERROR: "\u274c", DECISION: "\u26a1" };
const LEVEL_COLORS = { INFO: "var(--text-secondary)", WARN: "var(--yellow)", ERROR: "var(--red)", DECISION: "var(--cyan)" };

function AdjBar({ value, label }) {
  const pct = Math.max(0, Math.min(100, ((value - 0.5) / 1.0) * 100));
  const color = value >= 1.0 ? "var(--green)" : "var(--red)";
  return (
    <div className="learning-adj-row">
      <span className="learning-adj-label">{label}</span>
      <div className="learning-adj-bar-track">
        <div className="learning-adj-bar-fill" style={{ width: `${pct}%`, background: color }} />
        <span className="learning-adj-bar-value">{value.toFixed(3)}</span>
      </div>
    </div>
  );
}

export default function Learning2Page({ isActive }) {
  const [data, setData] = useState(null);
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const [adjRes, logRes] = await Promise.all([
        fetch("/api/learning2/adjustments").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch("/api/agents/learning_2/logs?limit=30")
          .then((r) => r.ok ? r.json() : []).catch(() => []),
      ]);
      setData(adjRes);
      setLogs(Array.isArray(logRes) ? logRes : []);
    } catch { /* ignore */ } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isActive) fetchData();
    const id = setInterval(fetchData, 60_000);
    return () => clearInterval(id);
  }, [isActive, fetchData]);

  const triggerLearning = async () => {
    setTriggering(true);
    try {
      await fetch("/api/learning2/trigger", { method: "POST" });
      setTimeout(fetchData, 2000);
    } catch { /* ignore */ } finally {
      setTriggering(false);
    }
  };

  const tickerAdj = data?.ticker_adj || {};
  const newscatAdj = data?.newscat_adj || {};
  const directionAdj = data?.direction_adj || {};
  const signalCal = data?.signal_calibration || {};
  const anomalies = data?.anomalies || [];
  const stats = data?.stats || {};

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>{"\ud83d\udca1"} Agent Learning 2 &mdash; Trend Learning</h2>
        <span className="agent-page-desc">
          Apprentissage adaptatif pour Trader 2 &mdash; 4 dimensions (ticker, newscat, direction, seuil)
        </span>
      </div>

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement...</div>}

      {/* Stats KPIs */}
      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-value">{stats.total_periods || 0}</div>
          <div className="kpi-label">P&eacute;riodes analys&eacute;es</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{stats.win_rate || 0}%</div>
          <div className="kpi-label">Win Rate</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value" style={{ color: (stats.avg_pnl_pct || 0) >= 0 ? "var(--green)" : "var(--red)" }}>
            {(stats.avg_pnl_pct || 0) >= 0 ? "+" : ""}{(stats.avg_pnl_pct || 0).toFixed(2)}%
          </div>
          <div className="kpi-label">P&L Moyen</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{signalCal.threshold_adj ? signalCal.threshold_adj.toFixed(2) : "1.00"}</div>
          <div className="kpi-label">Seuil Adj.</div>
        </div>
      </div>

      {/* Trigger button */}
      <div style={{ marginBottom: 16 }}>
        <button className="trigger-btn" onClick={triggerLearning} disabled={triggering}>
          {triggering ? "En cours..." : "Recalculer Learning 2"}
        </button>
      </div>

      {/* Anomalies */}
      {anomalies.length > 0 && (
        <div className="section-card" style={{ borderLeft: "3px solid var(--yellow)" }}>
          <h3>{"\u26a0\ufe0f"} Anomalies d&eacute;tect&eacute;es</h3>
          {anomalies.map((a, i) => (
            <div key={i} style={{ padding: "4px 0", fontSize: 13 }}>{a}</div>
          ))}
        </div>
      )}

      {/* Per-ticker adjustments */}
      <div className="section-card">
        <h3>Ajustements par ticker</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          Multiplieur appliqu&eacute; au signal de chaque ticker (1.0 = neutre)
        </p>
        {Object.keys(tickerAdj).length > 0 ? (
          Object.entries(tickerAdj).map(([ticker, val]) => (
            <AdjBar key={ticker} label={ticker} value={val} />
          ))
        ) : (
          <div className="trend-no-data">Pas assez de donn&eacute;es (min {4} flips par ticker)</div>
        )}
      </div>

      {/* Per-newscat adjustments */}
      <div className="section-card">
        <h3>Ajustements par cat&eacute;gorie de news</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          Quelles cat&eacute;gories produisent de bons retournements ?
        </p>
        {Object.keys(newscatAdj).length > 0 ? (
          Object.entries(newscatAdj).map(([cat, val]) => (
            <AdjBar key={cat} label={cat} value={val} />
          ))
        ) : (
          <div className="trend-no-data">Pas assez de donn&eacute;es (min {3} flips par cat&eacute;gorie)</div>
        )}
      </div>

      {/* Per-direction adjustments */}
      <div className="section-card">
        <h3>Ajustements par direction</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          LONG vs SHORT &mdash; quelle direction performe mieux ?
        </p>
        {Object.keys(directionAdj).length > 0 ? (
          Object.entries(directionAdj).map(([dir, val]) => (
            <AdjBar key={dir} label={dir} value={val} />
          ))
        ) : (
          <div className="trend-no-data">Pas assez de donn&eacute;es (min {4} flips par direction)</div>
        )}
      </div>

      {/* Signal calibration */}
      <div className="section-card">
        <h3>Calibration du seuil de flip</h3>
        <div style={{ display: "flex", gap: 24, padding: 8 }}>
          <div>
            <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>Seuil ajust&eacute;</div>
            <div style={{ fontSize: 18, fontWeight: 600 }}>
              {(20 * (signalCal.threshold_adj || 1.0)).toFixed(1)}
            </div>
          </div>
          <div>
            <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>Force moyenne</div>
            <div style={{ fontSize: 18, fontWeight: 600 }}>
              {(signalCal.avg_strength || 0).toFixed(1)}
            </div>
          </div>
        </div>
      </div>

      {/* Agent Logs */}
      <div className="section-card">
        <h3>Logs Agent Learning 2</h3>
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
