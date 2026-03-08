import React, { useState, useEffect, useCallback } from "react";

const LEVEL_ICONS = { INFO: "\u2139\ufe0f", WARN: "\u26a0\ufe0f", ERROR: "\u274c", DECISION: "\u26a1" };
const LEVEL_COLORS = { INFO: "var(--text-secondary)", WARN: "var(--yellow)", ERROR: "var(--red)", DECISION: "var(--cyan)" };
const DIR_COLORS = { LONG: "var(--green)", SHORT: "var(--red)", NEUTRAL: "var(--text-muted)" };

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

export default function Journal4Page({ isActive }) {
  const [entries, setEntries] = useState([]);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const [entriesRes, logRes] = await Promise.all([
        fetch("/api/journal4/entries").then((r) => r.ok ? r.json() : []).catch(() => []),
        fetch(`/api/agents/journal_4/logs?limit=30${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`)
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
      await fetch("/api/journal4/trigger", { method: "POST" });
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

  // Group by confluence level
  const byConfluence = {};
  entries.forEach((e) => {
    const lvl = `${e.confluence_level || 0}/3`;
    if (!byConfluence[lvl]) byConfluence[lvl] = [];
    byConfluence[lvl].push(e);
  });

  // Win rate per confluence level
  const confluenceStats = Object.entries(byConfluence).map(([lvl, levelEntries]) => {
    const w = levelEntries.filter((e) => (e.pnl_pct || 0) > 0).length;
    const wr = levelEntries.length > 0 ? ((w / levelEntries.length) * 100).toFixed(1) : "\u2014";
    const pnl = levelEntries.reduce((s, e) => s + (e.pnl_pct || 0), 0);
    return { level: lvl, count: levelEntries.length, winRate: wr, pnl };
  }).sort((a, b) => b.level.localeCompare(a.level));

  // Best combo
  const byCombo = {};
  entries.forEach((e) => {
    const combo = (e.contributing_teams || []).sort().join("+") || "?";
    if (!byCombo[combo]) byCombo[combo] = { count: 0, wins: 0, pnl: 0 };
    byCombo[combo].count++;
    if ((e.pnl_pct || 0) > 0) byCombo[combo].wins++;
    byCombo[combo].pnl += e.pnl_pct || 0;
  });
  const bestCombo = Object.entries(byCombo).sort((a, b) => b[1].pnl - a[1].pnl)[0];

  // Per-team accuracy
  const teamAccuracy = { team_1: { total: 0, wins: 0 }, team_2: { total: 0, wins: 0 }, team_3: { total: 0, wins: 0 } };
  entries.forEach((e) => {
    (e.contributing_teams || []).forEach((t) => {
      if (teamAccuracy[t]) {
        teamAccuracy[t].total++;
        if ((e.pnl_pct || 0) > 0) teamAccuracy[t].wins++;
      }
    });
  });

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>{"\ud83d\udcd4"} Agent Journal 4 &mdash; Journal Meta</h2>
        <span className="agent-page-desc">
          Journal des trades multi-signal (Trader 4) &mdash; analyse par niveau de confluence et combinaison d'&eacute;quipes
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
          <div className="kpi-label">Win Rate global</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value" style={{ color: totalPnl >= 0 ? "var(--green)" : "var(--red)" }}>
            {totalPnl >= 0 ? "+" : ""}{totalPnl.toFixed(2)}%
          </div>
          <div className="kpi-label">P&L Total</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{bestCombo ? bestCombo[0] : "\u2014"}</div>
          <div className="kpi-label">Meilleur combo</div>
        </div>
      </div>

      {/* Trigger button */}
      <div style={{ marginBottom: 16 }}>
        <button className="trigger-btn" onClick={triggerJournal} disabled={triggering}>
          {triggering ? "En cours..." : "Lancer Journal 4"}
        </button>
      </div>

      {/* Win rate per confluence level */}
      <div className="section-card">
        <h3>Performance par niveau de confluence</h3>
        {confluenceStats.length > 0 ? (
          <table className="compact-table">
            <thead>
              <tr>
                <th>Confluence</th>
                <th>Trades</th>
                <th>Win Rate</th>
                <th>P&L Total</th>
              </tr>
            </thead>
            <tbody>
              {confluenceStats.map((cs) => (
                <tr key={cs.level}>
                  <td style={{ fontWeight: 600 }}>{cs.level}</td>
                  <td>{cs.count}</td>
                  <td>{cs.winRate}%</td>
                  <td style={{ color: cs.pnl >= 0 ? "var(--green)" : "var(--red)" }}>
                    {cs.pnl >= 0 ? "+" : ""}{cs.pnl.toFixed(2)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          !loading && <div className="trend-no-data">Aucune donn&eacute;e de confluence</div>
        )}
      </div>

      {/* Per-team accuracy */}
      <div className="section-card">
        <h3>Pr&eacute;cision par &eacute;quipe</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 12 }}>
          Win rate des trades auxquels chaque &eacute;quipe a contribu&eacute;
        </p>
        <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
          {Object.entries(teamAccuracy).map(([team, ta]) => {
            const wr = ta.total > 0 ? ((ta.wins / ta.total) * 100).toFixed(1) : "\u2014";
            return (
              <div key={team} style={{ flex: 1, minWidth: 120, textAlign: "center" }}>
                <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4 }}>{TEAM_LABELS[team] || team}</div>
                <div style={{ fontSize: 22, fontWeight: 700, color: TEAM_COLORS[team] }}>{wr}%</div>
                <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{ta.wins}/{ta.total} trades</div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Entries grouped by confluence */}
      <div className="section-card">
        <h3>Historique par confluence</h3>
        {Object.entries(byConfluence).sort((a, b) => b[0].localeCompare(a[0])).map(([lvl, levelEntries]) => (
          <div key={lvl} style={{ marginBottom: 16 }}>
            <h4 style={{ display: "flex", gap: 12, alignItems: "center" }}>
              <span>Confluence {lvl}</span>
              <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                {levelEntries.length} trades
              </span>
            </h4>
            <table className="compact-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Ticker</th>
                  <th>Direction</th>
                  <th>&Eacute;quipes</th>
                  <th>R&eacute;sultat</th>
                  <th>P&L</th>
                </tr>
              </thead>
              <tbody>
                {levelEntries.slice(0, 20).map((e, i) => (
                  <tr key={i}>
                    <td>{e.entry_time ? new Date(e.entry_time).toLocaleDateString("fr-FR", { day: "2-digit", month: "short" }) : "\u2014"}</td>
                    <td style={{ fontWeight: 600 }}>{e.ticker}</td>
                    <td style={{ color: DIR_COLORS[e.direction] }}>{e.direction}</td>
                    <td>
                      {(e.contributing_teams || []).map((t, j) => (
                        <span key={j} className="badge" style={{ background: TEAM_COLORS[t] || "var(--bg-tertiary)", color: "#fff", fontSize: 10, marginRight: 4 }}>
                          {TEAM_LABELS[t] || t}
                        </span>
                      ))}
                    </td>
                    <td>{e.result || "\u2014"}</td>
                    <td style={{ color: (e.pnl_pct || 0) >= 0 ? "var(--green)" : "var(--red)" }}>
                      {(e.pnl_pct || 0) >= 0 ? "+" : ""}{(e.pnl_pct || 0).toFixed(2)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
        {totalTrades === 0 && !loading && (
          <div className="trend-no-data">Aucun trade enregistr&eacute; &mdash; le journal sera aliment&eacute; apr&egrave;s les premi&egrave;res cl&ocirc;tures</div>
        )}
      </div>

      {/* Logs */}
      <div className="section-card">
        <h3>Logs Agent Journal 4</h3>
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
