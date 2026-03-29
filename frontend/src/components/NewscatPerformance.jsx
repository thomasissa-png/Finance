/**
 * NewscatPerformance — Shared component for zone+intensity+source performance analysis.
 * Used by TraderPage (Team 1) and Trader2Page (Team 2).
 *
 * Displays per-newscat combo stats (WR, PnL, sources) with filters and drill-down.
 */
import React, { useState, useEffect, useCallback, useMemo } from "react";
import { pnlColor } from "../utils/format";

const CATEGORY_COLORS = {
  weather: "#4fc3f7", commodity: "#ffb74d", geopolitical: "#ef5350",
  supply_chain: "#ab47bc", sector: "#66bb6a", regulatory: "#78909c",
  macro: "#9e9e9e", earnings: "#757575", central_bank_subtle: "#8d6e63",
  m_a: "#5c6bc0", other: "#90a4ae",
};

const INTENSITY_LABELS = { low: "Faible", high: "Fort", "": "Moyen" };
const INTENSITY_COLORS = { low: "var(--text-muted)", high: "var(--yellow)", "": "var(--text-secondary)" };

export default function NewscatPerformance({ team = 1, isActive }) {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [expandedKey, setExpandedKey] = useState(null);

  // Filters
  const [filterCategory, setFilterCategory] = useState("");
  const [filterZone, setFilterZone] = useState("");
  const [filterIntensity, setFilterIntensity] = useState("");
  const [filterTicker, setFilterTicker] = useState("");
  const [filterSource, setFilterSource] = useState("");
  const [sortBy, setSortBy] = useState("total"); // total, win_rate, avg_pnl, total_pnl

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch(`/api/newscat-performance/${team}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(Array.isArray(json) ? json : []);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [team]);

  useEffect(() => {
    if (isActive) fetchData();
  }, [isActive, fetchData]);

  // Extract unique values for filter dropdowns
  const filterOptions = useMemo(() => {
    const categories = new Set();
    const zones = new Set();
    const tickers = new Set();
    const sources = new Set();
    for (const d of data) {
      categories.add(d.category);
      if (d.zone) zones.add(d.zone);
      tickers.add(d.ticker);
      for (const s of Object.keys(d.sources || {})) sources.add(s);
    }
    return {
      categories: [...categories].sort(),
      zones: [...zones].sort(),
      tickers: [...tickers].sort(),
      sources: [...sources].sort(),
    };
  }, [data]);

  // Apply filters
  const filtered = useMemo(() => {
    let list = [...data];
    if (filterCategory) list = list.filter((d) => d.category === filterCategory);
    if (filterZone) list = list.filter((d) => d.zone === filterZone);
    if (filterIntensity) list = list.filter((d) => {
      if (filterIntensity === "none") return !d.intensity;
      return d.intensity === filterIntensity;
    });
    if (filterTicker) list = list.filter((d) => d.ticker === filterTicker);
    if (filterSource) list = list.filter((d) => d.sources && d.sources[filterSource]);

    // Sort
    if (sortBy === "win_rate") list.sort((a, b) => b.win_rate - a.win_rate);
    else if (sortBy === "avg_pnl") list.sort((a, b) => b.avg_pnl - a.avg_pnl);
    else if (sortBy === "total_pnl") list.sort((a, b) => b.total_pnl - a.total_pnl);
    else list.sort((a, b) => b.total - a.total);

    return list;
  }, [data, filterCategory, filterZone, filterIntensity, filterTicker, filterSource, sortBy]);

  // Aggregated stats
  const stats = useMemo(() => {
    const totalTrades = filtered.reduce((s, d) => s + d.total, 0);
    const totalWins = filtered.reduce((s, d) => s + d.wins, 0);
    const totalPnl = filtered.reduce((s, d) => s + d.total_pnl, 0);
    return {
      combos: filtered.length,
      trades: totalTrades,
      wr: totalTrades > 0 ? (totalWins / totalTrades * 100).toFixed(1) : "0",
      pnl: totalPnl.toFixed(2),
    };
  }, [filtered]);

  if (loading) return <div className="agent-loading"><span className="spinner" /> Chargement...</div>;
  if (error) return <div className="agent-error-banner">Erreur : {error}</div>;

  return (
    <div className="section-card newscat-perf">
      <div className="section-header">
        <h3>Performance par Newscat (zone + intensite + source)</h3>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          {stats.combos} combos | {stats.trades} trades | WR {stats.wr}% | PnL {stats.pnl}%
        </span>
      </div>

      {/* Filters */}
      <div className="newscat-filters">
        <select value={filterCategory} onChange={(e) => setFilterCategory(e.target.value)} className="filter-select">
          <option value="">Toutes categories</option>
          {filterOptions.categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={filterZone} onChange={(e) => setFilterZone(e.target.value)} className="filter-select">
          <option value="">Toutes zones</option>
          {filterOptions.zones.map((z) => <option key={z} value={z}>{z}</option>)}
        </select>
        <select value={filterIntensity} onChange={(e) => setFilterIntensity(e.target.value)} className="filter-select">
          <option value="">Toutes intensites</option>
          <option value="high">Fort (67-100)</option>
          <option value="low">Faible (0-33)</option>
          <option value="none">Moyen (pas de tier)</option>
        </select>
        <select value={filterTicker} onChange={(e) => setFilterTicker(e.target.value)} className="filter-select">
          <option value="">Tous tickers</option>
          {filterOptions.tickers.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select value={filterSource} onChange={(e) => setFilterSource(e.target.value)} className="filter-select">
          <option value="">Toutes sources</option>
          {filterOptions.sources.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={sortBy} onChange={(e) => setSortBy(e.target.value)} className="filter-select">
          <option value="total">Tri: Nb trades</option>
          <option value="win_rate">Tri: Win Rate</option>
          <option value="avg_pnl">Tri: PnL moyen</option>
          <option value="total_pnl">Tri: PnL total</option>
        </select>
      </div>

      {filtered.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-message">Aucune donnee pour ces filtres</div>
        </div>
      ) : (
        <div className="newscat-combos">
          {filtered.map((combo) => {
            const isExpanded = expandedKey === combo.key;
            const wrColor = combo.win_rate >= 55 ? "var(--green)" : combo.win_rate <= 35 ? "var(--red)" : "var(--yellow)";
            return (
              <div key={combo.key} className={`newscat-combo-card ${isExpanded ? "expanded" : ""}`}>
                <div
                  className="newscat-combo-header"
                  onClick={() => setExpandedKey(isExpanded ? null : combo.key)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => { if (e.key === "Enter") setExpandedKey(isExpanded ? null : combo.key); }}
                >
                  <div className="newscat-combo-key">
                    <span className="newscat-cat-badge" style={{ backgroundColor: CATEGORY_COLORS[combo.category] || "#90a4ae" }}>
                      {combo.category}
                    </span>
                    {combo.zone && <span className="newscat-zone-badge">{combo.zone}</span>}
                    {combo.intensity && (
                      <span className="newscat-intensity-badge" style={{ color: INTENSITY_COLORS[combo.intensity] }}>
                        {INTENSITY_LABELS[combo.intensity]}
                      </span>
                    )}
                    <span className="newscat-ticker">{combo.ticker}</span>
                  </div>
                  <div className="newscat-combo-stats">
                    <span className="newscat-stat">
                      <span className="newscat-stat-value" style={{ color: wrColor }}>{combo.win_rate}%</span>
                      <span className="newscat-stat-label">WR</span>
                    </span>
                    <span className="newscat-stat">
                      <span className="newscat-stat-value" style={{ color: pnlColor(combo.avg_pnl) }}>
                        {combo.avg_pnl >= 0 ? "+" : ""}{combo.avg_pnl.toFixed(2)}%
                      </span>
                      <span className="newscat-stat-label">Moy</span>
                    </span>
                    <span className="newscat-stat">
                      <span className="newscat-stat-value" style={{ color: pnlColor(combo.total_pnl) }}>
                        {combo.total_pnl >= 0 ? "+" : ""}{combo.total_pnl.toFixed(2)}%
                      </span>
                      <span className="newscat-stat-label">Total</span>
                    </span>
                    <span className="newscat-stat">
                      <span className="newscat-stat-value">{combo.wins}/{combo.losses}/{combo.expired}</span>
                      <span className="newscat-stat-label">W/L/E</span>
                    </span>
                    <span className="newscat-stat">
                      <span className="newscat-stat-value">{combo.total}</span>
                      <span className="newscat-stat-label">Trades</span>
                    </span>
                  </div>
                  <span className="newscat-expand-icon">{isExpanded ? "\u25B2" : "\u25BC"}</span>
                </div>

                {/* Expanded: sources + trades */}
                {isExpanded && (
                  <div className="newscat-combo-detail">
                    {/* Sources breakdown */}
                    <div className="newscat-sources">
                      <h4>Sources</h4>
                      <div className="newscat-source-list">
                        {Object.entries(combo.sources).sort((a, b) => b[1] - a[1]).map(([src, count]) => (
                          <span key={src} className="newscat-source-tag">
                            {src} <span className="newscat-source-count">({count})</span>
                          </span>
                        ))}
                      </div>
                    </div>

                    {/* Trades list */}
                    <div className="newscat-trades">
                      <h4>Trades ({combo.trades.length})</h4>
                      <div className="compact-table">
                        <table>
                          <thead>
                            <tr>
                              <th>Date</th>
                              <th>Dir</th>
                              <th>Resultat</th>
                              <th>P&L</th>
                              <th>Source</th>
                              <th>Headline</th>
                            </tr>
                          </thead>
                          <tbody>
                            {combo.trades.map((t, i) => (
                              <tr key={`${t.timestamp}-${i}`}>
                                <td style={{ whiteSpace: "nowrap", fontSize: 12 }}>
                                  {t.timestamp ? new Date(t.timestamp).toLocaleDateString("fr-FR", { day: "2-digit", month: "short" }) : "—"}
                                </td>
                                <td>
                                  <span className={`direction-badge ${(t.direction || "").toLowerCase()}`}>
                                    {t.direction}
                                  </span>
                                </td>
                                <td>
                                  <span className={`result-badge ${t.result === "TP_HIT" || t.result === "WIN" ? "tp" : t.result === "SL_HIT" || t.result === "LOSS" ? "sl" : "expired"}`}>
                                    {t.result}
                                  </span>
                                </td>
                                <td style={{ color: pnlColor(t.pnl_pct), fontWeight: 600 }}>
                                  {t.pnl_pct != null ? `${t.pnl_pct >= 0 ? "+" : ""}${t.pnl_pct.toFixed(2)}%` : "—"}
                                </td>
                                <td style={{ fontSize: 11, color: "var(--text-secondary)" }}>{t.source}</td>
                                <td className="newscat-headline" title={t.news_headline}>
                                  {(t.news_headline || "").slice(0, 60)}{(t.news_headline || "").length > 60 ? "..." : ""}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
