import React, { useState, useEffect, useCallback } from "react";
import { LEVEL_ICONS, LEVEL_COLORS, POLL_NORMAL } from "../utils/constants";
import { apiFetch } from "../utils/api";
import { ErrorBanner, EmptyState, LastUpdated, LogSection } from "./shared";
import { formatPrice } from "../utils/format";

const DIR_COLORS = { LONG: "var(--green)", SHORT: "var(--red)", NEUTRAL: "var(--text-muted)" };

const CAT_COLORS = {
  weather: "var(--cyan)",
  supply_chain: "var(--yellow)",
  commodity: "var(--green)",
  geopolitical: "var(--red)",
  regulatory: "var(--text-secondary)",
};

export default function Scoring2Page({ isActive }) {
  const [data, setData] = useState(null);
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [logFilter, setLogFilter] = useState("ALL");

  const fetchData = useCallback(async () => {
    try {
      setError(null);
      const [resultRes, logRes] = await Promise.all([
        apiFetch("/api/scoring2/result", {}, null),
        apiFetch("/api/agents/scoring_2/logs?limit=30", {}, []),
      ]);
      setData(resultRes);
      const allLogs = Array.isArray(logRes) ? logRes : [];
      setLogs(logFilter === "ALL" ? allLogs : allLogs.filter((l) => l.level === logFilter));
      setLastUpdate(new Date());
    } catch (err) {
      setError(err.message || "Impossible de charger les données Scoring 2");
    } finally {
      setLoading(false);
    }
  }, [logFilter]);

  useEffect(() => {
    if (isActive) fetchData();
    const id = setInterval(fetchData, POLL_NORMAL);
    return () => clearInterval(id);
  }, [isActive, fetchData]);

  const stats = data?.stats || {};
  const trendScored = data?.trend_scored || [];
  const accumulation = data?.accumulation || {};
  const byTicker = data?.by_ticker || {};

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>{"\ud83c\udfaf"} Agent Scoring 2 — Trend Scoring</h2>
        <span className="agent-page-desc">
          Re-pondération des news pour le trend following — multiplicateurs structurels, persistence, accumulation
        </span>
        <LastUpdated date={lastUpdate} />
      </div>

      <ErrorBanner error={error} onRetry={fetchData} />

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement...</div>}

      {/* KPIs */}
      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-value">{stats.total_items || 0}</div>
          <div className="kpi-label">News analysées</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{stats.relevant_items || 0}</div>
          <div className="kpi-label">Pertinentes trend</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{stats.avg_trend_score || 0}</div>
          <div className="kpi-label">Score moyen trend</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{stats.top_category || "\u2014"}</div>
          <div className="kpi-label">Cat. dominante</div>
        </div>
      </div>

      {/* Accumulation per ticker */}
      <div className="section-card">
        <h3>Accumulation directionnelle par ticker</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 12 }}>
          Signal net accumulé — différence LONG vs SHORT pondérée par le trend score
        </p>
        <div className="trend-positions-grid">
          {Object.entries(accumulation).map(([ticker, acc]) => {
            const net = (acc.long || 0) - (acc.short || 0);
            const maxVal = Math.max(acc.long || 0, acc.short || 0, 1);
            const newsCount = (byTicker[ticker] || []).length;
            return (
              <div key={ticker} className="trend-position-card" style={{ cursor: "default" }}>
                <div className="trend-position-header">
                  <div className="trend-position-ticker">
                    <span className="trend-ticker-name">{ticker}</span>
                    <span className="trend-asset-name">{newsCount} news</span>
                  </div>
                  <div className="trend-position-dir" style={{ color: net > 0 ? "var(--green)" : net < 0 ? "var(--red)" : "var(--text-muted)" }}>
                    {net > 0 ? "\u2191 LONG" : net < 0 ? "\u2193 SHORT" : "\u2022 NEUTRAL"} ({Math.abs(net).toFixed(1)})
                  </div>
                </div>
                {/* Signal bars */}
                <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 11, color: "var(--green)", marginBottom: 2 }}>LONG {(acc.long || 0).toFixed(1)}</div>
                    <div style={{ background: "var(--bg-tertiary)", borderRadius: 3, height: 8 }}>
                      <div style={{ width: `${((acc.long || 0) / maxVal) * 100}%`, background: "var(--green)", borderRadius: 3, height: "100%" }} />
                    </div>
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 11, color: "var(--red)", marginBottom: 2 }}>SHORT {(acc.short || 0).toFixed(1)}</div>
                    <div style={{ background: "var(--bg-tertiary)", borderRadius: 3, height: 8 }}>
                      <div style={{ width: `${((acc.short || 0) / maxVal) * 100}%`, background: "var(--red)", borderRadius: 3, height: "100%" }} />
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
        {Object.keys(accumulation).length === 0 && !loading && (
          <EmptyState message="Aucune donnée" detail="L'agent sera alimenté au prochain scan" />
        )}
      </div>

      {/* Trend-scored news — desktop table */}
      <div className="section-card">
        <h3>News re-pondérées pour le trend ({trendScored.length})</h3>

        {/* Desktop view */}
        <div className="compact-table desktop-only">
          <table>
            <thead>
              <tr>
                <th>Direction</th>
                <th>Titre</th>
                <th>Source</th>
                <th>Trend</th>
                <th>Original</th>
                <th>Catégorie</th>
                <th>Cat×</th>
                <th>Persist×</th>
                <th>Mag / Rel</th>
                <th>Tickers</th>
              </tr>
            </thead>
            <tbody>
              {trendScored.slice(0, 30).map((item, i) => (
                <tr key={i}>
                  <td style={{ color: DIR_COLORS[item.direction], fontWeight: 600 }}>
                    {item.direction === "LONG" ? "\u2191" : "\u2193"} {item.direction}
                  </td>
                  <td style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {item.title}
                  </td>
                  <td style={{ color: "var(--text-secondary)", fontSize: 12 }}>{item.source}</td>
                  <td style={{ color: "var(--cyan)", fontWeight: 600 }}>{item.trend_score}</td>
                  <td style={{ color: "var(--text-secondary)" }}>{item.original_score}</td>
                  <td style={{ color: CAT_COLORS[item.news_category] || "var(--text-secondary)" }}>
                    {item.news_category}
                  </td>
                  <td>{item.category_mult}</td>
                  <td style={{ color: item.persistence_mult > 1.0 ? "var(--yellow)" : "var(--text-muted)" }}>
                    {item.persistence_mult > 1.0 ? item.persistence_mult : "\u2014"}
                  </td>
                  <td>{item.magnitude} / {item.reliability}</td>
                  <td style={{ color: "var(--text-muted)", fontSize: 12 }}>
                    {item.impacted_tickers?.join(", ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Mobile view */}
        <div className="mobile-only">
          {trendScored.slice(0, 30).map((item, i) => (
            <div key={i} className="agent-log-entry" style={{ borderLeftColor: DIR_COLORS[item.direction] }}>
              <div className="agent-log-header">
                <span style={{ fontWeight: 600 }}>
                  {item.direction === "LONG" ? "\u2191" : "\u2193"} {item.title}
                </span>
                <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                  {item.source}
                </span>
              </div>
              <div style={{ display: "flex", gap: 12, fontSize: 12, marginTop: 4, flexWrap: "wrap" }}>
                <span style={{ color: "var(--cyan)" }}>
                  Trend: <b>{item.trend_score}</b>
                </span>
                <span style={{ color: "var(--text-secondary)" }}>
                  Original: {item.original_score}
                </span>
                <span style={{ color: CAT_COLORS[item.news_category] || "var(--text-secondary)" }}>
                  {item.news_category}
                </span>
                <span>cat×{item.category_mult}</span>
                {item.persistence_mult > 1.0 && (
                  <span style={{ color: "var(--yellow)" }}>
                    persist×{item.persistence_mult}
                  </span>
                )}
                <span>mag:{item.magnitude} rel:{item.reliability}</span>
                <span style={{ color: "var(--text-muted)" }}>
                  {item.impacted_tickers?.join(", ")}
                </span>
              </div>
            </div>
          ))}
        </div>

        {trendScored.length === 0 && !loading && (
          <EmptyState message="Aucune news trend-pertinente" detail="Les données apparaîtront après le prochain scan" />
        )}
      </div>

      {/* Agent Logs — shared LogSection component */}
      <LogSection logs={logs} logFilter={logFilter} setLogFilter={setLogFilter} limit={20} />
    </div>
  );
}
