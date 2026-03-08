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

export default function Learning3Page({ isActive }) {
  const [data, setData] = useState(null);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const [adjRes, logRes] = await Promise.all([
        fetch("/api/learning3/adjustments").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch(`/api/agents/learning_3/logs?limit=30${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`)
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
      await fetch("/api/learning3/trigger", { method: "POST" });
      setTimeout(fetchData, 2000);
    } catch { /* ignore */ } finally {
      setTriggering(false);
    }
  };

  const strategyAdj = data?.strategy_adj || {};
  const tickerAdj = data?.ticker_adj || {};
  const timeframeAdj = data?.timeframe_adj || {};
  const regimeAdj = data?.regime_adj || {};
  const abResults = data?.ab_test_results || {};
  const anomalies = data?.anomalies || [];
  const stats = data?.stats || {};

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>{"\ud83d\udca1"} Agent Learning 3 &mdash; Apprentissage Technique</h2>
        <span className="agent-page-desc">
          Apprentissage adaptatif pour Trader 3 &mdash; 5 dimensions (strat&eacute;gie, ticker, timeframe, r&eacute;gime, A/B tests)
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
          <div className="kpi-value">{stats.strategies_tracked || 0}</div>
          <div className="kpi-label">Strat&eacute;gies suivies</div>
        </div>
      </div>

      {/* Trigger button */}
      <div style={{ marginBottom: 16 }}>
        <button className="trigger-btn" onClick={triggerLearning} disabled={triggering}>
          {triggering ? "En cours..." : "Recalculer Learning 3"}
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

      {/* Per-strategy adjustments */}
      <div className="section-card">
        <h3>Ajustements par strat&eacute;gie</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          Multiplieur appliqu&eacute; &agrave; chaque strat&eacute;gie technique (1.0 = neutre)
        </p>
        {Object.keys(strategyAdj).length > 0 ? (
          Object.entries(strategyAdj).map(([strategy, val]) => (
            <AdjBar key={strategy} label={strategy} value={val} />
          ))
        ) : (
          <div className="trend-no-data">Pas assez de donn&eacute;es (min trades par strat&eacute;gie)</div>
        )}
      </div>

      {/* Per-ticker adjustments */}
      <div className="section-card">
        <h3>Ajustements par ticker</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          Quels actifs r&eacute;pondent bien aux signaux techniques ?
        </p>
        {Object.keys(tickerAdj).length > 0 ? (
          Object.entries(tickerAdj).map(([ticker, val]) => (
            <AdjBar key={ticker} label={ticker} value={val} />
          ))
        ) : (
          <div className="trend-no-data">Pas assez de donn&eacute;es (min trades par ticker)</div>
        )}
      </div>

      {/* Per-timeframe adjustments */}
      <div className="section-card">
        <h3>Ajustements par timeframe</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          Quel timeframe technique produit les meilleurs signaux ?
        </p>
        {Object.keys(timeframeAdj).length > 0 ? (
          Object.entries(timeframeAdj).map(([tf, val]) => (
            <AdjBar key={tf} label={tf} value={val} />
          ))
        ) : (
          <div className="trend-no-data">Pas assez de donn&eacute;es (min trades par timeframe)</div>
        )}
      </div>

      {/* Per-regime adjustments */}
      <div className="section-card">
        <h3>Ajustements par r&eacute;gime de march&eacute;</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          Low-vol vs high-vol &mdash; quel r&eacute;gime favorise les setups techniques ?
        </p>
        {Object.keys(regimeAdj).length > 0 ? (
          Object.entries(regimeAdj).map(([regime, val]) => (
            <AdjBar key={regime} label={regime} value={val} />
          ))
        ) : (
          <div className="trend-no-data">Pas assez de donn&eacute;es (min trades par r&eacute;gime)</div>
        )}
      </div>

      {/* A/B test results */}
      <div className="section-card">
        <h3>R&eacute;sultats A/B Testing</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          Comparaison des strat&eacute;gies &mdash; la gagnante est boost&eacute;e, la perdante p&eacute;nalis&eacute;e
        </p>
        {Object.keys(abResults).length > 0 ? (
          <table className="compact-table">
            <thead>
              <tr>
                <th>Test</th>
                <th>Strat&eacute;gie A</th>
                <th>Strat&eacute;gie B</th>
                <th>Gagnant</th>
                <th>Diff. WR</th>
                <th>Confiance</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(abResults).map(([testId, r]) => (
                <tr key={testId}>
                  <td>{testId}</td>
                  <td>{r.strategy_a || "\u2014"}</td>
                  <td>{r.strategy_b || "\u2014"}</td>
                  <td style={{ fontWeight: 600, color: "var(--green)" }}>{r.winner || "\u2014"}</td>
                  <td>{r.wr_diff != null ? `${r.wr_diff.toFixed(1)}pp` : "\u2014"}</td>
                  <td>{r.confidence != null ? `${r.confidence.toFixed(0)}%` : "\u2014"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="trend-no-data">Aucun test A/B termin&eacute; &mdash; n&eacute;cessite suffisamment de trades par strat&eacute;gie</div>
        )}
      </div>

      {/* Logs */}
      <div className="section-card">
        <h3>Logs Agent Learning 3</h3>
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
