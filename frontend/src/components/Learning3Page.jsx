import React, { useState, useEffect, useCallback } from "react";
import { POLL_NORMAL } from "../utils/constants";
import { apiFetch, apiTrigger } from "../utils/api";
import { pnlColor } from "../utils/format";
import { ErrorBanner, EmptyState, LastUpdated, AdjBar, LogSection } from "./shared";

export default function Learning3Page({ isActive }) {
  const [data, setData] = useState(null);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [error, setError] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const [adjRes, logRes] = await Promise.all([
        apiFetch("/api/learning3/adjustments", {}, null),
        apiFetch("/api/agents/learning_3/logs?limit=30", {}, []),
      ]);
      setData(adjRes);
      setLogs(Array.isArray(logRes) ? logRes : []);
      setError(null);
      setLastUpdate(new Date());
    } catch (err) {
      setError(err.message || "Impossible de charger les données Learning 3");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isActive) fetchData();
    const id = setInterval(fetchData, POLL_NORMAL);
    return () => clearInterval(id);
  }, [isActive, fetchData]);

  const triggerLearning = async () => {
    setTriggering(true);
    const result = await apiTrigger("/api/learning3/trigger");
    if (!result.ok) setError(result.error);
    setTimeout(fetchData, 2000);
    setTriggering(false);
  };

  const strategyAdj = data?.strategy_adj || {};
  const tickerAdj = data?.ticker_adj || {};
  const timeframeAdj = data?.timeframe_adj || {};
  const regimeAdj = data?.regime_adj || {};
  const abResults = data?.ab_test_results || {};
  const anomalies = data?.anomalies || [];
  const stats = data?.stats || {};

  const filteredLogs = logFilter === "ALL" ? logs : logs.filter((l) => l.level === logFilter);

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>Agent Learning 3 &mdash; Apprentissage Technique</h2>
        <span className="agent-page-desc">
          Apprentissage adaptatif pour Trader 3 &mdash; 5 dimensions (stratégie, ticker, timeframe, régime, A/B tests)
        </span>
        <LastUpdated date={lastUpdate} />
      </div>

      <ErrorBanner error={error} onRetry={fetchData} />

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement...</div>}

      {/* Stats KPIs */}
      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-value">{stats.total_trades || 0}</div>
          <div className="kpi-label">Trades analysés</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{stats.win_rate || 0}%</div>
          <div className="kpi-label">Win Rate</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value" style={{ color: pnlColor(stats.avg_pnl_pct) }}>
            {(stats.avg_pnl_pct || 0) >= 0 ? "+" : ""}{(stats.avg_pnl_pct || 0).toFixed(2)}%
          </div>
          <div className="kpi-label">P&L Moyen</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{stats.strategies_tracked || 0}</div>
          <div className="kpi-label">Stratégies suivies</div>
        </div>
      </div>

      {/* Trigger */}
      <div style={{ marginBottom: 16 }}>
        <button className="trigger-btn" onClick={triggerLearning} disabled={triggering}>
          {triggering ? "En cours..." : "Recalculer Learning 3"}
        </button>
      </div>

      {/* Anomalies */}
      {anomalies.length > 0 && (
        <div className="section-card" style={{ borderLeft: "3px solid var(--yellow)" }}>
          <h3>! Anomalies détectées</h3>
          {anomalies.map((a, i) => (
            <div key={i} style={{ padding: "4px 0", fontSize: 13 }}>{a}</div>
          ))}
        </div>
      )}

      {/* Per-strategy */}
      <div className="section-card">
        <h3>Ajustements par stratégie</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          Multiplieur appliqué à chaque stratégie technique (1.0 = neutre)
        </p>
        {Object.keys(strategyAdj).length > 0 ? (
          Object.entries(strategyAdj).map(([strategy, val]) => (
            <AdjBar key={strategy} label={strategy} value={val} />
          ))
        ) : (
          <EmptyState message="Pas assez de données" detail="Min trades par stratégie requis" />
        )}
      </div>

      {/* Per-ticker */}
      <div className="section-card">
        <h3>Ajustements par ticker</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          Quels actifs répondent bien aux signaux techniques ?
        </p>
        {Object.keys(tickerAdj).length > 0 ? (
          Object.entries(tickerAdj).map(([ticker, val]) => (
            <AdjBar key={ticker} label={ticker} value={val} />
          ))
        ) : (
          <EmptyState message="Pas assez de données" detail="Min trades par ticker requis" />
        )}
      </div>

      {/* Per-timeframe */}
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
          <EmptyState message="Pas assez de données" detail="Min trades par timeframe requis" />
        )}
      </div>

      {/* Per-regime */}
      <div className="section-card">
        <h3>Ajustements par régime de marché</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          Low-vol vs high-vol &mdash; quel régime favorise les setups techniques ?
        </p>
        {Object.keys(regimeAdj).length > 0 ? (
          Object.entries(regimeAdj).map(([regime, val]) => (
            <AdjBar key={regime} label={regime} value={val} />
          ))
        ) : (
          <EmptyState message="Pas assez de données" detail="Min trades par régime requis" />
        )}
      </div>

      {/* A/B test results */}
      <div className="section-card">
        <h3>Résultats A/B Testing</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          Comparaison des stratégies &mdash; la gagnante est boostée, la perdante pénalisée
        </p>
        {Object.keys(abResults).length > 0 ? (
          <table className="compact-table">
            <thead>
              <tr>
                <th>Test</th>
                <th>Stratégie A</th>
                <th>Stratégie B</th>
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
          <EmptyState message="Aucun test A/B terminé" detail="Nécessite suffisamment de trades par stratégie" />
        )}
      </div>

      {/* Logs */}
      <LogSection logs={filteredLogs} logFilter={logFilter} setLogFilter={setLogFilter} />
    </div>
  );
}
