import React, { useState, useEffect, useCallback } from "react";
import { DIR_COLORS, DIR_ARROWS, POLL_FAST, T4_TEAM_COLORS, T4_TEAM_LABELS } from "../utils/constants";
import { apiFetch } from "../utils/api";
import { pnlColor } from "../utils/format";
import { ErrorBanner, EmptyState, LastUpdated, LogSection } from "./shared";

export default function Trader4Page({ isActive }) {
  const [positions, setPositions] = useState([]);
  const [history, setHistory] = useState([]);
  const [learningAdj, setLearningAdj] = useState(null);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState("ALL");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const [posRes, histRes, adjRes, logRes, journalRes] = await Promise.all([
        apiFetch("/api/trader4/positions", {}, []),
        apiFetch("/api/trader4/history", {}, []),
        apiFetch("/api/learning4/adjustments", {}, null),
        apiFetch("/api/agents/trader_4/logs?limit=50", {}, []),
        apiFetch("/api/journal4/entries", {}, []),
      ]);
      setPositions(Array.isArray(posRes?.active) ? posRes.active : Array.isArray(posRes) ? posRes : []);
      // Merge trader history + journal entries, preferring journal (more complete data)
      const traderHist = Array.isArray(histRes) ? histRes : [];
      const journalHist = Array.isArray(journalRes) ? journalRes : [];
      // Build merged history: journal entries take priority (have enriched data)
      const histMap = new Map();
      traderHist.forEach((h) => {
        const key = `${h.ticker}-${h.entry_time || h.time || ""}`;
        histMap.set(key, h);
      });
      journalHist.forEach((e) => {
        const key = `${e.ticker}-${e.entry_time || ""}`;
        histMap.set(key, { ...e, contributing_teams: e.contributing_teams || Object.keys(e.source_details || {}) });
      });
      const merged = Array.from(histMap.values()).sort(
        (a, b) => (b.time || b.close_time || "").localeCompare(a.time || a.close_time || "")
      );
      setHistory(merged);
      setLearningAdj(adjRes);
      setLogs(Array.isArray(logRes) ? logRes : []);
      setError(null);
      setLastUpdate(new Date());
    } catch (err) {
      setError(err.message || "Impossible de charger les données Trader 4");
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
  const historyWithPnl = history.filter((t) => t.pnl_pct != null);
  const totalTrades = historyWithPnl.length;
  const wins = historyWithPnl.filter((t) => (t.pnl_pct || 0) > 0).length;
  const winRate = totalTrades > 0 ? (wins / totalTrades * 100).toFixed(1) : null;
  const confluenceTrades = history.filter((t) => (t.confluence_level || 0) >= 2).length;
  const realizedPnl = historyWithPnl.reduce((s, t) => s + (t.pnl_pct || 0), 0);
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
  const totalPagesCount = Math.max(1, Math.ceil(history.length / perPage));
  const pagedHistory = history.slice((page - 1) * perPage, page * perPage);

  // Team contribution
  const teamContrib = { team_1: 0, team_2: 0, team_3: 0 };
  history.forEach((t) => {
    (t.contributing_teams || []).forEach((team) => {
      if (teamContrib[team] !== undefined) teamContrib[team]++;
    });
  });
  const totalContrib = Object.values(teamContrib).reduce((s, v) => s + v, 0) || 1;

  const filteredLogs = logFilter === "ALL" ? logs : logs.filter((l) => l.level === logFilter);

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>Agent Trader 4 &mdash; Expert Multi-Signal (15+ ans)</h2>
        <span className="agent-page-desc">
          Trading haute conviction &mdash; n'agit que sur les signaux confluents de plusieurs équipes
        </span>
        <LastUpdated date={lastUpdate} />
      </div>

      <ErrorBanner error={error} onRetry={fetchData} />

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement...</div>}

      {/* Activation status banner */}
      {!isActivated && (
        <div className="section-card" style={{ borderLeft: "3px solid var(--yellow)", padding: "12px 16px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span>!</span>
            <span style={{ fontSize: 13 }}>
              <b>Agent en attente d'activation</b> &mdash; nécessite suffisamment de données des équipes 1, 2 et 3
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
          <div className="kpi-value">{totalTrades}</div>
          <div className="kpi-label">Trades clôturés</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value" style={{ color: winRate != null && winRate >= 50 ? "var(--green)" : winRate != null ? "var(--red)" : "var(--text-muted)" }}>
            {winRate != null ? `${winRate}%` : "N/A"}
          </div>
          <div className="kpi-label">Win Rate</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{confluenceTrades}</div>
          <div className="kpi-label">Trades confluents</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value" style={{ color: pnlColor(realizedPnl) }}>
            {realizedPnl >= 0 ? "+" : ""}{realizedPnl.toFixed(2)}%
          </div>
          <div className="kpi-label">P&L Réalisé</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{bestCombo ? bestCombo[0] : "\u2014"}</div>
          <div className="kpi-label">Meilleur combo</div>
        </div>
      </div>

      {/* Team contribution */}
      <div className="section-card">
        <h3>Contribution par équipe</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 12 }}>
          Fréquence de contribution de chaque équipe aux signaux de trading
        </p>
        <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
          {Object.entries(teamContrib).map(([team, count]) => {
            const pct = (count / totalContrib) * 100;
            return (
              <div key={team} style={{ flex: 1, minWidth: 120 }}>
                <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4 }}>{T4_TEAM_LABELS[team] || team}</div>
                <div style={{ background: "var(--bg-tertiary)", borderRadius: 4, height: 12 }}>
                  <div style={{ width: `${pct}%`, background: T4_TEAM_COLORS[team], borderRadius: 4, height: "100%" }} />
                </div>
                <div style={{ fontSize: 13, fontWeight: 600, marginTop: 4, color: T4_TEAM_COLORS[team] }}>
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
                <th>Entrée</th>
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
                      <span key={j} className="badge" style={{ background: T4_TEAM_COLORS[t] || "var(--bg-tertiary)", color: "#fff", fontSize: 10, marginRight: 4 }}>
                        {T4_TEAM_LABELS[t] || t}
                      </span>
                    ))}
                  </td>
                  <td>{p.entry_price != null ? p.entry_price.toFixed(2) : "\u2014"}</td>
                  <td>{p.current_price != null ? p.current_price.toFixed(2) : "\u2014"}</td>
                  <td style={{ color: pnlColor(p.unrealized_pnl_pct) }}>
                    {(p.unrealized_pnl_pct || 0) >= 0 ? "+" : ""}{(p.unrealized_pnl_pct || 0).toFixed(2)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          !loading && <EmptyState message="Aucune position active" detail={isActivated ? "En attente de signal confluent" : "Agent non activé"} />
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
                  <th>Résultat</th>
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
