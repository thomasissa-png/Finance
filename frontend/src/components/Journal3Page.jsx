import React, { useState, useEffect, useCallback } from "react";
import { DIR_COLORS, POLL_NORMAL } from "../utils/constants";
import { apiFetch, apiTrigger } from "../utils/api";
import { pnlColor } from "../utils/format";
import { ErrorBanner, EmptyState, LastUpdated, LogSection } from "./shared";

export default function Journal3Page({ isActive }) {
  const [entries, setEntries] = useState([]);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [error, setError] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const [entriesRes, logRes] = await Promise.all([
        apiFetch("/api/journal3/entries", {}, []),
        apiFetch("/api/agents/journal_3/logs?limit=30", {}, []),
      ]);
      setEntries(Array.isArray(entriesRes) ? entriesRes : []);
      setLogs(Array.isArray(logRes) ? logRes : []);
      setError(null);
      setLastUpdate(new Date());
    } catch (err) {
      setError(err.message || "Impossible de charger les données Journal 3");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isActive) fetchData();
    const id = setInterval(fetchData, POLL_NORMAL);
    return () => clearInterval(id);
  }, [isActive, fetchData]);

  const triggerJournal = async () => {
    setTriggering(true);
    const result = await apiTrigger("/api/journal3/trigger");
    if (!result.ok) setError(result.error);
    setTimeout(fetchData, 2000);
    setTriggering(false);
  };

  // KPIs
  const totalTrades = entries.length;
  const wins = entries.filter((e) => (e.pnl_pct || 0) > 0).length;
  const winRate = totalTrades > 0 ? ((wins / totalTrades) * 100).toFixed(1) : "\u2014";
  const totalPnl = entries.reduce((s, e) => s + (e.pnl_pct || 0), 0);
  const avgMae = totalTrades > 0
    ? (entries.reduce((s, e) => s + (e.mae_pct || 0), 0) / totalTrades).toFixed(2)
    : "\u2014";

  // Group by strategy
  const byStrategy = {};
  entries.forEach((e) => {
    const s = e.strategy || "inconnu";
    if (!byStrategy[s]) byStrategy[s] = [];
    byStrategy[s].push(e);
  });

  const filteredLogs = logFilter === "ALL" ? logs : logs.filter((l) => l.level === logFilter);

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>Agent Journal 3 &mdash; Journal Technique</h2>
        <span className="agent-page-desc">
          Journal des trades techniques (Trader 3) &mdash; clôture, P&L, MAE/MFE par stratégie
        </span>
        <LastUpdated date={lastUpdate} />
      </div>

      <ErrorBanner error={error} onRetry={fetchData} />

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement...</div>}

      {/* KPIs */}
      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-value">{totalTrades}</div>
          <div className="kpi-label">Trades fermés</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{winRate}%</div>
          <div className="kpi-label">Win Rate</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value" style={{ color: pnlColor(totalPnl) }}>
            {totalPnl >= 0 ? "+" : ""}{totalPnl.toFixed(2)}%
          </div>
          <div className="kpi-label">P&L Total</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{avgMae}%</div>
          <div className="kpi-label">MAE Moy.</div>
        </div>
      </div>

      {/* Trigger */}
      <div style={{ marginBottom: 16 }}>
        <button className="trigger-btn" onClick={triggerJournal} disabled={triggering}>
          {triggering ? "En cours..." : "Lancer Journal 3"}
        </button>
      </div>

      {/* Performance par strategie */}
      <div className="section-card">
        <h3>Performance par stratégie</h3>
        {Object.keys(byStrategy).length > 0 ? (
          <table className="compact-table">
            <thead>
              <tr>
                <th>Stratégie</th>
                <th>Trades</th>
                <th>Win Rate</th>
                <th>P&L Total</th>
                <th>P&L Moy.</th>
                <th>MAE Moy.</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(byStrategy).map(([strategy, sEntries]) => {
                const sWins = sEntries.filter((e) => (e.pnl_pct || 0) > 0).length;
                const sWR = sEntries.length > 0 ? ((sWins / sEntries.length) * 100).toFixed(1) : "\u2014";
                const sPnl = sEntries.reduce((s, e) => s + (e.pnl_pct || 0), 0);
                const sAvg = sEntries.length > 0 ? (sPnl / sEntries.length).toFixed(2) : "\u2014";
                const sMae = sEntries.length > 0
                  ? (sEntries.reduce((s, e) => s + (e.mae_pct || 0), 0) / sEntries.length).toFixed(2)
                  : "\u2014";
                return (
                  <tr key={strategy}>
                    <td style={{ fontWeight: 600 }}>{strategy}</td>
                    <td>{sEntries.length}</td>
                    <td>{sWR}%</td>
                    <td style={{ color: pnlColor(sPnl) }}>
                      {sPnl >= 0 ? "+" : ""}{sPnl.toFixed(2)}%
                    </td>
                    <td>{sAvg}%</td>
                    <td>{sMae}%</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          !loading && <EmptyState message="Aucune donnée par stratégie" />
        )}
      </div>

      {/* Recent entries */}
      <div className="section-card">
        <h3>Entrées récentes ({totalTrades})</h3>
        {entries.length > 0 ? (
          <table className="compact-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Ticker</th>
                <th>Stratégie</th>
                <th>Direction</th>
                <th>Résultat</th>
                <th>P&L</th>
                <th>MAE</th>
                <th>MFE</th>
              </tr>
            </thead>
            <tbody>
              {entries.slice(0, 30).map((e, i) => (
                <tr key={i}>
                  <td>{e.entry_time ? new Date(e.entry_time).toLocaleDateString("fr-FR", { day: "2-digit", month: "short" }) : "\u2014"}</td>
                  <td style={{ fontWeight: 600 }}>{e.ticker}</td>
                  <td>{e.strategy || "\u2014"}</td>
                  <td style={{ color: DIR_COLORS[e.direction] }}>{e.direction}</td>
                  <td>{e.result || "\u2014"}</td>
                  <td style={{ color: pnlColor(e.pnl_pct) }}>
                    {(e.pnl_pct || 0) >= 0 ? "+" : ""}{(e.pnl_pct || 0).toFixed(2)}%
                  </td>
                  <td>{(e.mae_pct || 0).toFixed(2)}%</td>
                  <td>{(e.mfe_pct || 0).toFixed(2)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          !loading && <EmptyState message="Aucun trade enregistré" detail="Le journal sera alimenté après les premières clôtures" />
        )}
      </div>

      {/* Logs */}
      <LogSection logs={filteredLogs} logFilter={logFilter} setLogFilter={setLogFilter} />
    </div>
  );
}
