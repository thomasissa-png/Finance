import React, { useState, useEffect, useCallback } from "react";
import { TEAM_COLORS, LEVEL_ICONS, LEVEL_COLORS, POLL_NORMAL } from "../utils/constants";
import { apiFetch } from "../utils/api";
import { ErrorBanner, EmptyState, LastUpdated, LogSection } from "./shared";

const DIR_COLORS = { LONG: "var(--green)", SHORT: "var(--red)", NEUTRAL: "var(--text-muted)" };
const DIR_ARROWS = { LONG: "\u2191", SHORT: "\u2193", NEUTRAL: "\u2022" };

const SOURCE_TEAMS = {
  team_1: { label: "News (Éq. 1)", key: "news_weight", colorKey: "1" },
  team_2: { label: "Trend (Éq. 2)", key: "trend_weight", colorKey: "2" },
  team_3: { label: "Tech (Éq. 3)", key: "tech_weight", colorKey: "3" },
};

const TEAM_BADGE_LABELS = {
  team_1: "News (1)",
  team_2: "Trend (2)",
  team_3: "Tech (3)",
};

function SkeletonRow({ cols }) {
  return (
    <tr>
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i}><span className="skeleton-line" style={{ width: `${50 + Math.random() * 40}%` }} /></td>
      ))}
    </tr>
  );
}

function SkeletonBlock({ lines = 3 }) {
  return (
    <div className="section-card">
      <div className="skeleton-line" style={{ width: "30%", height: 18, marginBottom: 12 }} />
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="skeleton-line" style={{ width: `${60 + Math.random() * 30}%`, height: 14, marginBottom: 8 }} />
      ))}
    </div>
  );
}

