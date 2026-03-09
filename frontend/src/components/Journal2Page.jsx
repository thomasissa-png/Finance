import React, { useState, useEffect, useCallback } from "react";
import { DIR_COLORS, POLL_NORMAL } from "../utils/constants";
import { apiFetch, apiTrigger } from "../utils/api";
import { pnlColor } from "../utils/format";
import { ErrorBanner, EmptyState, LastUpdated, LogSection } from "./shared";

export default function Journal2Page({ isActive }) {
  const [entries, setEntries] = useState([]);
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [error, setError] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [logFilter, setLogFilter] = useState("ALL");

  const fetchData = useCallback(async () => {
    try {
      const [entriesRes, logRes] = await Promise.all([
        apiFetch("/api/journal2/entries", {}, []),
        apiFetch("/api/agents/journal_2/logs?limit=30", {}, []),
      ]);
      setEntries(Array.isArray(entriesRes) ? entriesRes : []);
      setLogs(Array.isArray(logRes) ? logRes : []);
      setError(null);
      setLastUpdate(new Date());
    } catch (err) {
      setError(err.message || "Impossible de charger les données Journal 2");
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
    const result = await apiTrigger("/api/journal2/trigger");
    if (!result.ok) setError(result.error);
    setTimeout(fetchData, 2000);
    setTriggering(false);
  };

  // KPIs
  const totalFlips = entries.length;
  const wins = entries.filter((e) => (e.pnl_pct || 0) > 0).length;
  const winRate = totalFlips > 0 ? ((wins / totalFlips) * 100).toFixed(1) : "\u2014";
  const totalPnl = entries.reduce((s, e) => s + (e.pnl_pct || 0), 0);
  const avgMae = totalFlips > 0
    ? (entries.reduce((s, e) => s + (e.mae_pct || 0), 0) / totalFlips).toFixed(2)
    : "\u2014";

  // Group by ticker
  const byTicker = {};
  entries.forEach((e) => {
    const t = e.ticker || "?";
    if (!byTicker[t]) byTicker[t] = [];
    byTicker[t].push(e);
  });

  const filteredLogs = logFilter === "ALL" ? logs : logs.filter((l) => l.level === logFilter);

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>Agent Journal 2 &mdash; Trend Positions</h2>
        <span className="agent-page-desc">
          Journal des positions de tendance (Trader 2) &mdash; flips, MAE/MFE, P&L par p&eacute;riode
        </span>
        <LastUpdated date={lastUpdate} />
      </div>

      <ErrorBanner error={error} onRetry={fetchData} />

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement...</div>}

      {/* KPIs */}
      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-value">{totalFlips}</div>
          <div className="kpi-label">P&eacute;riodes cl&ocirc;tur&eacute;es</div>
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

      {/* Trigger button */}
      <div style={{ marginBottom: 16 }}>
        <button className="trigger-btn" onClick={triggerJournal} disabled={triggering}>
          {triggering ? "En cours..." : "Lancer Journal 2"}
        </button>
      </div>

      {/* Entries by ticker */}
      <div className="section-card">
        <h3>Historique des flips par ticker</h3>
        {Object.entries(byTicker).map(([ticker, tickerEntries]) => {
          const tickerPnl = tickerEntries.reduce((s, e) => s + (e.pnl_pct || 0), 0);
          const tickerWins = tickerEntries.filter((e) => (e.pnl_pct || 0) > 0).length;
          return (
            <div key={ticker} style={{ marginBottom: 16 }}>
              <h4 style={{ display: "flex", gap: 12, alignItems: "center" }}>
                <span>{ticker}</span>
                <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                  {tickerEntries.length} flips &mdash; WR {tickerEntries.length > 0 ? ((tickerWins / tickerEntries.length) * 100).toFixed(0) : 0}%
                  &mdash; P&L {tickerPnl >= 0 ? "+" : ""}{tickerPnl.toFixed(2)}%
                </span>
              </h4>
              <table className="compact-table">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Direction</th>
                    <th>P&L</th>
                    <th>MAE</th>
                    <th>MFE</th>
                    <th>Signal</th>
                    <th>Cat&eacute;gories</th>
                  </tr>
                </thead>
                <tbody>
                  {tickerEntries.slice(0, 20).map((e, i) => (
                    <tr key={i}>
                      <td>{e.entry_time ? new Date(e.entry_time).toLocaleDateString("fr-FR", { day: "2-digit", month: "short" }) : "\u2014"}</td>
                      <td style={{ color: DIR_COLORS[e.direction] }}>{e.direction}</td>
                      <td style={{ color: pnlColor(e.pnl_pct) }}>
                        {(e.pnl_pct || 0) >= 0 ? "+" : ""}{(e.pnl_pct || 0).toFixed(2)}%
                      </td>
                      <td>{(e.mae_pct || 0).toFixed(2)}%</td>
                      <td>{(e.mfe_pct || 0).toFixed(2)}%</td>
                      <td>{(e.signal_strength || 0).toFixed(0)}</td>
                      <td>
                        {(e.news_categories || []).map((c, j) => (
                          <span key={j} className="badge badge-cat">{c}</span>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        })}
        {totalFlips === 0 && !loading && (
          <EmptyState message="Aucun flip enregistré" detail="Le journal sera alimenté après les premiers changements de position" />
        )}
      </div>

      {/* Agent Logs */}
      <LogSection logs={filteredLogs} logFilter={logFilter} setLogFilter={setLogFilter} />
    </div>
  );
}
