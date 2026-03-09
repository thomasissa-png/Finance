import React, { useState, useEffect, useCallback } from "react";
import { POLL_NORMAL, T4_TEAM_COLORS, T4_TEAM_LABELS } from "../utils/constants";
import { apiFetch, apiTrigger } from "../utils/api";
import { pnlColor } from "../utils/format";
import { ErrorBanner, EmptyState, LastUpdated, AdjBar, LogSection } from "./shared";

export default function Learning4Page({ isActive }) {
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
        apiFetch("/api/learning4/adjustments", {}, null),
        apiFetch("/api/agents/learning_4/logs?limit=30", {}, []),
      ]);
      setData(adjRes);
      setLogs(Array.isArray(logRes) ? logRes : []);
      setError(null);
      setLastUpdate(new Date());
    } catch (err) {
      setError(err.message || "Impossible de charger les données Learning 4");
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
    const result = await apiTrigger("/api/learning4/trigger");
    if (!result.ok) setError(result.error);
    setTimeout(fetchData, 2000);
    setTriggering(false);
  };

  const comboAdj = data?.team_combination_adj || {};
  const tickerAdj = data?.ticker_adj || {};
  const confluenceAdj = data?.confluence_adj || {};
  const weightOpt = data?.weight_optimization || {};
  const anomalies = data?.anomalies || [];
  const stats = data?.stats || {};
  const comboPerf = data?.combination_performance || {};

  const filteredLogs = logFilter === "ALL" ? logs : logs.filter((l) => l.level === logFilter);

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>Agent Learning 4 &mdash; Apprentissage Multi-Signal</h2>
        <span className="agent-page-desc">
          Apprentissage adaptatif pour Trader 4 &mdash; 4 dimensions (combinaison d'&eacute;quipes, ticker, confluence, poids)
        </span>
        <LastUpdated date={lastUpdate} />
      </div>

      <ErrorBanner error={error} onRetry={fetchData} />

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
          <div className="kpi-value" style={{ color: pnlColor(stats.avg_pnl_pct) }}>
            {(stats.avg_pnl_pct || 0) >= 0 ? "+" : ""}{(stats.avg_pnl_pct || 0).toFixed(2)}%
          </div>
          <div className="kpi-label">P&L Moyen</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{stats.combinations_tracked || 0}</div>
          <div className="kpi-label">Combos suiv&eacute;s</div>
        </div>
      </div>

      {/* Trigger */}
      <div style={{ marginBottom: 16 }}>
        <button className="trigger-btn" onClick={triggerLearning} disabled={triggering}>
          {triggering ? "En cours..." : "Recalculer Learning 4"}
        </button>
      </div>

      {/* Anomalies */}
      {anomalies.length > 0 && (
        <div className="section-card" style={{ borderLeft: "3px solid var(--yellow)" }}>
          <h3>! Anomalies d&eacute;tect&eacute;es</h3>
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
                  <div style={{ width: `${Math.min(current * 100, 100)}%`, background: T4_TEAM_COLORS[team], borderRadius: 4, height: "100%" }} />
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4 }}>
                  <span style={{ fontSize: 16, fontWeight: 700, color: T4_TEAM_COLORS[team] }}>
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

      {/* Per-combination */}
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
          <EmptyState message="Pas assez de données" detail="Min trades par combinaison requis" />
        )}
      </div>

      {/* Per-ticker */}
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
          <EmptyState message="Pas assez de données" detail="Min trades par ticker requis" />
        )}
      </div>

      {/* Per-confluence-level */}
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
          <EmptyState message="Pas assez de données" detail="Min trades par niveau requis" />
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
                        background: T4_TEAM_COLORS[t] || "var(--bg-tertiary)",
                        color: "#fff", fontSize: 10, marginRight: 4,
                      }}>
                        {T4_TEAM_LABELS[t] || t}
                      </span>
                    ))}
                  </td>
                  <td>{perf.count || 0}</td>
                  <td>{perf.win_rate != null ? `${perf.win_rate.toFixed(1)}%` : "\u2014"}</td>
                  <td style={{ color: pnlColor(perf.pnl) }}>
                    {(perf.pnl || 0) >= 0 ? "+" : ""}{(perf.pnl || 0).toFixed(2)}%
                  </td>
                  <td>{perf.avg_pnl != null ? `${perf.avg_pnl.toFixed(2)}%` : "\u2014"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <EmptyState message="Aucune donnée de performance" detail="Nécessite des trades clôturés par Trader 4" />
        )}
      </div>

      {/* Logs */}
      <LogSection logs={filteredLogs} logFilter={logFilter} setLogFilter={setLogFilter} />
    </div>
  );
}
