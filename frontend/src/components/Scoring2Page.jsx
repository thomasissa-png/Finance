import React, { useState, useEffect, useCallback } from "react";

const LEVEL_ICONS = { INFO: "\u2139\ufe0f", WARN: "\u26a0\ufe0f", ERROR: "\u274c", DECISION: "\u26a1" };
const LEVEL_COLORS = { INFO: "var(--text-secondary)", WARN: "var(--yellow)", ERROR: "var(--red)", DECISION: "var(--cyan)" };

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

  const fetchData = useCallback(async () => {
    try {
      const [resultRes, logRes] = await Promise.all([
        fetch("/api/scoring2/result").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch("/api/agents/scoring_2/logs?limit=30")
          .then((r) => r.ok ? r.json() : []).catch(() => []),
      ]);
      setData(resultRes);
      setLogs(Array.isArray(logRes) ? logRes : []);
    } catch { /* ignore */ } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isActive) fetchData();
    const id = setInterval(fetchData, 30_000);
    return () => clearInterval(id);
  }, [isActive, fetchData]);

  const stats = data?.stats || {};
  const trendScored = data?.trend_scored || [];
  const accumulation = data?.accumulation || {};
  const byTicker = data?.by_ticker || {};

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>{"\ud83c\udfaf"} Agent Scoring 2 &mdash; Trend Scoring</h2>
        <span className="agent-page-desc">
          Re-pond&eacute;ration des news pour le trend following &mdash; multiplicateurs structurels, persistence, accumulation
        </span>
      </div>

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement...</div>}

      {/* KPIs */}
      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-value">{stats.total_items || 0}</div>
          <div className="kpi-label">News analys&eacute;es</div>
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
          Signal net accumul&eacute; &mdash; diff&eacute;rence LONG vs SHORT pondr&eacute;e par le trend score
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
          <div className="trend-no-data">Aucune donn&eacute;e &mdash; l'agent sera aliment&eacute; au prochain scan</div>
        )}
      </div>

      {/* Trend-scored news */}
      <div className="section-card">
        <h3>News re-pond&eacute;r&eacute;es pour le trend ({trendScored.length})</h3>
        <div className="agent-logs-list">
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
                <span>cat&times;{item.category_mult}</span>
                {item.persistence_mult > 1.0 && (
                  <span style={{ color: "var(--yellow)" }}>
                    persist&times;{item.persistence_mult}
                  </span>
                )}
                <span>mag:{item.magnitude} rel:{item.reliability}</span>
                <span style={{ color: "var(--text-muted)" }}>
                  {item.impacted_tickers?.join(", ")}
                </span>
              </div>
            </div>
          ))}
          {trendScored.length === 0 && !loading && (
            <div className="trend-no-data">Aucune news trend-pertinente dans le dernier scan</div>
          )}
        </div>
      </div>

      {/* Agent Logs */}
      <div className="section-card">
        <h3>Logs Agent Scoring 2</h3>
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
