import React, { useState, useEffect, useCallback } from "react";
import { LEVEL_ICONS, LEVEL_COLORS, POLL_NORMAL } from "../utils/constants";
import { apiFetch } from "../utils/api";
import { ErrorBanner, EmptyState, LastUpdated, LogSection } from "./shared";

const DIR_COLORS = { LONG: "var(--green)", SHORT: "var(--red)", NEUTRAL: "var(--text-muted)" };
const DIR_ARROWS = { LONG: "\u2191", SHORT: "\u2193", NEUTRAL: "\u2022" };

export default function Scoring3Page({ isActive }) {
  const [data, setData] = useState(null);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const logUrl = `/api/agents/scoring_3/logs?limit=50${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`;
      const [resultRes, logRes] = await Promise.all([
        apiFetch("/api/scoring3/result", {}, null),
        apiFetch(logUrl, {}, []),
      ]);
      setData(resultRes);
      setLogs(Array.isArray(logRes) ? logRes : []);
      setError(null);
      setLastUpdate(new Date());
    } catch (err) {
      setError(err.message || "Impossible de charger les donn\u00e9es Scoring 3");
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
  const setups = data?.scored_setups || [];

  if (loading && !data) {
    return (
      <div className="agent-page">
        <div className="agent-page-header">
          <h2>{"\ud83d\udcca"} Agent Scoring 3 \u2014 Indicateurs Techniques</h2>
        </div>
        <div className="kpi-row">
          {[1, 2, 3, 4].map((k) => (
            <div key={k} className="skeleton skeleton-card" />
          ))}
        </div>
        <div className="section-card">
          <div className="skeleton skeleton-card" style={{ height: 200 }} />
        </div>
      </div>
    );
  }

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>{"\ud83d\udcca"} Agent Scoring 3 \u2014 Indicateurs Techniques</h2>
        <span className="agent-page-desc">
          D\u00e9tection et scoring de setups techniques \u2014 strat\u00e9gies, timeframes, signaux directionnels
          <LastUpdated date={lastUpdate} />
        </span>
      </div>

      <ErrorBanner error={error} onRetry={fetchData} />

      {/* KPIs */}
      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-value">{stats.setups_detected || 0}</div>
          <div className="kpi-label">Setups d\u00e9tect\u00e9s</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{stats.avg_tech_score || 0}</div>
          <div className="kpi-label">Score tech moyen</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{stats.top_strategy || "\u2014"}</div>
          <div className="kpi-label">Meilleure strat\u00e9gie</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{stats.top_timeframe || "\u2014"}</div>
          <div className="kpi-label">Meilleur timeframe</div>
        </div>
      </div>

      {/* Scored technical setups */}
      <div className="section-card">
        <h3>Setups techniques scor\u00e9s ({setups.length})</h3>
        {setups.length > 0 ? (
          <>
            {/* Desktop table */}
            <table className="compact-table desktop-only">
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Strat\u00e9gie</th>
                  <th>Timeframe</th>
                  <th>Direction</th>
                  <th>Score</th>
                  <th>Entr\u00e9e</th>
                  <th>Target</th>
                  <th>Stop</th>
                </tr>
              </thead>
              <tbody>
                {setups.slice(0, 50).map((s, i) => (
                  <tr key={`${s.ticker}-${s.strategy}-${i}`}>
                    <td style={{ fontWeight: 600 }}>{s.ticker}</td>
                    <td>{s.strategy}</td>
                    <td>{s.timeframe}</td>
                    <td style={{ color: DIR_COLORS[s.direction] }}>
                      {DIR_ARROWS[s.direction] || "\u2022"} {s.direction}
                    </td>
                    <td style={{ fontWeight: 600, color: "var(--cyan)" }}>{s.score}</td>
                    <td>{s.entry_price != null ? s.entry_price.toFixed(2) : "\u2014"}</td>
                    <td style={{ color: "var(--green)" }}>{s.target_price != null ? s.target_price.toFixed(2) : "\u2014"}</td>
                    <td style={{ color: "var(--red)" }}>{s.stop_price != null ? s.stop_price.toFixed(2) : "\u2014"}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            {/* Mobile cards */}
            <div className="mobile-only">
              {setups.slice(0, 50).map((s, i) => (
                <div key={`m-${s.ticker}-${s.strategy}-${i}`} className="mobile-card">
                  <div className="mobile-card-header">
                    <span style={{ fontWeight: 600 }}>{s.ticker}</span>
                    <span style={{ color: DIR_COLORS[s.direction] }}>
                      {DIR_ARROWS[s.direction] || "\u2022"} {s.direction}
                    </span>
                    <span style={{ fontWeight: 600, color: "var(--cyan)" }}>{s.score}</span>
                  </div>
                  <div className="mobile-card-body">
                    <div className="mobile-card-row">
                      <span className="mobile-card-label">Strat\u00e9gie</span>
                      <span>{s.strategy}</span>
                    </div>
                    <div className="mobile-card-row">
                      <span className="mobile-card-label">Timeframe</span>
                      <span>{s.timeframe}</span>
                    </div>
                    <div className="mobile-card-row">
                      <span className="mobile-card-label">Entr\u00e9e</span>
                      <span>{s.entry_price != null ? s.entry_price.toFixed(2) : "\u2014"}</span>
                    </div>
                    <div className="mobile-card-row">
                      <span className="mobile-card-label">Target</span>
                      <span style={{ color: "var(--green)" }}>{s.target_price != null ? s.target_price.toFixed(2) : "\u2014"}</span>
                    </div>
                    <div className="mobile-card-row">
                      <span className="mobile-card-label">Stop</span>
                      <span style={{ color: "var(--red)" }}>{s.stop_price != null ? s.stop_price.toFixed(2) : "\u2014"}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </>
        ) : (
          !loading && <EmptyState message="Aucun setup d\u00e9tect\u00e9" detail="L'agent sera aliment\u00e9 au prochain scan" />
        )}
      </div>

      {/* Logs */}
      <LogSection logs={logs} logFilter={logFilter} setLogFilter={setLogFilter} />
    </div>
  );
}
