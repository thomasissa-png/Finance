import React, { useState, useEffect, useCallback } from "react";
import { formatDate, formatTime, CATEGORY_COLORS, scanLabel } from "../utils/format";
import { POLL_NORMAL } from "../utils/constants";
import { apiFetch } from "../utils/api";
import { ErrorBanner, LastUpdated, LogSection } from "./shared";

const DIM_LABELS = {
  surprise: "Surprise",
  directional_clarity: "Clarté",
  transmission_delay: "Délai transmission",
  market_awareness: "Awareness marché",
  signal_reliability: "Fiabilité",
  expected_magnitude: "Magnitude",
};

export default function ScoringPage({ isActive }) {
  const [history, setHistory] = useState([]);
  const [learning, setLearning] = useState(null);
  const [logs, setLogs] = useState([]);
  const [expandedScan, setExpandedScan] = useState(null);
  const [logFilter, setLogFilter] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const [hRes, lRes, logRes] = await Promise.all([
        apiFetch("/api/scan-history?limit=20", {}, []),
        apiFetch("/api/learning", {}, null),
        apiFetch(`/api/agents/scoring/logs?limit=50${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`, {}, []),
      ]);
      setHistory(Array.isArray(hRes) ? hRes : []);
      setLearning(lRes);
      setLogs(Array.isArray(logRes) ? logRes : []);
      setError(null);
      setLastUpdate(new Date());
    } catch (err) {
      setError(err.message || "Erreur de chargement");
    } finally {
      setLoading(false);
    }
  }, [logFilter]);

  useEffect(() => {
    if (isActive) fetchData();
    const id = setInterval(fetchData, POLL_NORMAL);
    return () => clearInterval(id);
  }, [isActive, fetchData]);

  const newscatAdj = learning?.newscat_adj || {};

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>{"\ud83c\udfaf"} Agent Scoring</h2>
        <span className="agent-page-desc">Notation edge-weighted, analyse Claude, détection de signaux</span>
        <LastUpdated date={lastUpdate} />
      </div>

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement des données...</div>}
      <ErrorBanner error={error} onRetry={fetchData} />

      {/* Newscat learning adjustments */}
      {Object.keys(newscatAdj).length > 0 && (
        <div className="section-card">
          <h3>Ajustements par catégorie de news</h3>
          <div className="learning-grid">
            {Object.entries(newscatAdj)
              .sort((a, b) => Math.abs(b[1] - 1) - Math.abs(a[1] - 1))
              .map(([cat, mult]) => (
                <div key={cat} className={`learning-item ${mult >= 1 ? "boost" : "penalty"}`}>
                  <div className="learning-item-ticker">{cat}</div>
                  <div className="learning-item-mult" style={{ color: mult >= 1 ? "var(--green)" : "var(--red)" }}>
                    {mult.toFixed(3)}&times;
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
            <div className="agent-logs-empty">Aucun scan enregistré</div>
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
                      <span>{scored.length} news analysées</span>
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
                          <strong>Décision :</strong> {scan.decision_summary}
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
                                <span className="scored-news-ticker">{news.ticker || "\u2014"}</span>
                                {news.news_category && (
                                  <span className="cat-badge" style={{ backgroundColor: CATEGORY_COLORS[news.news_category] || "#90a4ae" }}>
                                    {news.news_category}
                                  </span>
                                )}
                                {news.direction && news.direction !== "NEUTRAL" && (
                                  <span className={`direction-badge ${news.direction.toLowerCase()}`}>{news.direction}</span>
                                )}
                              </div>
                              <div className="scored-news-title">{news.headline || news.title || "\u2014"}</div>

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
                              <span className="rejection-ticker">{r.ticker || "\u2014"}</span>
                              <span className="rejection-reason">{r.reason || r.rejection_reason || "\u2014"}</span>
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
      <LogSection logs={logs} logFilter={logFilter} setLogFilter={setLogFilter} title="Logs Agent Scoring" />
    </div>
  );
}
