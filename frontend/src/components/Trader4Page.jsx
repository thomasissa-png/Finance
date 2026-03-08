import React, { useState, useEffect, useCallback } from "react";

const LEVEL_ICONS = { INFO: "\u2139\ufe0f", WARN: "\u26a0\ufe0f", ERROR: "\u274c", DECISION: "\u26a1" };
const LEVEL_COLORS = { INFO: "var(--text-secondary)", WARN: "var(--yellow)", ERROR: "var(--red)", DECISION: "var(--cyan)" };

const DIR_COLORS = { LONG: "var(--green)", SHORT: "var(--red)", NEUTRAL: "var(--text-muted)" };
const DIR_ARROWS = { LONG: "\u2191", SHORT: "\u2193", NEUTRAL: "\u2022" };

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

export default function Trader4Page({ isActive }) {
  const [positions, setPositions] = useState([]);
  const [history, setHistory] = useState([]);
  const [learningAdj, setLearningAdj] = useState(null);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState("DECISION");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const [posRes, histRes, adjRes, logRes] = await Promise.all([
        fetch("/api/trader4/positions").then((r) => r.ok ? r.json() : []).catch(() => []),
        fetch("/api/trader4/history").then((r) => r.ok ? r.json() : []).catch(() => []),
        fetch("/api/learning4/adjustments").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch(`/api/agents/trader_4/logs?limit=50${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`)
          .then((r) => r.ok ? r.json() : []).catch(() => []),
      ]);
      setPositions(Array.isArray(posRes?.active) ? posRes.active : Array.isArray(posRes) ? posRes : []);
      setHistory(Array.isArray(histRes) ? histRes : []);
      setLearningAdj(adjRes);
      setLogs(Array.isArray(logRes) ? logRes : []);
    } catch { /* ignore */ } finally {
      setLoading(false);
    }
  }, [logFilter]);

  useEffect(() => {
    if (isActive) fetchData();
    const id = setInterval(fetchData, 30_000);
    return () => clearInterval(id);
  }, [isActive, fetchData]);

  // KPIs
  const activePositions = positions.filter((p) => p.status === "PENDING" || p.status === "active");
  const confluenceTrades = history.filter((t) => (t.confluence_level || 0) >= 2).length;
  const realizedPnl = history.reduce((s, t) => s + (t.pnl_pct || 0), 0);
  const byCombo = {};
  history.forEach((t) => {
    const combo = (t.contributing_teams || []).sort().join("+") || "?";
    if (!byCombo[combo]) byCombo[combo] = { count: 0, pnl: 0 };
    byCombo[combo].count++;
    byCombo[combo].pnl += t.pnl_pct || 0;
  });
  const bestCombo = Object.entries(byCombo).sort((a, b) => b[1].pnl - a[1].pnl)[0];

  // Activation status
  const activationStatus = learningAdj?.activation_status || {};
  const isActivated = activationStatus.activated !== false;

  // Pagination
  const perPage = 15;
  const totalPages = Math.max(1, Math.ceil(history.length / perPage));
  const pagedHistory = history.slice((page - 1) * perPage, page * perPage);

  // Team contribution analysis
  const teamContrib = { team_1: 0, team_2: 0, team_3: 0 };
  history.forEach((t) => {
    (t.contributing_teams || []).forEach((team) => {
      if (teamContrib[team] !== undefined) teamContrib[team]++;
    });
  });
  const totalContrib = Object.values(teamContrib).reduce((s, v) => s + v, 0) || 1;

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>{"\ud83c\udf1f"} Agent Trader 4 &mdash; Expert Multi-Signal (15+ ans)</h2>
        <span className="agent-page-desc">
          Trading haute conviction &mdash; n'agit que sur les signaux confluents de plusieurs &eacute;quipes
        </span>
      </div>

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement...</div>}

      {/* Activation status banner */}
      {!isActivated && (
        <div className="section-card" style={{ borderLeft: "3px solid var(--yellow)", padding: "12px 16px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span>{"\u26a0\ufe0f"}</span>
            <span style={{ fontSize: 13 }}>
              <b>Agent en attente d'activation</b> &mdash; n&eacute;cessite suffisamment de donn&eacute;es des &eacute;quipes 1, 2 et 3
            </span>
          </div>
          {activationStatus.reason && (
            <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>
              {activationStatus.reason}
            </div>
          )}
        </div>
      )}

      {/* KPIs */}
      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-value">{activePositions.length}</div>
          <div className="kpi-label">Positions actives</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{confluenceTrades}</div>
          <div className="kpi-label">Trades confluents</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value" style={{ color: realizedPnl >= 0 ? "var(--green)" : "var(--red)" }}>
            {realizedPnl >= 0 ? "+" : ""}{realizedPnl.toFixed(2)}%
          </div>
          <div className="kpi-label">P&L R&eacute;alis&eacute;</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{bestCombo ? bestCombo[0] : "\u2014"}</div>
          <div className="kpi-label">Meilleur combo</div>
        </div>
      </div>

      {/* Team contribution */}
      <div className="section-card">
        <h3>Contribution par &eacute;quipe</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 12 }}>
          Fr&eacute;quence de contribution de chaque &eacute;quipe aux signaux de trading
        </p>
        <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
          {Object.entries(teamContrib).map(([team, count]) => {
            const pct = (count / totalContrib) * 100;
            return (
              <div key={team} style={{ flex: 1, minWidth: 120 }}>
                <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4 }}>{TEAM_LABELS[team] || team}</div>
                <div style={{ background: "var(--bg-tertiary)", borderRadius: 4, height: 12 }}>
                  <div style={{ width: `${pct}%`, background: TEAM_COLORS[team], borderRadius: 4, height: "100%" }} />
                </div>
                <div style={{ fontSize: 13, fontWeight: 600, marginTop: 4, color: TEAM_COLORS[team] }}>
                  {count} ({pct.toFixed(0)}%)
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Active positions */}
      <div className="section-card">
        <h3>Positions actives ({activePositions.length})</h3>
        {activePositions.length > 0 ? (
          <table className="compact-table">
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Direction</th>
                <th>Confluence</th>
                <th>&Eacute;quipes</th>
                <th>Entr&eacute;e</th>
                <th>Actuel</th>
                <th>P&L %</th>
              </tr>
            </thead>
            <tbody>
              {activePositions.map((p, i) => (
                <tr key={i}>
                  <td style={{ fontWeight: 600 }}>{p.ticker}</td>
                  <td style={{ color: DIR_COLORS[p.direction] }}>
                    {DIR_ARROWS[p.direction] || "\u2022"} {p.direction}
                  </td>
                  <td style={{ fontWeight: 600 }}>{p.confluence_level || 0}/3</td>
                  <td>
                    {(p.contributing_teams || []).map((t, j) => (
                      <span key={j} className="badge" style={{ background: TEAM_COLORS[t] || "var(--bg-tertiary)", color: "#fff", fontSize: 10, marginRight: 4 }}>
                        {TEAM_LABELS[t] || t}
                      </span>
                    ))}
                  </td>
                  <td>{p.entry_price != null ? p.entry_price.toFixed(2) : "\u2014"}</td>
                  <td>{p.current_price != null ? p.current_price.toFixed(2) : "\u2014"}</td>
                  <td style={{ color: (p.unrealized_pnl_pct || 0) >= 0 ? "var(--green)" : "var(--red)" }}>
                    {(p.unrealized_pnl_pct || 0) >= 0 ? "+" : ""}{(p.unrealized_pnl_pct || 0).toFixed(2)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          !loading && <div className="trend-no-data">Aucune position active &mdash; {isActivated ? "en attente de signal confluent" : "agent non activ\u00e9"}</div>
        )}
      </div>

      {/* Trade history */}
      <div className="section-card">
        <h3>Historique des trades ({history.length})</h3>
        {pagedHistory.length > 0 ? (
          <>
            <table className="compact-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Ticker</th>
                  <th>Dir.</th>
                  <th>Confluence</th>
                  <th>R&eacute;sultat</th>
                  <th>P&L</th>
                </tr>
              </thead>
              <tbody>
                {pagedHistory.map((t, i) => (
                  <tr key={i}>
                    <td>{t.timestamp ? new Date(t.timestamp).toLocaleDateString("fr-FR", { day: "2-digit", month: "short" }) : "\u2014"}</td>
                    <td style={{ fontWeight: 600 }}>{t.ticker}</td>
                    <td style={{ color: DIR_COLORS[t.direction] }}>{t.direction}</td>
                    <td>{t.confluence_level || 0}/3</td>
                    <td>{t.result || "\u2014"}</td>
                    <td style={{ color: (t.pnl_pct || 0) >= 0 ? "var(--green)" : "var(--red)" }}>
                      {(t.pnl_pct || 0) >= 0 ? "+" : ""}{(t.pnl_pct || 0).toFixed(2)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {totalPages > 1 && (
              <div style={{ display: "flex", gap: 8, justifyContent: "center", marginTop: 8 }}>
                <button disabled={page <= 1} onClick={() => setPage(page - 1)} className="log-filter-btn">&laquo;</button>
                <span style={{ fontSize: 12, lineHeight: "28px" }}>{page}/{totalPages}</span>
                <button disabled={page >= totalPages} onClick={() => setPage(page + 1)} className="log-filter-btn">&raquo;</button>
              </div>
            )}
          </>
        ) : (
          !loading && <div className="trend-no-data">Aucun trade dans l'historique</div>
        )}
      </div>

      {/* Logs */}
      <div className="section-card">
        <h3>Logs Agent Trader 4</h3>
        <div className="log-filter-row">
          {["DECISION", "ALL", "INFO", "WARN", "ERROR"].map((level) => (
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
                  {log.details.ticker && <span><b>{log.details.ticker}</b></span>}
                  {log.details.confluence_level && <span> (confluence {log.details.confluence_level}/3)</span>}
                  {log.details.direction && <span> {log.details.direction}</span>}
                  {log.details.reason && <div className="agent-log-reason">{log.details.reason}</div>}
                  {!log.details.ticker && typeof log.details === "object" && (
                    <span>{JSON.stringify(log.details)}</span>
                  )}
                </div>
              )}
            </div>
          ))}
          {logs.length === 0 && <div className="trend-no-data">Aucune d&eacute;cision enregistr&eacute;e</div>}
        </div>
      </div>
    </div>
  );
}
