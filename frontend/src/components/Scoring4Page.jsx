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

export default function Scoring4Page({ isActive }) {
  const [data, setData] = useState(null);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState("ALL");
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const [resultRes, logRes] = await Promise.all([
        fetch("/api/scoring4/result").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch(`/api/agents/scoring_4/logs?limit=50${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`)
          .then((r) => r.ok ? r.json() : []).catch(() => []),
      ]);
      setData(resultRes);
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

  const stats = data?.stats || {};
  const metaScored = data?.meta_scored || [];
  const sourceWeights = data?.source_weights || {};
  const confluenceMap = data?.confluence || {};

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>{"\ud83c\udfaf"} Agent Scoring 4 &mdash; Meta-Scoring Multi-&Eacute;quipe</h2>
        <span className="agent-page-desc">
          Combinaison des signaux des &eacute;quipes 1, 2 et 3 &mdash; d&eacute;tection de confluence pour signaux haute conviction
        </span>
      </div>

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement...</div>}

      {/* KPIs */}
      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-value">{stats.signals_combined || 0}</div>
          <div className="kpi-label">Signaux combin&eacute;s</div>
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
          Pond&eacute;ration relative de chaque &eacute;quipe dans le meta-score
        </p>
        <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
          {[
            { key: "news_weight", label: "News (Eq. 1)", team: "team_1" },
            { key: "trend_weight", label: "Trend (Eq. 2)", team: "team_2" },
            { key: "tech_weight", label: "Tech (Eq. 3)", team: "team_3" },
          ].map(({ key, label, team }) => {
            const w = sourceWeights[key] || 0;
            return (
              <div key={key} style={{ flex: 1, minWidth: 120 }}>
                <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4 }}>{label}</div>
                <div style={{ background: "var(--bg-tertiary)", borderRadius: 4, height: 12, position: "relative" }}>
                  <div style={{ width: `${Math.min(w * 100, 100)}%`, background: TEAM_COLORS[team], borderRadius: 4, height: "100%" }} />
                </div>
                <div style={{ fontSize: 14, fontWeight: 600, marginTop: 4, color: TEAM_COLORS[team] }}>
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
          Signaux o&ugrave; plusieurs &eacute;quipes convergent &mdash; plus la confluence est forte, plus le signal est fiable
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
                      <span className="trend-asset-name">{level}/3 &eacute;quipes</span>
                    </div>
                    <div style={{ color: DIR_COLORS[conf.direction] }}>
                      {DIR_ARROWS[conf.direction] || "\u2022"} {conf.direction || "NEUTRAL"}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 6, marginTop: 6, flexWrap: "wrap" }}>
                    {teams.map((t, j) => (
                      <span key={j} className="badge" style={{ background: TEAM_COLORS[t] || "var(--bg-tertiary)", color: "#fff", fontSize: 11 }}>
                        {TEAM_LABELS[t] || t}
                      </span>
                    ))}
                  </div>
                  <div style={{ fontSize: 12, marginTop: 6, color: "var(--text-secondary)" }}>
                    Score meta: <b style={{ color: "var(--cyan)" }}>{conf.meta_score || 0}</b>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          !loading && <div className="trend-no-data">Aucune confluence d&eacute;tect&eacute;e &mdash; les &eacute;quipes doivent &ecirc;tre actives</div>
        )}
      </div>

      {/* Recent meta-scored items */}
      <div className="section-card">
        <h3>Signaux meta-scor&eacute;s r&eacute;cents ({metaScored.length})</h3>
        {metaScored.length > 0 ? (
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
                <tr key={i}>
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
        ) : (
          !loading && <div className="trend-no-data">Aucun signal meta-scor&eacute; &mdash; l'agent sera aliment&eacute; au prochain scan</div>
        )}
      </div>

      {/* Logs */}
      <div className="section-card">
        <h3>Logs Agent Scoring 4</h3>
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