export default function Scoring4Page({ isActive }) {
  const [data, setData] = useState(null);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      setError(null);
      const logUrl = `/api/agents/scoring_4/logs?limit=50${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`;
      const [resultRes, logRes] = await Promise.all([
        apiFetch("/api/scoring4/result", {}, null),
        apiFetch(logUrl, {}, []),
      ]);
      setData(resultRes);
      setLogs(Array.isArray(logRes) ? logRes : []);
      setLastUpdate(new Date());
    } catch (err) {
      setError(err.message || "Impossible de charger les données Scoring 4");
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
  const metaScored = data?.meta_scored || [];
  const sourceWeights = data?.source_weights || {};
  const confluenceMap = data?.confluence || {};

  if (loading) {
    return (
      <div className="agent-page">
        <div className="agent-page-header">
          <h2>{"\ud83c\udfaf"} Agent Scoring 4 — Meta-Scoring Multi-Équipe</h2>
        </div>
        <div className="kpi-row">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="kpi-card">
              <div className="skeleton-line" style={{ width: "50%", height: 24, margin: "0 auto 8px" }} />
              <div className="skeleton-line" style={{ width: "70%", height: 12, margin: "0 auto" }} />
            </div>
          ))}
        </div>
        <SkeletonBlock lines={4} />
        <SkeletonBlock lines={5} />
      </div>
    );
  }

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <h2>{"\ud83c\udfaf"} Agent Scoring 4 — Meta-Scoring Multi-Équipe</h2>
          <LastUpdated date={lastUpdate} />
        </div>
        <span className="agent-page-desc">
          Combinaison des signaux des équipes 1, 2 et 3 — détection de confluence pour signaux haute conviction
        </span>
      </div>

      <ErrorBanner error={error} onRetry={fetchData} />

      {/* KPIs */}
      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-value">{stats.signals_combined || 0}</div>
          <div className="kpi-label">Signaux combinés</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{stats.confluence_rate != null ? `${stats.confluence_rate}%` : "\u2014"}</div>
          <div className="kpi-label">Taux de confluence</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{stats.avg_meta_score || 0}</div>
          <div className="kpi-label">Score meta moyen</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{stats.active_sources || 0}/3</div>
          <div className="kpi-label">Sources actives</div>
        </div>
      </div>

      {/* Source weights */}
      <div className="section-card">
        <h3>Poids des sources</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 12 }}>
          Pondération relative de chaque équipe dans le meta-score
        </p>
        <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
          {Object.entries(SOURCE_TEAMS).map(([teamKey, { label, key, colorKey }]) => {
            const w = sourceWeights[key] || 0;
            const color = TEAM_COLORS[colorKey] || "var(--text-muted)";
            return (
              <div key={teamKey} style={{ flex: 1, minWidth: 120 }}>
                <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4 }}>{label}</div>
                <div style={{ background: "var(--bg-tertiary)", borderRadius: 4, height: 12, position: "relative" }}>
                  <div style={{ width: `${Math.min(w * 100, 100)}%`, background: color, borderRadius: 4, height: "100%" }} />
                </div>
                <div style={{ fontSize: 14, fontWeight: 600, marginTop: 4, color }}>
                  {(w * 100).toFixed(0)}%
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Confluence visualization */}
      <div className="section-card">
        <h3>Carte de confluence</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 12 }}>
          Signaux où plusieurs équipes convergent — plus la confluence est forte, plus le signal est fiable
        </p>
        {Object.keys(confluenceMap).length > 0 ? (
          <div className="trend-positions-grid">
            {Object.entries(confluenceMap).map(([ticker, conf]) => {
              const teams = conf.teams || [];
              const level = teams.length;
              const bgOpacity = level === 3 ? 0.25 : level === 2 ? 0.15 : 0.05;
              return (
                <div key={ticker} className="trend-position-card" style={{
                  cursor: "default",
                  borderLeft: `3px solid ${level === 3 ? "var(--green)" : level === 2 ? "var(--yellow)" : "var(--text-muted)"}`,
                  background: `rgba(255,255,255,${bgOpacity})`,
                }}>
                  <div className="trend-position-header">
                    <div className="trend-position-ticker">
                      <span className="trend-ticker-name">{ticker}</span>
                      <span className="trend-asset-name">{level}/3 équipes</span>
                    </div>
                    <div style={{ color: DIR_COLORS[conf.direction] }}>
                      {DIR_ARROWS[conf.direction] || "\u2022"} {conf.direction || "NEUTRAL"}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 6, marginTop: 6, flexWrap: "wrap" }}>
                    {teams.map((t, j) => {
                      const cKey = t.replace("team_", "");
                      return (
                        <span key={j} className="badge" style={{ background: TEAM_COLORS[cKey] || "var(--bg-tertiary)", color: "#fff", fontSize: 11 }}>
                          {TEAM_BADGE_LABELS[t] || t}
                        </span>
                      );
                    })}
                  </div>
                  <div style={{ fontSize: 12, marginTop: 6, color: "var(--text-secondary)" }}>
                    Score meta: <b style={{ color: "var(--cyan)" }}>{conf.meta_score || 0}</b>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <EmptyState message="Aucune confluence détectée" detail="Les équipes doivent être actives pour produire des signaux convergents" />
        )}
      </div>

      {/* Recent meta-scored items */}
      <div className="section-card">
        <h3>Signaux meta-scorés récents ({metaScored.length})</h3>
        {metaScored.length > 0 ? (
          <div className="table-responsive">
            <table className="compact-table">
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Direction</th>
                  <th>Meta Score</th>
                  <th>Confluence</th>
                  <th>News</th>
                  <th>Trend</th>
                  <th>Tech</th>
                </tr>
              </thead>
              <tbody>
                {metaScored.slice(0, 30).map((item, i) => (
                  <tr key={`${item.ticker}-${i}`}>
                    <td style={{ fontWeight: 600 }}>{item.ticker}</td>
                    <td style={{ color: DIR_COLORS[item.direction] }}>
                      {DIR_ARROWS[item.direction] || "\u2022"} {item.direction}
                    </td>
                    <td style={{ fontWeight: 600, color: "var(--cyan)" }}>{item.meta_score || 0}</td>
                    <td>{item.confluence_level || 0}/3</td>
                    <td style={{ color: item.news_signal ? "var(--cyan)" : "var(--text-muted)" }}>
                      {item.news_score != null ? item.news_score : "\u2014"}
                    </td>
                    <td style={{ color: item.trend_signal ? "var(--green)" : "var(--text-muted)" }}>
                      {item.trend_score != null ? item.trend_score : "\u2014"}
                    </td>
                    <td style={{ color: item.tech_signal ? "var(--yellow)" : "var(--text-muted)" }}>
                      {item.tech_score != null ? item.tech_score : "\u2014"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState message="Aucun signal meta-scoré" detail="L'agent sera alimenté au prochain scan" />
        )}
      </div>

      {/* Logs */}
      <LogSection logs={logs} logFilter={logFilter} setLogFilter={setLogFilter} />
    </div>
  );
}
