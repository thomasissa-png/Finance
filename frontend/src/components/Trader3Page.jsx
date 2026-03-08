import React, { useState, useEffect, useCallback } from "react";

const LEVEL_ICONS = { INFO: "\u2139\ufe0f", WARN: "\u26a0\ufe0f", ERROR: "\u274c", DECISION: "\u26a1" };
const LEVEL_COLORS = { INFO: "var(--text-secondary)", WARN: "var(--yellow)", ERROR: "var(--red)", DECISION: "var(--cyan)" };

const DIR_COLORS = { LONG: "var(--green)", SHORT: "var(--red)", NEUTRAL: "var(--text-muted)" };
const DIR_ARROWS = { LONG: "\u2191", SHORT: "\u2193", NEUTRAL: "\u2022" };

const STATUS_COLORS = { active: "var(--green)", paused: "var(--yellow)", stopped: "var(--red)" };

export default function Trader3Page({ isActive }) {
  const [positions, setPositions] = useState([]);
  const [strategies, setStrategies] = useState([]);
  const [learningAdj, setLearningAdj] = useState(null);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState("DECISION");
  const [filterResult, setFilterResult] = useState("");
  const [filterDirection, setFilterDirection] = useState("");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const [posRes, stratRes, adjRes, logRes] = await Promise.all([
        fetch("/api/trader3/positions").then((r) => r.ok ? r.json() : []).catch(() => []),
        fetch("/api/trader3/strategies").then((r) => r.ok ? r.json() : []).catch(() => []),
        fetch("/api/learning3/adjustments").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch(`/api/agents/trader_3/logs?limit=50${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`)
          .then((r) => r.ok ? r.json() : []).catch(() => []),
      ]);
      setPositions(Array.isArray(posRes?.active) ? posRes.active : Array.isArray(posRes) ? posRes : []);
      setStrategies(Array.isArray(stratRes) ? stratRes : []);
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
  const totalPages = Math.max(1, Math.ceil(filteredHistory.length / perPage));
  const pagedHistory = filteredHistory.slice((page - 1) * perPage, page * perPage);

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>{"\ud83d\udcc9"} Agent Trader 3 &mdash; Trading Technique (heures-3j)</h2>
        <span className="agent-page-desc">
          Positions bas&eacute;es sur les indicateurs techniques &mdash; strat&eacute;gies multiples, dur&eacute;e heures &agrave; 3 jours
        </span>
      </div>

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement...</div>}

      {/* KPIs */}
      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-value">{activePositions.length}</div>
          <div className="kpi-label">Positions actives</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value" style={{ color: realizedPnl >= 0 ? "var(--green)" : "var(--red)" }}>
            {realizedPnl >= 0 ? "+" : ""}{realizedPnl.toFixed(2)}%
          </div>
          <div className="kpi-label">P&L R&eacute;alis&eacute;</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value" style={{ color: latentPnl >= 0 ? "var(--green)" : "var(--red)" }}>
            {latentPnl >= 0 ? "+" : ""}{latentPnl.toFixed(2)}%
          </div>
          <div className="kpi-label">P&L Latent</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{topStrategy ? `${topStrategy[0]} (${topStrategy[1]})` : "\u2014"}</div>
          <div className="kpi-label">Top strat&eacute;gie</div>
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
              {"\u26a0\ufe0f"} {learningAdj.anomalies.join(" | ")}
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
                <th>Strat&eacute;gie</th>
                <th>Direction</th>
                <th>Entr&eacute;e</th>
                <th>Actuel</th>
                <th>P&L %</th>
                <th>Dur&eacute;e</th>
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
                    <td style={{ color: (p.unrealized_pnl_pct || 0) >= 0 ? "var(--green)" : "var(--red)" }}>
                      {(p.unrealized_pnl_pct || 0) >= 0 ? "+" : ""}{(p.unrealized_pnl_pct || 0).toFixed(2)}%
                    </td>
                    <td>{holdingHours}h</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          !loading && <div className="trend-no-data">Aucune position active</div>
        )}
      </div>

      {/* A/B Testing — Strategy comparison */}
      <div className="section-card">
        <h3>A/B Testing &mdash; Comparaison des strat&eacute;gies</h3>
        {strategies.length > 0 ? (
          <table className="compact-table">
            <thead>
              <tr>
                <th>Strat&eacute;gie</th>
                <th>Trades</th>
                <th>Win Rate</th>
                <th>P&L Moy.</th>
                <th>Statut</th>
              </tr>
            </thead>
            <tbody>
              {strategies.map((s, i) => (
                <tr key={i}>
                  <td style={{ fontWeight: 600 }}>{s.strategy_name}</td>
                  <td>{s.trades_count || 0}</td>
                  <td>{s.win_rate != null ? `${s.win_rate.toFixed(1)}%` : "\u2014"}</td>
                  <td style={{ color: (s.avg_pnl || 0) >= 0 ? "var(--green)" : "var(--red)" }}>
                    {(s.avg_pnl || 0) >= 0 ? "+" : ""}{(s.avg_pnl || 0).toFixed(2)}%
                  </td>
                  <td>
                    <span style={{ color: STATUS_COLORS[s.status] || "var(--text-secondary)", fontWeight: 600 }}>
                      {s.status || "active"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          !loading && <div className="trend-no-data">Aucune strat&eacute;gie enregistr&eacute;e &mdash; les donn&eacute;es seront disponibles apr&egrave;s les premiers trades</div>
        )}
      </div>

      {/* Trade history */}
      <div className="section-card">
        <h3>Historique des trades</h3>
        <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
          <select value={filterResult} onChange={(e) => { setFilterResult(e.target.value); setPage(1); }}
            style={{ background: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border)", borderRadius: 4, padding: "4px 8px", fontSize: 12 }}>
            <option value="">Tous r&eacute;sultats</option>
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
                  <th>Strat&eacute;gie</th>
                  <th>Dir.</th>
                  <th>R&eacute;sultat</th>
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
        <h3>Logs Agent Trader 3</h3>
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
                  {log.details.strategy && <span> [{log.details.strategy}]</span>}
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
