import React, { useState, useEffect, useCallback } from "react";

const LEVEL_ICONS = { INFO: "\u2139\ufe0f", WARN: "\u26a0\ufe0f", ERROR: "\u274c", DECISION: "\u26a1" };
const LEVEL_COLORS = { INFO: "var(--text-secondary)", WARN: "var(--yellow)", ERROR: "var(--red)", DECISION: "var(--cyan)" };
const DIR_COLORS = { LONG: "var(--green)", SHORT: "var(--red)", NEUTRAL: "var(--text-muted)" };

export default function Journal3Page({ isActive }) {
  const [entries, setEntries] = useState([]);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const [entriesRes, logRes] = await Promise.all([
        fetch("/api/journal3/entries").then((r) => r.ok ? r.json() : []).catch(() => []),
        fetch(`/api/agents/journal_3/logs?limit=30${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`)
          .then((r) => r.ok ? r.json() : []).catch(() => []),
      ]);
      setEntries(Array.isArray(entriesRes) ? entriesRes : []);
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

  const triggerJournal = async () => {
    setTriggering(true);
    try {
      await fetch("/api/journal3/trigger", { method: "POST" });
      setTimeout(fetchData, 2000);
    } catch { /* ignore */ } finally {
      setTriggering(false);
    }
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

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>{"\ud83d\udcd4"} Agent Journal 3 &mdash; Journal Technique</h2>
        <span className="agent-page-desc">
          Journal des trades techniques (Trader 3) &mdash; cl&ocirc;ture, P&L, MAE/MFE par strat&eacute;gie
        </span>
      </div>

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement...</div>}

      {/* KPIs */}
      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-value">{totalTrades}</div>
          <div className="kpi-label">Trades ferm&eacute;s</div>
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
          {triggering ? "En cours..." : "Lancer Journal 3"}
        </button>
      </div>

      {/* Performance par strategie */}
      <div className="section-card">
        <h3>Performance par strat&eacute;gie</h3>
        {Object.keys(byStrategy).length > 0 ? (
          <table className="compact-table">
            <thead>
              <tr>
                <th>Strat&eacute;gie</th>
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
                    <td style={{ color: sPnl >= 0 ? "var(--green)" : "var(--red)" }}>
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
          !loading && <div className="trend-no-data">Aucune donn&eacute;e par strat&eacute;gie</div>
        )}
      </div>

      {/* Recent entries */}
      <div className="section-card">
        <h3>Entr&eacute;es r&eacute;centes ({totalTrades})</h3>
        {entries.length > 0 ? (
          <table className="compact-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Ticker</th>
                <th>Strat&eacute;gie</th>
                <th>Direction</th>
                <th>R&eacute;sultat</th>
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
                  <td style={{ color: (e.pnl_pct || 0) >= 0 ? "var(--green)" : "var(--red)" }}>
                    {(e.pnl_pct || 0) >= 0 ? "+" : ""}{(e.pnl_pct || 0).toFixed(2)}%
                  </td>
                  <td>{(e.mae_pct || 0).toFixed(2)}%</td>
                  <td>{(e.mfe_pct || 0).toFixed(2)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          !loading && <div className="trend-no-data">Aucun trade enregistr&eacute; &mdash; le journal sera aliment&eacute; apr&egrave;s les premi&egrave;res cl&ocirc;tures</div>
        )}
      </div>

      {/* Logs */}
      <div className="section-card">
        <h3>Logs Agent Journal 3</h3>
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
