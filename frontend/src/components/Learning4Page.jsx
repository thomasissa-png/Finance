import React, { useState, useEffect, useCallback } from "react";

const LEVEL_ICONS = { INFO: "\u2139\ufe0f", WARN: "\u26a0\ufe0f", ERROR: "\u274c", DECISION: "\u26a1" };
const LEVEL_COLORS = { INFO: "var(--text-secondary)", WARN: "var(--yellow)", ERROR: "var(--red)", DECISION: "var(--cyan)" };

const TEAM_COLORS = {
  team_1: "var(--cyan)",
  team_2: "var(--green)",
  team_3: "var(--yellow)",
};
const TEAM_LABELS = {
  team_1: "News (1)",
  team_2: "Trend (2)",
  team_3: "Tech (3)",
};

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

export default function Learning4Page({ isActive }) {
  const [data, setData] = useState(null);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const [adjRes, logRes] = await Promise.all([
        fetch("/api/learning4/adjustments").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch(`/api/agents/learning_4/logs?limit=30${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`)
          .then((r) => r.ok ? r.json() : []).catch(() => []),
      ]);
      setData(adjRes);
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

  const triggerLearning = async () => {
    setTriggering(true);
    try {
      await fetch("/api/learning4/trigger", { method: "POST" });
      setTimeout(fetchData, 2000);
    } catch { /* ignore */ } finally {
      setTriggering(false);
    }
  };

  const comboAdj = data?.team_combination_adj || {};
  const tickerAdj = data?.ticker_adj || {};
  const confluenceAdj = data?.confluence_adj || {};
  const weightOpt = data?.weight_optimization || {};
  const anomalies = data?.anomalies || [];
  const stats = data?.stats || {};
  const comboPerf = data?.combination_performance || {};

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>{"\ud83d\udca1"} Agent Learning 4 &mdash; Apprentissage Multi-Signal</h2>
        <span className="agent-page-desc">
          Apprentissage adaptatif pour Trader 4 &mdash; 4 dimensions (combinaison d'&eacute;quipes, ticker, confluence, poids)
        </span>
      </div>

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement...</div>}

      {/* Stats KPIs */}
      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-value">{stats.total_trades || 0}</div>
          <div className="kpi-label">Trades analys&eacute;s</div>
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
          <div className="kpi-value">{stats.combinations_tracked || 0}</div>
          <div className="kpi-label">Combos suiv&eacute;s</div>
        </div>
      </div>

      {/* Trigger button */}
      <div style={{ marginBottom: 16 }}>
        <button className="trigger-btn" onClick={triggerLearning} disabled={triggering}>
          {triggering ? "En cours..." : "Recalculer Learning 4"}
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

      {/* Weight optimization */}
      <div className="section-card">
        <h3>Optimisation des poids</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 12 }}>
          Poids actuels de chaque source dans le meta-score &mdash; ajust&eacute;s par le learning
        </p>
        <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
          {[
            { key: "news_weight", label: "News (Eq. 1)", team: "team_1" },
            { key: "trend_weight", label: "Trend (Eq. 2)", team: "team_2" },
            { key: "tech_weight", label: "Tech (Eq. 3)", team: "team_3" },
          ].map(({ key, label, team }) => {
            const current = weightOpt[key] || 0;
            const initial = weightOpt[`${key}_initial`] || current;
            const diff = current - initial;
            return (
              <div key={key} style={{ flex: 1, minWidth: 140 }}>
                <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4 }}>{label}</div>
                <div style={{ background: "var(--bg-tertiary)", borderRadius: 4, height: 14, position: "relative" }}>
                  <div style={{ width: `${Math.min(current * 100, 100)}%`, background: TEAM_COLORS[team], borderRadius: 4, height: "100%" }} />
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4 }}>
                  <span style={{ fontSize: 16, fontWeight: 700, color: TEAM_COLORS[team] }}>
                    {(current * 100).toFixed(1)}%
                  </span>
                  {diff !== 0 && (
                    <span style={{ fontSize: 11, color: diff > 0 ? "var(--green)" : "var(--red)" }}>
                      {diff > 0 ? "+" : ""}{(diff * 100).toFixed(1)}pp
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Per-team-combination adjustments */}
      <div className="section-card">
        <h3>Ajustements par combinaison d'&eacute;quipes</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          Quelles combinaisons produisent les meilleurs r&eacute;sultats ?
        </p>
        {Object.keys(comboAdj).length > 0 ? (
          Object.entries(comboAdj).map(([combo, val]) => (
            <AdjBar key={combo} label={combo} value={val} />
          ))
        ) : (
          <div className="trend-no-data">Pas assez de donn&eacute;es (min trades par combinaison)</div>
        )}
      </div>

      {/* Per-ticker adjustments */}
      <div className="section-card">
        <h3>Ajustements par ticker</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          Quels actifs r&eacute;pondent le mieux aux signaux confluents ?
        </p>
        {Object.keys(tickerAdj).length > 0 ? (
          Object.entries(tickerAdj).map(([ticker, val]) => (
            <AdjBar key={ticker} label={ticker} value={val} />
          ))
        ) : (
          <div className="trend-no-data">Pas assez de donn&eacute;es (min trades par ticker)</div>
        )}
      </div>

      {/* Per-confluence-level adjustments */}
      <div className="section-card">
        <h3>Ajustements par niveau de confluence</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          2/3 vs 3/3 &mdash; quelle force de confluence performe mieux ?
        </p>
        {Object.keys(confluenceAdj).length > 0 ? (
          Object.entries(confluenceAdj).map(([lvl, val]) => (
            <AdjBar key={lvl} label={`Confluence ${lvl}`} value={val} />
          ))
        ) : (
          <div className="trend-no-data">Pas assez de donn&eacute;es (min trades par niveau)</div>
        )}
      </div>

      {/* Combination performance matrix */}
      <div className="section-card">
        <h3>Matrice de performance des combinaisons</h3>
        {Object.keys(comboPerf).length > 0 ? (
          <table className="compact-table">
            <thead>
              <tr>
                <th>Combinaison</th>
                <th>Trades</th>
                <th>Win Rate</th>
                <th>P&L Total</th>
                <th>P&L Moy.</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(comboPerf).sort((a, b) => (b[1].pnl || 0) - (a[1].pnl || 0)).map(([combo, perf]) => (
                <tr key={combo}>
                  <td>
                    {combo.split("+").map((t, j) => (
                      <span key={j} className="badge" style={{
                        background: TEAM_COLORS[t] || "var(--bg-tertiary)",
                        color: "#fff", fontSize: 10, marginRight: 4,
                      }}>
                        {TEAM_LABELS[t] || t}
                      </span>
                    ))}
                  </td>
                  <td>{perf.count || 0}</td>
                  <td>{perf.win_rate != null ? `${perf.win_rate.toFixed(1)}%` : "\u2014"}</td>
                  <td style={{ color: (perf.pnl || 0) >= 0 ? "var(--green)" : "var(--red)" }}>
                    {(perf.pnl || 0) >= 0 ? "+" : ""}{(perf.pnl || 0).toFixed(2)}%
                  </td>
                  <td>{perf.avg_pnl != null ? `${perf.avg_pnl.toFixed(2)}%` : "\u2014"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="trend-no-data">Aucune donn&eacute;e de performance &mdash; n&eacute;cessite des trades cl&ocirc;tur&eacute;s par Trader 4</div>
        )}
      </div>

      {/* Logs */}
      <div className="section-card">
        <h3>Logs Agent Learning 4</h3>
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
