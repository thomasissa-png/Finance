import React, { useState, useEffect, useCallback } from "react";
import { formatDate, formatTime, CATEGORY_COLORS, scanLabel } from "../utils/format";

const LEVEL_ICONS = { INFO: "\u2139\ufe0f", WARN: "\u26a0\ufe0f", ERROR: "\u274c", DECISION: "\u26a1" };
const LEVEL_COLORS = { INFO: "var(--text-secondary)", WARN: "var(--yellow)", ERROR: "var(--red)", DECISION: "var(--cyan)" };

const DIM_LABELS = {
  surprise: "Surprise",
  directional_clarity: "Clart\u00e9",
  transmission_delay: "D\u00e9lai transmission",
  market_awareness: "Awareness march\u00e9",
  signal_reliability: "Fiabilit\u00e9",
  expected_magnitude: "Magnitude",
};

export default function ScoringPage({ isActive }) {
  const [history, setHistory] = useState([]);
  const [learning, setLearning] = useState(null);
  const [logs, setLogs] = useState([]);
  const [expandedScan, setExpandedScan] = useState(null);
  const [logFilter, setLogFilter] = useState("ALL");

  const fetchData = useCallback(async () => {
    const [hRes, lRes, logRes] = await Promise.all([
      fetch("/api/scan-history?limit=20").then((r) => r.json()).catch(() => []),
      fetch("/api/learning").then((r) => r.json()).catch(() => null),
      fetch(`/api/agents/scoring/logs?limit=50${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`)
        .then((r) => r.json()).catch(() => []),
    ]);
    setHistory(Array.isArray(hRes) ? hRes : []);
    setLearning(lRes);
    setLogs(Array.isArray(logRes) ? logRes : []);
  }, [logFilter]);

  useEffect(() => {
    if (isActive) fetchData();
    const id = setInterval(fetchData, 60_000);
    return () => clearInterval(id);
  }, [isActive, fetchData]);

  const newscatAdj = learning?.newscat_adj || {};

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>\ud83c\udfaf Agent Scoring</h2>
        <span className="agent-page-desc">Notation edge-weighted, analyse Claude, d\u00e9tection de signaux</span>
      </div>

      {/* Newscat learning adjustments */}
      {Object.keys(newscatAdj).length > 0 && (
        <div className="section-card">
          <h3>Ajustements par cat\u00e9gorie de news</h3>
          <div className="learning-grid">
            {Object.entries(newscatAdj)
              .sort((a, b) => Math.abs(b[1] - 1) - Math.abs(a[1] - 1))
              .map(([cat, mult]) => (
                <div key={cat} className={`learning-item ${mult >= 1 ? "boost" : "penalty"}`}>
                  <div className="learning-item-ticker">{cat}</div>
                  <div className="learning-item-mult" style={{ color: mult >= 1 ? "var(--green)" : "var(--red)" }}>
                    {mult.toFixed(3)}\u00d7
                  </div>
                </div>
              ))}
          </div>
        </div>
      )}

      {/* Scan history */}
      <div className="section-card">
        <h3>Historique des scans ({history.length})</h3>
        <div className="scoring-history">
          {history.length === 0 ? (
            <div className="agent-logs-empty">Aucun scan enregistr\u00e9</div>
          ) : (
            history.map((scan, idx) => {
              const scored = scan.all_scored_news || [];
              const isExpanded = expandedScan === idx;
              const topNews = scored.filter((n) => (n.score || 0) > 0).sort((a, b) => (b.score || 0) - (a.score || 0));
              const avgScore = scored.length > 0
                ? (scored.reduce((s, n) => s + (n.score || 0), 0) / scored.length).toFixed(1)
                : "0";

              return (
                <div key={`${scan.timestamp}-${idx}`} className="scoring-scan-card">
                  <div className="scoring-scan-header" onClick={() => setExpandedScan(isExpanded ? null : idx)}>
                    <div className="scoring-scan-meta">
                      <span className="scoring-scan-date">{formatDate(scan.timestamp)}</span>
                      <span className="scoring-scan-time">{formatTime(scan.timestamp)}</span>
                      <span className="scoring-scan-type">{scanLabel(scan.scan_type)}</span>
                    </div>
                    <div className="scoring-scan-stats">
                      <span>{scored.length} news analys\u00e9es</span>
                      <span>Score moy: {avgScore}</span>
                      <span>{scan.has_trade ? "\u2705 Trade" : "\u274c Pas de trade"}</span>
                    </div>
                    <span className="expand-icon">{isExpanded ? "\u25b2" : "\u25bc"}</span>
                  </div>

                  {isExpanded && (
                    <div className="scoring-scan-detail">
                      {/* Decision summary */}
                      {scan.decision_summary && (
                        <div className="scoring-decision">
                          <strong>D\u00e9cision :</strong> {scan.decision_summary}
                        </div>
                      )}

                      {/* Scored news list */}
                      {topNews.length > 0 && (
                        <div className="scored-news-list">
                          {topNews.map((news, ni) => (
                            <div key={ni} className="scored-news-item">
                              <div className="scored-news-header">
                                <span className="scored-news-score" style={{
                                  color: (news.score || 0) >= 50 ? "var(--green)" : (news.score || 0) >= 20 ? "var(--yellow)" : "var(--text-muted)"
                                }}>
                                  {(news.score || 0).toFixed(0)}
                                </span>
                                <span className="scored-news-ticker">{news.ticker || "—"}</span>
                                {news.news_category && (
                                  <span className="cat-badge" style={{ backgroundColor: CATEGORY_COLORS[news.news_category] || "#90a4ae" }}>
                                    {news.news_category}
                                  </span>
                                )}
                                {news.direction && news.direction !== "NEUTRAL" && (
                                  <span className={`direction-badge ${news.direction.toLowerCase()}`}>{news.direction}</span>
                                )}
                              </div>
                              <div className="scored-news-title">{news.headline || news.title || "—"}</div>

                              {/* Scoring dimensions */}
                              <div className="scoring-dims">
                                {Object.entries(DIM_LABELS).map(([key, label]) => {
                                  const val = news[key];
                                  if (val == null) return null;
                                  return (
                                    <div key={key} className="scoring-dim">
                                      <span className="scoring-dim-label">{label}</span>
                                      <div className="scoring-dim-bar">
                                        <div className="scoring-dim-fill" style={{ width: `${val}%`, backgroundColor: val >= 60 ? "var(--green)" : val >= 30 ? "var(--yellow)" : "var(--red)" }} />
                                      </div>
                                      <span className="scoring-dim-val">{val}</span>
                                    </div>
                                  );
                                })}
                              </div>

                              {news.reasoning && (
                                <div className="scored-news-reasoning">{news.reasoning}</div>
                              )}
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Rejection log */}
                      {scan.rejection_log && scan.rejection_log.length > 0 && (
                        <div className="rejection-log">
                          <h4>Rejets ({scan.rejection_log.length})</h4>
                          {scan.rejection_log.slice(0, 10).map((r, ri) => (
                            <div key={ri} className="rejection-item">
                              <span className="rejection-ticker">{r.ticker || "—"}</span>
                              <span className="rejection-reason">{r.reason || r.rejection_reason || "—"}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Agent logs */}
      <div className="section-card">
        <div className="section-header">
          <h3>Logs Agent Scoring</h3>
          <div className="log-filter-row">
            {["ALL", "DECISION", "INFO", "WARN", "ERROR"].map((level) => (
              <button key={level} className={`log-filter-btn ${logFilter === level ? "active" : ""}`}
                onClick={() => setLogFilter(level)}>
                {level}
              </button>
            ))}
          </div>
        </div>
        <div className="agent-logs compact-logs">
          {logs.length === 0 ? (
            <div className="agent-logs-empty">Aucun log</div>
          ) : (
            logs.slice(0, 20).map((log, i) => (
              <div key={`${log.timestamp}-${i}`} className={`agent-log-entry ${(log.level || "info").toLowerCase()}`}>
                <div className="agent-log-header">
                  <span className="agent-log-icon">{LEVEL_ICONS[log.level] || "\u2139\ufe0f"}</span>
                  <span className="agent-log-action" style={{ color: LEVEL_COLORS[log.level] }}>{log.action}</span>
                  <span className="agent-log-time">
                    {log.timestamp ? new Date(log.timestamp).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : ""}
                  </span>
                </div>
                {log.details && Object.keys(log.details).length > 0 && (
                  <div className="agent-log-details">
                    {Object.entries(log.details).slice(0, 3).map(([k, v]) => (
                      <span key={k} className="agent-log-detail">
                        <span className="agent-log-detail-key">{k}:</span>{" "}
                        {typeof v === "object" ? JSON.stringify(v).slice(0, 80) : String(v).slice(0, 80)}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
