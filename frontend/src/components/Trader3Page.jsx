import React, { useState, useEffect, useCallback } from "react";
import { DIR_COLORS, DIR_ARROWS, POLL_FAST } from "../utils/constants";
import { apiFetch } from "../utils/api";
import { pnlColor } from "../utils/format";
import { ErrorBanner, EmptyState, LastUpdated, LogSection } from "./shared";
import StrategyPerformance from "./StrategyPerformance";

export default function Trader3Page({ isActive }) {
  const [positions, setPositions] = useState([]);
  const [learningAdj, setLearningAdj] = useState(null);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState("ALL");
  const [filterResult, setFilterResult] = useState("");
  const [filterDirection, setFilterDirection] = useState("");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const [posRes, adjRes, logRes] = await Promise.all([
        apiFetch("/api/trader3/positions", {}, []),
        apiFetch("/api/learning3/adjustments", {}, null),
        apiFetch("/api/agents/trader_3/logs?limit=50", {}, []),
      ]);
      setPositions(Array.isArray(posRes?.active) ? posRes.active : Array.isArray(posRes) ? posRes : []);
      setLearningAdj(adjRes);
      setLogs(Array.isArray(logRes) ? logRes : []);
      setError(null);
      setLastUpdate(new Date());
    } catch (err) {
      setError(err.message || "Impossible de charger les données Trader 3");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isActive) fetchData();
    const id = setInterval(fetchData, POLL_FAST);
    return () => clearInterval(id);
  }, [isActive, fetchData]);

  // KPIs
  const activePositions = positions.filter((p) => p.status === "PENDING" || p.status === "active");
  const allTrades = positions;
  const realizedPnl = allTrades.filter((t) => t.pnl_pct != null).reduce((s, t) => s + (t.pnl_pct || 0), 0);
  const latentPnl = activePositions.reduce((s, p) => s + (p.unrealized_pnl_pct || 0), 0);
  const byStrategy = {};
  allTrades.forEach((t) => { const s = t.strategy || "?"; byStrategy[s] = (byStrategy[s] || 0) + 1; });
  const topStrategy = Object.entries(byStrategy).sort((a, b) => b[1] - a[1])[0];

  // Filter history
  const history = allTrades.filter((t) => t.result && t.result !== "PENDING");
  const filteredHistory = history.filter((t) => {
    if (filterResult && t.result !== filterResult) return false;
    if (filterDirection && t.direction !== filterDirection) return false;
    return true;
  });
  const perPage = 15;
  const totalPagesCount = Math.max(1, Math.ceil(filteredHistory.length / perPage));
  const pagedHistory = filteredHistory.slice((page - 1) * perPage, page * perPage);

  const filteredLogs = logFilter === "ALL" ? logs : logs.filter((l) => l.level === logFilter);

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>Agent Trader 3 &mdash; Trading Technique (heures-3j)</h2>
        <span className="agent-page-desc">
          Positions basées sur les indicateurs techniques &mdash; stratégies multiples, durée heures à 3 jours
        </span>
        <LastUpdated date={lastUpdate} />
      </div>

      <ErrorBanner error={error} onRetry={fetchData} />

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement...</div>}

      {/* KPIs */}
      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-value">{activePositions.length}</div>
          <div className="kpi-label">Positions actives</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value" style={{ color: pnlColor(realizedPnl) }}>
            {realizedPnl >= 0 ? "+" : ""}{realizedPnl.toFixed(2)}%
          </div>
          <div className="kpi-label">P&L Réalisé</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value" style={{ color: pnlColor(latentPnl) }}>
            {latentPnl >= 0 ? "+" : ""}{latentPnl.toFixed(2)}%
          </div>
          <div className="kpi-label">P&L Latent</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{topStrategy ? `${topStrategy[0]} (${topStrategy[1]})` : "\u2014"}</div>
          <div className="kpi-label">Top stratégie</div>
        </div>
      </div>

      {/* Learning 3 context */}
      {learningAdj && learningAdj.stats && learningAdj.stats.sufficient_data && (
        <div className="section-card" style={{ padding: "12px 16px" }}>
          <h3 style={{ marginBottom: 8 }}>Learning 3 &mdash; Ajustements actifs</h3>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 13 }}>
            {Object.entries(learningAdj.strategy_adj || {}).map(([s, v]) => (
              <span key={s} style={{ color: v >= 1 ? "var(--green)" : "var(--red)" }}>
                {s}: {v.toFixed(2)}x
              </span>
            ))}
            {Object.entries(learningAdj.ticker_adj || {}).map(([t, v]) => (
              <span key={t} style={{ color: v >= 1 ? "var(--green)" : "var(--red)" }}>
                {t}: {v.toFixed(2)}x
              </span>
            ))}
          </div>
          {(learningAdj.anomalies || []).length > 0 && (
            <div style={{ marginTop: 6, fontSize: 12, color: "var(--yellow)" }}>
              ! {learningAdj.anomalies.join(" | ")}
            </div>
          )}
        </div>
      )}

      {/* Active positions */}
      <div className="section-card">
        <h3>Positions actives ({activePositions.length})</h3>
        {activePositions.length > 0 ? (
          <table className="compact-table">
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Stratégie</th>
                <th>Direction</th>
                <th>Entrée</th>
                <th>Actuel</th>
                <th>P&L %</th>
                <th>Durée</th>
              </tr>
            </thead>
            <tbody>
              {activePositions.map((p, i) => {
                const holdingMs = p.entry_time ? Date.now() - new Date(p.entry_time).getTime() : 0;
                const holdingHours = (holdingMs / 3600000).toFixed(1);
                return (
                  <tr key={i}>
                    <td style={{ fontWeight: 600 }}>{p.ticker}</td>
                    <td>{p.strategy || "\u2014"}</td>
                    <td style={{ color: DIR_COLORS[p.direction] }}>
                      {DIR_ARROWS[p.direction] || "\u2022"} {p.direction}
                    </td>
                    <td>{p.entry_price != null ? p.entry_price.toFixed(2) : "\u2014"}</td>
                    <td>{p.current_price != null ? p.current_price.toFixed(2) : "\u2014"}</td>
                    <td style={{ color: pnlColor(p.unrealized_pnl_pct) }}>
                      {(p.unrealized_pnl_pct || 0) >= 0 ? "+" : ""}{(p.unrealized_pnl_pct || 0).toFixed(2)}%
                    </td>
                    <td>{holdingHours}h</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          !loading && <EmptyState message="Aucune position active" />
        )}
      </div>

      {/* Strategy Performance — full drill-down */}
      <StrategyPerformance isActive={isActive} />

      {/* Trade history */}
      <div className="section-card">
        <h3>Historique des trades</h3>
        <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
          <select value={filterResult} onChange={(e) => { setFilterResult(e.target.value); setPage(1); }}
            style={{ background: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border)", borderRadius: 4, padding: "4px 8px", fontSize: 12 }}>
            <option value="">Tous résultats</option>
            <option value="TP_HIT">TP_HIT</option>
            <option value="SL_HIT">SL_HIT</option>
            <option value="EXPIRED">EXPIRED</option>
          </select>
          <select value={filterDirection} onChange={(e) => { setFilterDirection(e.target.value); setPage(1); }}
            style={{ background: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border)", borderRadius: 4, padding: "4px 8px", fontSize: 12 }}>
            <option value="">Toutes directions</option>
            <option value="LONG">LONG</option>
            <option value="SHORT">SHORT</option>
          </select>
        </div>
        {pagedHistory.length > 0 ? (
          <>
            <table className="compact-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Ticker</th>
                  <th>Stratégie</th>
                  <th>Dir.</th>
                  <th>Résultat</th>
                  <th>P&L</th>
                </tr>
              </thead>
              <tbody>
                {pagedHistory.map((t, i) => (
                  <tr key={i}>
                    <td>{t.timestamp ? new Date(t.timestamp).toLocaleDateString("fr-FR", { day: "2-digit", month: "short" }) : "\u2014"}</td>
                    <td style={{ fontWeight: 600 }}>{t.ticker}</td>
                    <td>{t.strategy || "\u2014"}</td>
                    <td style={{ color: DIR_COLORS[t.direction] }}>{t.direction}</td>
                    <td>{t.result}</td>
                    <td style={{ color: pnlColor(t.pnl_pct) }}>
                      {(t.pnl_pct || 0) >= 0 ? "+" : ""}{(t.pnl_pct || 0).toFixed(2)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {totalPagesCount > 1 && (
              <div style={{ display: "flex", gap: 8, justifyContent: "center", marginTop: 8 }}>
                <button disabled={page <= 1} onClick={() => setPage(page - 1)} className="log-filter-btn">&laquo;</button>
                <span style={{ fontSize: 12, lineHeight: "28px" }}>{page}/{totalPagesCount}</span>
                <button disabled={page >= totalPagesCount} onClick={() => setPage(page + 1)} className="log-filter-btn">&raquo;</button>
              </div>
            )}
          </>
        ) : (
          !loading && <EmptyState message="Aucun trade dans l'historique" />
        )}
      </div>

      {/* Logs */}
      <LogSection logs={filteredLogs} logFilter={logFilter} setLogFilter={setLogFilter} />
    </div>
  );
}
