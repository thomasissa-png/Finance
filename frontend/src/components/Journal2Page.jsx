import React, { useState, useEffect, useCallback } from "react";

const LEVEL_ICONS = { INFO: "\u2139\ufe0f", WARN: "\u26a0\ufe0f", ERROR: "\u274c", DECISION: "\u26a1" };
const LEVEL_COLORS = { INFO: "var(--text-secondary)", WARN: "var(--yellow)", ERROR: "var(--red)", DECISION: "var(--cyan)" };
const DIR_COLORS = { LONG: "var(--green)", SHORT: "var(--red)", NEUTRAL: "var(--text-muted)" };

export default function Journal2Page({ isActive }) {
  const [entries, setEntries] = useState([]);
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const [entriesRes, logRes] = await Promise.all([
        fetch("/api/journal2/entries").then((r) => r.ok ? r.json() : []).catch(() => []),
        fetch("/api/agents/journal_2/logs?limit=30")
          .then((r) => r.ok ? r.json() : []).catch(() => []),
      ]);
      setEntries(Array.isArray(entriesRes) ? entriesRes : []);
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

  const triggerJournal = async () => {
    setTriggering(true);
    try {
      await fetch("/api/journal2/trigger", { method: "POST" });
      setTimeout(fetchData, 2000);
    } catch { /* ignore */ } finally {
      setTriggering(false);
    }
  };

  // Compute KPIs
  const totalFlips = entries.length;
  const wins = entries.filter((e) => (e.pnl_pct || 0) > 0).length;
  const winRate = totalFlips > 0 ? ((wins / totalFlips) * 100).toFixed(1) : "—";
  const totalPnl = entries.reduce((s, e) => s + (e.pnl_pct || 0), 0);
  const avgMae = totalFlips > 0
    ? (entries.reduce((s, e) => s + (e.mae_pct || 0), 0) / totalFlips).toFixed(2)
    : "—";
  const avgMfe = totalFlips > 0
    ? (entries.reduce((s, e) => s + (e.mfe_pct || 0), 0) / totalFlips).toFixed(2)
    : "—";

  // Group by ticker
  const byTicker = {};
  entries.forEach((e) => {
    const t = e.ticker || "?";
    if (!byTicker[t]) byTicker[t] = [];
    byTicker[t].push(e);
  });

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>{"\ud83d\udcd4"} Agent Journal 2 &mdash; Trend Positions</h2>
        <span className="agent-page-desc">
          Journal des positions de tendance (Trader 2) &mdash; flips, MAE/MFE, P&L par p&eacute;riode
        </span>
      </div>

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
          <div className="kpi-value" style={{ color: totalPnl >= 0 ? "var(--green)" : "var(--red)" }}>
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
                      <td>{e.entry_time ? new Date(e.entry_time).toLocaleDateString("fr-FR", { day: "2-digit", month: "short" }) : "—"}</td>
                      <td style={{ color: DIR_COLORS[e.direction] }}>{e.direction}</td>
                      <td style={{ color: (e.pnl_pct || 0) >= 0 ? "var(--green)" : "var(--red)" }}>
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
          <div className="trend-no-data">Aucun flip enregistr&eacute; &mdash; le journal sera aliment&eacute; apr&egrave;s les premiers changements de position</div>
        )}
      </div>

      {/* Agent Logs */}
      <div className="section-card">
        <h3>Logs Agent Journal 2</h3>
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
