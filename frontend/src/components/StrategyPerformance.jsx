import React, { useState, useEffect, useCallback, useMemo } from "react";
import { apiFetch } from "../utils/api";
import { pnlColor } from "../utils/format";
import TickerLink from "./TickerLink";
import { POLL_FAST } from "../utils/constants";

const DIR_COLORS = { LONG: "var(--green)", SHORT: "var(--red)" };
const RESULT_COLORS = { TP_HIT: "var(--green)", SL_HIT: "var(--red)", EXPIRED: "var(--yellow)" };

/**
 * StrategyPerformance — Team 3 strategy tracker with drill-down.
 *
 * Shows all strategies (single + combo) with:
 * - Win rate, P&L, Sharpe, trade count, learning adj
 * - Filter by type (all/single/combo), status, sort
 * - Expandable rows with trade-level detail
 */
export default function StrategyPerformance({ isActive }) {
  const [strategies, setStrategies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(null);

  // Filters
  const [typeFilter, setTypeFilter] = useState("all"); // all | single | combo
  const [statusFilter, setStatusFilter] = useState("");
  const [sortBy, setSortBy] = useState("trades"); // trades | win_rate | pnl | sharpe

  const fetchData = useCallback(async () => {
    try {
      const res = await apiFetch("/api/trader3/strategies", {}, []);
      setStrategies(Array.isArray(res) ? res : []);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isActive) fetchData();
    const id = setInterval(fetchData, POLL_FAST);
    return () => clearInterval(id);
  }, [isActive, fetchData]);

  const filtered = useMemo(() => {
    let list = [...strategies];
    if (typeFilter === "combo") list = list.filter((s) => s.is_combo);
    else if (typeFilter === "single") list = list.filter((s) => !s.is_combo);
    if (statusFilter) list = list.filter((s) => s.status === statusFilter);

    const sorters = {
      trades: (a, b) => b.trades_count - a.trades_count,
      win_rate: (a, b) => b.win_rate - a.win_rate,
      pnl: (a, b) => b.total_pnl - a.total_pnl,
      sharpe: (a, b) => (b.sharpe || -99) - (a.sharpe || -99),
    };
    list.sort(sorters[sortBy] || sorters.trades);
    return list;
  }, [strategies, typeFilter, statusFilter, sortBy]);

  // Aggregated stats
  const totalTrades = strategies.reduce((s, st) => s + st.trades_count, 0);
  const totalWins = strategies.reduce((s, st) => s + st.wins, 0);
  const totalPnl = strategies.reduce((s, st) => s + st.total_pnl, 0);
  const globalWR = totalTrades > 0 ? (totalWins / totalTrades * 100).toFixed(1) : "—";
  const comboCount = strategies.filter((s) => s.is_combo).length;
  const singleCount = strategies.filter((s) => !s.is_combo).length;

  const selectStyle = {
    background: "var(--bg-tertiary)", color: "var(--text-primary)",
    border: "1px solid var(--border)", borderRadius: 4, padding: "4px 8px", fontSize: 12,
  };

  return (
    <div className="section-card strat-perf">
      <h3>Performance par strategie</h3>

      {/* Aggregated header */}
      <div className="strat-perf-header">
        <span><strong>{strategies.length}</strong> strategies ({singleCount} simples, {comboCount} combos)</span>
        <span><strong>{totalTrades}</strong> trades</span>
        <span>WR: <strong>{globalWR}%</strong></span>
        <span style={{ color: pnlColor(totalPnl) }}>
          P&L: <strong>{totalPnl >= 0 ? "+" : ""}{totalPnl.toFixed(2)}%</strong>
        </span>
      </div>

      {/* Filters */}
      <div className="strat-perf-filters">
        <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} style={selectStyle}>
          <option value="all">Toutes</option>
          <option value="single">Simples</option>
          <option value="combo">Combos</option>
        </select>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} style={selectStyle}>
          <option value="">Tous statuts</option>
          <option value="active">Active</option>
          <option value="validated">Validee</option>
          <option value="disabled">Desactivee</option>
        </select>
        <select value={sortBy} onChange={(e) => setSortBy(e.target.value)} style={selectStyle}>
          <option value="trades">Tri: Trades</option>
          <option value="win_rate">Tri: Win Rate</option>
          <option value="pnl">Tri: P&L</option>
          <option value="sharpe">Tri: Sharpe</option>
        </select>
      </div>

      {loading && <div style={{ padding: 12, fontSize: 13 }}>Chargement...</div>}

      {!loading && filtered.length === 0 && (
        <div style={{ padding: 16, color: "var(--text-secondary)", fontSize: 13 }}>
          Aucune strategie avec des trades clotures
        </div>
      )}

      {/* Strategy cards */}
      <div className="strat-perf-list">
        {filtered.map((s) => {
          const isExpanded = expanded === s.strategy;
          return (
            <div key={s.strategy} className={`strat-perf-card ${isExpanded ? "expanded" : ""}`}>
              <div
                className="strat-perf-card-header"
                onClick={() => setExpanded(isExpanded ? null : s.strategy)}
                style={{ cursor: "pointer" }}
              >
                <div className="strat-perf-card-title">
                  <span className={`strat-type-badge ${s.is_combo ? "combo" : "single"}`}>
                    {s.is_combo ? "COMBO" : "SIMPLE"}
                  </span>
                  <strong>{s.strategy_name || s.strategy}</strong>
                  <span className={`strat-status-badge ${s.status}`}>{s.status}</span>
                </div>
                <div className="strat-perf-card-stats">
                  <span className="strat-stat">
                    <span className="strat-stat-val">{s.trades_count}</span>
                    <span className="strat-stat-label">trades</span>
                  </span>
                  <span className="strat-stat">
                    <span className="strat-stat-val">{s.win_rate}%</span>
                    <span className="strat-stat-label">WR</span>
                  </span>
                  <span className="strat-stat">
                    <span className="strat-stat-val" style={{ color: pnlColor(s.avg_pnl) }}>
                      {s.avg_pnl >= 0 ? "+" : ""}{s.avg_pnl}%
                    </span>
                    <span className="strat-stat-label">Moy P&L</span>
                  </span>
                  <span className="strat-stat">
                    <span className="strat-stat-val" style={{ color: pnlColor(s.total_pnl) }}>
                      {s.total_pnl >= 0 ? "+" : ""}{s.total_pnl}%
                    </span>
                    <span className="strat-stat-label">Total</span>
                  </span>
                  <span className="strat-stat">
                    <span className="strat-stat-val">{s.sharpe != null ? s.sharpe : "—"}</span>
                    <span className="strat-stat-label">Sharpe</span>
                  </span>
                  <span className="strat-stat">
                    <span className="strat-stat-val" style={{ color: s.learning_adj >= 1 ? "var(--green)" : "var(--red)" }}>
                      {s.learning_adj != null ? `${s.learning_adj.toFixed(2)}x` : "—"}
                    </span>
                    <span className="strat-stat-label">Learning</span>
                  </span>
                  <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>
                    {isExpanded ? "\u25B2" : "\u25BC"}
                  </span>
                </div>
              </div>

              {/* Expanded: result breakdown + trade list */}
              {isExpanded && (
                <div className="strat-perf-detail">
                  {/* Result breakdown bar */}
                  <div className="strat-result-bar">
                    {s.wins > 0 && (
                      <div className="strat-result-segment tp" style={{ width: `${s.wins / s.trades_count * 100}%` }}>
                        TP: {s.wins}
                      </div>
                    )}
                    {s.losses > 0 && (
                      <div className="strat-result-segment sl" style={{ width: `${s.losses / s.trades_count * 100}%` }}>
                        SL: {s.losses}
                      </div>
                    )}
                    {s.expired > 0 && (
                      <div className="strat-result-segment exp" style={{ width: `${s.expired / s.trades_count * 100}%` }}>
                        EXP: {s.expired}
                      </div>
                    )}
                  </div>

                  {/* Trade table */}
                  <table className="compact-table" style={{ marginTop: 8 }}>
                    <thead>
                      <tr>
                        <th>Date</th>
                        <th>Ticker</th>
                        <th>Dir.</th>
                        <th>Resultat</th>
                        <th>P&L</th>
                        <th>Score</th>
                        <th>Duree</th>
                        <th>Regime</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(s.trades || []).map((t, i) => (
                        <tr key={i}>
                          <td>
                            {t.entry_time
                              ? new Date(t.entry_time).toLocaleDateString("fr-FR", { day: "2-digit", month: "short" })
                              : "—"}
                          </td>
                          <td style={{ fontWeight: 600 }}><TickerLink ticker={t.ticker} raw /></td>
                          <td style={{ color: DIR_COLORS[t.direction] }}>{t.direction}</td>
                          <td style={{ color: RESULT_COLORS[t.result] || "var(--text-primary)" }}>{t.result}</td>
                          <td style={{ color: pnlColor(t.pnl_pct) }}>
                            {t.pnl_pct >= 0 ? "+" : ""}{t.pnl_pct.toFixed(2)}%
                          </td>
                          <td>{t.score}</td>
                          <td>{t.holding_hours}h</td>
                          <td>{t.regime || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
