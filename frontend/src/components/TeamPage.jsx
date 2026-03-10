import React, { useState, useEffect, useCallback, useMemo } from "react";
import { formatDate, formatTime, pnlColor, RESULT_LABELS, CATEGORY_COLORS, scanLabel, tickerName, paginate, totalPages, replaceTickersInText, formatPrice } from "../utils/format";

const TEAM_CONFIG = {
  "1": {
    name: "Équipe 1",
    subtitle: "Day Trading Intraday",
    desc: "News trading event-driven, 0-1 trade par scan, TP/SL intraday",
    agents: { scoring: "scoring", trader: "trader_1", journal: "journal", learning: "learning" },
    api: {
      trades: "/api/trades",
      tradesFormat: "array",
      perf: "/api/performance/report",
      learning: "/api/learning",
      scoring: "/api/scan-history?limit=20",
      journal: "/api/journal",
    },
    perfKey: "trader_1",
  },
  "2": {
    name: "Équipe 2",
    subtitle: "Tendance Commodities",
    desc: "Trend following sur 4 commodities, positions longue durée",
    agents: { scoring: "scoring_2", trader: "trader_2", journal: "journal_2", learning: "learning_2" },
    api: {
      trades: "/api/trader2/positions",
      tradesFormat: "dict",
      perf: "/api/performance/report",
      learning: "/api/learning2/adjustments",
      scoring: null,
      journal: "/api/journal2/entries",
    },
    perfKey: "trader_2",
  },
  "3": {
    name: "Équipe 3",
    subtitle: "Indicateurs Techniques",
    desc: "Trading sur indicateurs techniques (RSI, MACD, Bollinger), positions heures à 3 jours",
    agents: { scoring: "scoring_3", trader: "trader_3", journal: "journal_3", learning: "learning_3" },
    api: {
      trades: "/api/trader3/positions",
      tradesFormat: "active_closed",
      perf: "/api/performance/report",
      learning: "/api/learning3/weekly-config",
      scoring: null,
      journal: "/api/journal3/weekly-summary",
    },
    perfKey: "trader_3",
  },
  "4": {
    name: "Équipe 4",
    subtitle: "Meta / Ensemble",
    desc: "Confluence-driven, combine les signaux des 3 équipes",
    agents: { scoring: "scoring_4", trader: "trader_4", journal: "journal_4", learning: "learning_4" },
    api: {
      trades: "/api/trader4/positions",
      tradesFormat: "dict",
      perf: "/api/performance/report",
      learning: "/api/learning4/weekly-config",
      scoring: null,
      journal: "/api/journal4/entries",
    },
    perfKey: "trader_4",
  },
};

/* Shared helper: parse API response into normalized trades array */
function parseTradesResponse(data, format) {
  if (format === "dict") {
    const values = data && typeof data === "object" && !Array.isArray(data) ? Object.values(data) : [];
    return values.map((p) => ({
      ...p,
      _isActive: !!(p.direction && p.direction !== "FLAT" && p.direction !== "NONE"),
    }));
  }
  if (format === "active_closed") {
    const active = Array.isArray(data?.active) ? data.active.map((p) => ({ ...p, _isActive: true })) : [];
    const closed = Array.isArray(data?.closed) ? data.closed.map((p) => ({ ...p, _isActive: false })) : [];
    return [...active, ...closed];
  }
  // Default: array (Team 1)
  if (Array.isArray(data)) return data.map((t) => ({ ...t, _isActive: t.result === "PENDING" }));
  return [];
}

const DIM_LABELS = {
  surprise: "Surprise",
  directional_clarity: "Clarté",
  transmission_delay: "Délai transmission",
  market_awareness: "Awareness marché",
  signal_reliability: "Fiabilité",
  expected_magnitude: "Magnitude",
};

const LEVEL_ICONS = { INFO: "i", WARN: "!", ERROR: "x", DECISION: ">" };

function AgentStatusCard({ agent }) {
  if (!agent) return null;
  const statusColor = agent.status === "working" ? "var(--accent)" : agent.status === "error" ? "var(--red)" : "var(--text-muted)";
  return (
    <div className="agent-mini-card">
      <div className="agent-mini-card-header">
        <span className="agent-mini-name">
          {agent.name?.replace(/_/g, " ")}
          {agent.version && <span className="agent-mini-version">v{agent.version}</span>}
        </span>
        <span className="agent-mini-status" style={{ backgroundColor: statusColor }} />
      </div>
      {agent.metrics && (
        <div className="agent-mini-metrics">
          {Object.entries(agent.metrics).slice(0, 4).map(([k, v]) => (
            <span key={k} className="agent-mini-metric">
              {k}: <strong>{typeof v === "number" ? (Number.isInteger(v) ? v : v.toFixed(2)) : String(v).slice(0, 20)}</strong>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function LogSection({ agentName, logFilter, setLogFilter }) {
  const [logs, setLogs] = useState([]);
  const [logError, setLogError] = useState(null);

  useEffect(() => {
    const url = `/api/agents/${agentName}/logs?limit=30${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`;
    fetch(url)
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((data) => { setLogs(Array.isArray(data) ? data : []); setLogError(null); })
      .catch((err) => { setLogs([]); setLogError(err.message); });
  }, [agentName, logFilter]);

  return (
    <div className="section-card">
      <div className="section-header">
        <h3>Logs {agentName.replace(/_/g, " ")}</h3>
        <div className="log-filter-row">
          {["ALL", "DECISION", "INFO", "WARN", "ERROR"].map((level) => (
            <button key={level} className={`log-filter-btn ${logFilter === level ? "active" : ""}`}
              onClick={() => setLogFilter(level)}>{level}</button>
          ))}
        </div>
      </div>
      {logError && <div className="agent-error-banner" style={{ margin: "8px 0", fontSize: 12 }}>Erreur chargement logs : {logError}</div>}
      <div className="agent-logs compact-logs">
        {logs.length === 0 && !logError ? (
          <div className="agent-logs-empty">Aucun log</div>
        ) : logs.slice(0, 15).map((log, i) => (
          <div key={`${log.timestamp}-${i}`} className={`agent-log-entry ${(log.level || "info").toLowerCase()}`}>
            <div className="agent-log-header">
              <span className="agent-log-icon">{LEVEL_ICONS[log.level] || "i"}</span>
              <span className="agent-log-action" style={{ color: log.level === "ERROR" ? "var(--red)" : log.level === "WARN" ? "var(--yellow)" : log.level === "DECISION" ? "var(--accent)" : "var(--text-secondary)" }}>
                {replaceTickersInText(log.action)}
              </span>
              <span className="agent-log-time">
                {log.timestamp ? new Date(log.timestamp).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : ""}
              </span>
              {log.duration_ms != null && <span className="agent-log-duration">{log.duration_ms}ms</span>}
            </div>
            {log.details && Object.keys(log.details).length > 0 && (
              <div className="agent-log-details">
                {Object.entries(log.details).slice(0, 3).map(([k, v]) => (
                  <span key={k} className="agent-log-detail">
                    <span className="agent-log-detail-key">{k}:</span>{" "}
                    {replaceTickersInText(typeof v === "object" ? JSON.stringify(v).slice(0, 80) : String(v).slice(0, 80))}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Scoring tab: shows scored news for Team 1, agent logs for others ──
function ScoringSection({ teamId }) {
  const [history, setHistory] = useState([]);
  const [expandedScan, setExpandedScan] = useState(null);
  const [learning, setLearning] = useState(null);
  const [logFilter, setLogFilter] = useState("DECISION");
  const config = TEAM_CONFIG[teamId];

  useEffect(() => {
    if (config?.api.scoring) {
      fetch(config.api.scoring).then((r) => r.ok ? r.json() : []).then((d) => setHistory(Array.isArray(d) ? d : [])).catch(() => {});
    }
    if (teamId === "1") {
      fetch("/api/learning").then((r) => r.ok ? r.json() : null).then(setLearning).catch(() => {});
    }
  }, [teamId, config?.api.scoring]);

  const newscatAdj = learning?.newscat_adj || {};

  return (
    <div>
      {/* Newscat adjustments (Team 1 only) */}
      {teamId === "1" && Object.keys(newscatAdj).length > 0 && (
        <div className="section-card">
          <h3>Ajustements par catégorie de news</h3>
          <div className="learning-grid">
            {Object.entries(newscatAdj)
              .sort((a, b) => Math.abs(b[1] - 1) - Math.abs(a[1] - 1))
              .map(([cat, mult]) => (
                <div key={cat} className={`learning-item ${mult >= 1 ? "boost" : "penalty"}`}>
                  <div className="learning-item-ticker">{cat}</div>
                  <div className="learning-item-mult" style={{ color: mult >= 1 ? "var(--green)" : "var(--red)" }}>
                    {mult.toFixed(3)}x
                  </div>
                </div>
              ))}
          </div>
        </div>
      )}

      {/* Scan history with scored news (Team 1) */}
      {config?.api.scoring && (
        <div className="section-card">
          <h3>Historique des scans ({history.length})</h3>
          <div className="scoring-history">
            {history.length === 0 ? (
              <div className="agent-logs-empty">Aucun scan enregistré. Les données apparaissent après le premier scan.</div>
            ) : history.map((scan, idx) => {
              const scored = scan.all_scored_news || [];
              const isExpanded = expandedScan === idx;
              const topNews = scored.filter((n) => (n.score || 0) > 0).sort((a, b) => (b.score || 0) - (a.score || 0));

              return (
                <div key={`${scan.timestamp}-${idx}`} className="scoring-scan-card">
                  <div className="scoring-scan-header" onClick={() => setExpandedScan(isExpanded ? null : idx)}>
                    <div className="scoring-scan-meta">
                      <span className="scoring-scan-date">{formatDate(scan.timestamp)}</span>
                      <span className="scoring-scan-time">{formatTime(scan.timestamp)}</span>
                      <span className="scoring-scan-type">{scanLabel(scan.scan_type)}</span>
                    </div>
                    <div className="scoring-scan-stats">
                      <span>{scored.length} news</span>
                      <span>{scan.has_trade ? "Trade" : "Pas de trade"}</span>
                    </div>
                    <span className="expand-icon">{isExpanded ? "▲" : "▼"}</span>
                  </div>

                  {isExpanded && (
                    <div className="scoring-scan-detail">
                      {scan.decision_summary && (
                        <div className="scoring-decision"><strong>Décision :</strong> {scan.decision_summary}</div>
                      )}
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
                                <span className="scored-news-ticker">{tickerName(news.ticker || "")}</span>
                                {news.news_category && (
                                  <span className="cat-badge" style={{ backgroundColor: CATEGORY_COLORS[news.news_category] || "#5A6F94" }}>
                                    {news.news_category}
                                  </span>
                                )}
                                {news.direction && news.direction !== "NEUTRAL" && (
                                  <span className={`direction-badge ${news.direction.toLowerCase()}`}>{news.direction}</span>
                                )}
                              </div>
                              <div className="scored-news-title">{news.headline || news.title || ""}</div>
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
                              {news.reasoning && <div className="scored-news-reasoning">{news.reasoning}</div>}
                            </div>
                          ))}
                        </div>
                      )}
                      {scan.rejection_log && scan.rejection_log.length > 0 && (
                        <div className="rejection-log">
                          <h4>Rejets ({scan.rejection_log.length})</h4>
                          {scan.rejection_log.slice(0, 10).map((r, ri) => (
                            <div key={ri} className="rejection-item">
                              <span className="rejection-ticker">{tickerName(r.ticker || "")}</span>
                              <span className="rejection-reason">{r.reason || r.rejection_reason || ""}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Scoring agent logs for all teams */}
      <LogSection agentName={config.agents.scoring} logFilter={logFilter} setLogFilter={setLogFilter} />
    </div>
  );
}

function MobileTradeCard({ t, res }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="trade-mobile-card" onClick={() => setExpanded(!expanded)} role="button" tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setExpanded(!expanded); } }} aria-expanded={expanded}>
      <div className="trade-mobile-header">
        <span className="ticker-cell">{tickerName(t.ticker)}</span>
        <span className={`direction-badge ${(t.direction || "").toLowerCase()}`}>{t.direction}</span>
        <span className={`result-badge ${res.cls}`}>{res.label}</span>
      </div>
      <div className="trade-mobile-body">
        <span>{formatDate(t.timestamp || t.entry_time)}</span>
        <span>R/R: {t.risk_reward?.toFixed(2)}</span>
        <span style={{ color: pnlColor(t.pnl_pct), fontWeight: 600 }}>
          {t.pnl_pct != null ? `${t.pnl_pct > 0 ? "+" : ""}${t.pnl_pct.toFixed(2)}%` : ""}
        </span>
      </div>
      {expanded && (
        <div className="trade-mobile-details" style={{ marginTop: 8, fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.5, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
          {t.headline && <div style={{ marginBottom: 4 }}>{t.headline}</div>}
          {t.news_category && <span className="cat-badge" style={{ backgroundColor: CATEGORY_COLORS[t.news_category] || "#5A6F94", marginRight: 6 }}>{t.news_category}</span>}
          {t.entry_price != null && <span>Entrée: {t.entry_price.toFixed(2)} | </span>}
          {t.raw_claude_score != null && <span>Score: {t.raw_claude_score.toFixed(0)}</span>}
        </div>
      )}
    </div>
  );
}

// ── Trader section with correct per-team API ──
function TraderSection({ teamId }) {
  const [trades, setTrades] = useState([]);
  const [perf, setPerf] = useState(null);
  const [livePrices, setLivePrices] = useState({});
  const [filterResult, setFilterResult] = useState("");
  const [page, setPage] = useState(1);
  const [logFilter, setLogFilter] = useState("DECISION");
  const [expandedTrade, setExpandedTrade] = useState(null);

  const config = TEAM_CONFIG[teamId];

  useEffect(() => {
    if (config?.api.trades) {
      fetch(config.api.trades)
        .then((r) => r.ok ? r.json() : (config.api.tradesFormat === "dict" ? {} : []))
        .then((d) => setTrades(parseTradesResponse(d, config.api.tradesFormat)))
        .catch(() => setTrades([]));
    }
    fetch("/api/performance/report")
      .then((r) => r.ok ? r.json() : null)
      .then((report) => {
        if (report && config?.perfKey) setPerf(report[config.perfKey] || null);
      })
      .catch(() => {});
  }, [teamId, config?.api.trades, config?.api.tradesFormat, config?.perfKey]);

  const active = trades.filter((t) => t._isActive);
  const history = trades.filter((t) => !t._isActive);

  // Fetch live prices for active positions
  const fetchLivePrices = useCallback(async () => {
    const tickers = [...new Set(active.map((p) => p.ticker).filter(Boolean))];
    if (tickers.length === 0) return;
    try {
      const res = await fetch(`/api/prices/current?tickers=${encodeURIComponent(tickers.join(","))}`);
      if (res.ok) setLivePrices(await res.json());
    } catch { /* silent */ }
  }, [active]);

  useEffect(() => {
    if (active.length === 0) return;
    fetchLivePrices();
    const id = setInterval(fetchLivePrices, 60_000);
    return () => clearInterval(id);
  }, [fetchLivePrices, active.length]);

  function computeLivePnl(entry_price, current_price, direction) {
    if (!entry_price || current_price == null) return null;
    const pct = ((current_price - entry_price) / entry_price) * 100;
    return direction === "SHORT" ? -pct : pct;
  }

  const filteredHistory = useMemo(() => {
    let list = [...history].sort((a, b) => new Date(b.timestamp || b.entry_time || b.time || 0) - new Date(a.timestamp || a.entry_time || a.time || 0));
    if (filterResult) list = list.filter((t) => t.result === filterResult);
    return list;
  }, [history, filterResult]);

  const paged = paginate(filteredHistory, page);
  const tp = totalPages(filteredHistory);
  const wrField = teamId === "2" ? "flip_win_rate" : "win_rate";
  const pnlField = teamId === "2" ? "realized_pnl" : "pnl_total";

  return (
    <div>
      {/* KPIs */}
      {perf && (
        <div className="kpi-row">
          <div className="kpi-card">
            <div className="kpi-value" style={{ color: (perf[wrField] || 0) >= 50 ? "var(--green)" : "var(--red)" }}>
              {perf[wrField] != null ? `${perf[wrField].toFixed(1)}%` : "N/A"}
            </div>
            <div className="kpi-label">Win rate</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-value" style={{ color: pnlColor(perf[pnlField]) }}>
              {perf[pnlField] != null ? `${perf[pnlField] > 0 ? "+" : ""}${perf[pnlField].toFixed(2)}%` : "N/A"}
            </div>
            <div className="kpi-label">P&L</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-value">{perf.total_trades || perf.total_flips || 0}</div>
            <div className="kpi-label">Trades</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-value" style={{ color: active.length > 0 ? "var(--accent)" : "var(--text-muted)" }}>{active.length}</div>
            <div className="kpi-label">En cours</div>
          </div>
        </div>
      )}

      {/* Active positions with full details */}
      <div className="section-card">
        <h3>Positions en cours ({active.length})</h3>
        {active.length > 0 ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {active.map((t, i) => {
              const curPrice = livePrices[t.ticker] ?? t.current_price ?? null;
              const livePnl = computeLivePnl(t.entry_price, curPrice, t.direction);
              const displayPnl = livePnl ?? t.unrealized_pnl_pct ?? t.pnl_pct ?? null;
              const isExpanded = expandedTrade === `${t.ticker}-${i}`;
              const entryTime = t.timestamp || t.entry_time || t.last_change_time;
              return (
                <div key={`${t.ticker}-${t.strategy || ""}-${i}`} className="section-card" style={{ padding: "10px 14px", cursor: "pointer", margin: 0 }}
                  onClick={() => setExpandedTrade(isExpanded ? null : `${t.ticker}-${i}`)}
                  role="button" tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter") setExpandedTrade(isExpanded ? null : `${t.ticker}-${i}`); }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ fontWeight: 700 }}>{tickerName(t.ticker)}</span>
                      <span className={`direction-badge ${(t.direction || "").toLowerCase()}`}>{t.direction}</span>
                      {t.strategy && <span style={{ fontSize: 10, padding: "1px 6px", borderRadius: 3, background: "rgba(139,157,195,0.12)", color: "var(--text-secondary)" }}>{t.strategy}</span>}
                      {t.confluence_level && <span style={{ fontSize: 10, padding: "1px 6px", borderRadius: 3, background: "rgba(236,72,153,0.12)", color: "#EC4899" }}>Confluence {t.confluence_level}/3</span>}
                      {entryTime && <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{formatDate(entryTime)} {formatTime(entryTime)}</span>}
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: 12 }}>
                      <span>Entrée: {formatPrice(t.entry_price, t.ticker)}</span>
                      <span style={{ fontWeight: 500, color: curPrice ? "var(--text-primary)" : "var(--text-muted)" }}>Actuel: {formatPrice(curPrice, t.ticker)}</span>
                      <span style={{ color: pnlColor(displayPnl), fontWeight: 700 }}>
                        {displayPnl != null ? `${displayPnl > 0 ? "+" : ""}${displayPnl.toFixed(2)}%` : "\u2014"}
                      </span>
                      <span style={{ color: "var(--text-muted)", fontSize: 10 }}>{isExpanded ? "▲" : "▼"}</span>
                    </div>
                  </div>

                  {isExpanded && (
                    <div style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid var(--border)", fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.8 }}>
                      {/* Team 1: headline + news link */}
                      {t.news_headline && (
                        <div style={{ marginBottom: 4 }}>
                          <strong>News :</strong> {t.news_headline}
                          {t.news_url && <a href={t.news_url} target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)", marginLeft: 6, fontSize: 11 }}>[source]</a>}
                        </div>
                      )}
                      {t.news_description && <div style={{ marginBottom: 4, color: "var(--text-muted)", fontSize: 11 }}>{t.news_description}</div>}
                      {t.catalyst && <div style={{ marginBottom: 4 }}><strong>Catalyseur :</strong> {t.catalyst}</div>}
                      {t.news_category && <span className="cat-badge" style={{ backgroundColor: CATEGORY_COLORS[t.news_category] || "#5A6F94", marginRight: 6 }}>{t.news_category}</span>}

                      {/* Team 2: reasoning + key catalysts */}
                      {t.reasoning && <div style={{ marginBottom: 4 }}><strong>Raisonnement :</strong> {t.reasoning}</div>}
                      {t.key_catalysts?.length > 0 && (
                        <div style={{ marginBottom: 4 }}>
                          <strong>Catalyseurs :</strong>
                          {t.key_catalysts.slice(0, 3).map((c, ci) => (
                            <div key={ci} style={{ marginLeft: 8, fontSize: 11 }}>
                              {c.title || c.headline} {c.score != null && <span style={{ color: "var(--accent)" }}>({c.score.toFixed(0)})</span>}
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Team 3: strategy + signals */}
                      {t.strategy_name && <div><strong>Stratégie :</strong> {t.strategy_name}</div>}
                      {t.signals_at_entry && (
                        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 4 }}>
                          {Object.entries(t.signals_at_entry).slice(0, 6).map(([k, v]) => (
                            <span key={k} style={{ fontSize: 10, padding: "1px 5px", borderRadius: 3, background: "rgba(139,157,195,0.08)" }}>
                              {k}: {typeof v === "number" ? v.toFixed(1) : String(v)}
                            </span>
                          ))}
                        </div>
                      )}

                      {/* Shared: pricing details */}
                      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 6 }}>
                        {t.target_price != null && <span style={{ color: "var(--green)" }}>TP: {formatPrice(t.target_price, t.ticker)}</span>}
                        {t.stop_price != null && <span style={{ color: "var(--red)" }}>SL: {formatPrice(t.stop_price, t.ticker)}</span>}
                        {t.risk_reward != null && <span>R/R: {t.risk_reward.toFixed(2)}</span>}
                        {t.raw_claude_score != null && <span>Score: {t.raw_claude_score.toFixed(0)}</span>}
                        {t.confidence != null && <span>Confiance: {t.confidence}%</span>}
                        {t.entry_time && <span style={{ color: "var(--text-muted)" }}>Depuis: {formatDate(t.entry_time)} {formatTime(t.entry_time)}</span>}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="agent-logs-empty" style={{ padding: "12px 0" }}>Aucune position ouverte</div>
        )}
      </div>

      {/* Trade history */}
      {filteredHistory.length > 0 && (
        <div className="section-card">
          <div className="section-header">
            <h3>Historique ({filteredHistory.length})</h3>
            <div className="filter-row">
              <select value={filterResult} onChange={(e) => { setFilterResult(e.target.value); setPage(1); }} className="filter-select">
                <option value="">Tous résultats</option>
                <option value="TP_HIT">TP</option>
                <option value="SL_HIT">SL</option>
                <option value="EXPIRED">Expiré</option>
              </select>
            </div>
          </div>
          <div className="compact-table">
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Actif</th>
                  <th>Dir</th>
                  <th>Raison</th>
                  <th>Entrée</th>
                  <th>Résultat</th>
                  <th>P&L</th>
                </tr>
              </thead>
              <tbody>
                {paged.map((t, i) => {
                  const res = RESULT_LABELS[t.result] || { label: t.result || "\u2014", cls: "" };
                  const reason = t.news_headline || t.catalyst || t.reason || t.reasoning || t.strategy || "";
                  return (
                    <tr key={`${t.timestamp || t.entry_time || t.time}-${t.ticker}-${i}`}>
                      <td>{formatDate(t.timestamp || t.entry_time || t.time)}</td>
                      <td className="ticker-cell">{tickerName(t.ticker)}</td>
                      <td><span className={`direction-badge ${(t.direction || "").toLowerCase()}`}>{t.direction}</span></td>
                      <td style={{ fontSize: 11, maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={reason}>{reason || "\u2014"}</td>
                      <td>{formatPrice(t.entry_price, t.ticker)}</td>
                      <td><span className={`result-badge ${res.cls}`}>{res.label}</span></td>
                      <td style={{ color: pnlColor(t.pnl_pct), fontWeight: 600 }}>
                        {t.pnl_pct != null ? `${t.pnl_pct > 0 ? "+" : ""}${t.pnl_pct.toFixed(2)}%` : ""}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {tp > 1 && (
            <div className="pagination">
              <button disabled={page <= 1} onClick={() => setPage(page - 1)}>&laquo;</button>
              <span>{page} / {tp}</span>
              <button disabled={page >= tp} onClick={() => setPage(page + 1)}>&raquo;</button>
            </div>
          )}
        </div>
      )}

      {/* Trader agent logs */}
      <LogSection agentName={config.agents.trader} logFilter={logFilter} setLogFilter={setLogFilter} />
    </div>
  );
}

// ── Journal section for all teams ──
function JournalSection({ teamId }) {
  const [entries, setEntries] = useState([]);
  const [page, setPage] = useState(1);
  const [logFilter, setLogFilter] = useState("DECISION");
  const config = TEAM_CONFIG[teamId];

  useEffect(() => {
    if (config?.api.journal) {
      fetch(config.api.journal)
        .then((r) => r.ok ? r.json() : [])
        .then((d) => {
          if (Array.isArray(d)) setEntries(d);
          else if (d?.entries) setEntries(d.entries);
          else setEntries([]);
        })
        .catch(() => {});
    }
  }, [teamId, config?.api.journal]);

  const sorted = useMemo(() =>
    [...entries].sort((a, b) => new Date(b.entry_time || b.timestamp || 0) - new Date(a.entry_time || a.timestamp || 0)),
    [entries]
  );

  const paged = paginate(sorted, page);
  const tp = totalPages(sorted);

  return (
    <div>
      {sorted.length === 0 ? (
        <div className="agent-logs-empty">
          Aucune entrée journal pour l'équipe {teamId}. Les données apparaissent après la clôture quotidienne à 22h.
        </div>
      ) : (
        <div className="section-card">
          <h3>Journal des trades ({sorted.length} entrées)</h3>
          <div className="compact-table desktop-only">
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Actif</th>
                  <th>Direction</th>
                  <th>Résultat</th>
                  <th>P&L</th>
                  <th>MAE</th>
                  <th>MFE</th>
                </tr>
              </thead>
              <tbody>
                {paged.map((e, i) => {
                  const res = RESULT_LABELS[e.result] || { label: e.result || "", cls: "" };
                  return (
                    <tr key={`${e.entry_time}-${e.ticker}-${i}`}>
                      <td>{formatDate(e.entry_time || e.timestamp)}</td>
                      <td className="ticker-cell">{tickerName(e.ticker)}</td>
                      <td><span className={`direction-badge ${(e.direction || "").toLowerCase()}`}>{e.direction}</span></td>
                      <td><span className={`result-badge ${res.cls}`}>{res.label}</span></td>
                      <td style={{ color: pnlColor(e.pnl_pct), fontWeight: 600 }}>
                        {e.pnl_pct != null ? `${e.pnl_pct > 0 ? "+" : ""}${e.pnl_pct.toFixed(2)}%` : ""}
                      </td>
                      <td style={{ color: "var(--red)" }}>{e.mae != null ? `${e.mae.toFixed(2)}%` : ""}</td>
                      <td style={{ color: "var(--green)" }}>{e.mfe != null ? `${e.mfe.toFixed(2)}%` : ""}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Mobile journal cards with expandable details */}
          <div className="mobile-only">
            {paged.map((e, i) => {
              const res = RESULT_LABELS[e.result] || { label: e.result || "", cls: "" };
              return (
                <div key={`m-${e.entry_time}-${i}`} className="trade-mobile-card">
                  <div className="trade-mobile-header">
                    <span className="ticker-cell">{tickerName(e.ticker)}</span>
                    <span className={`direction-badge ${(e.direction || "").toLowerCase()}`}>{e.direction}</span>
                    <span className={`result-badge ${res.cls}`}>{res.label}</span>
                  </div>
                  <div className="trade-mobile-body">
                    <span>{formatDate(e.entry_time || e.timestamp)}</span>
                    <span style={{ color: pnlColor(e.pnl_pct), fontWeight: 600 }}>
                      {e.pnl_pct != null ? `${e.pnl_pct > 0 ? "+" : ""}${e.pnl_pct.toFixed(2)}%` : ""}
                    </span>
                  </div>
                  {(e.mae != null || e.mfe != null) && (
                    <div style={{ display: "flex", gap: 12, marginTop: 6, fontSize: 11, color: "var(--text-muted)" }}>
                      {e.mae != null && <span>MAE: <span style={{ color: "var(--red)" }}>{e.mae.toFixed(2)}%</span></span>}
                      {e.mfe != null && <span>MFE: <span style={{ color: "var(--green)" }}>{e.mfe.toFixed(2)}%</span></span>}
                      {e.realized_rr != null && <span>R/R: {e.realized_rr.toFixed(2)}</span>}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {tp > 1 && (
            <div className="pagination">
              <button disabled={page <= 1} onClick={() => setPage(page - 1)}>&laquo;</button>
              <span>{page} / {tp}</span>
              <button disabled={page >= tp} onClick={() => setPage(page + 1)}>&raquo;</button>
            </div>
          )}
        </div>
      )}

      {/* Journal agent logs */}
      <LogSection agentName={config.agents.journal} logFilter={logFilter} setLogFilter={setLogFilter} />
    </div>
  );
}

// ── Learning section for all teams ──
function LearningSection({ teamId }) {
  const [learning, setLearning] = useState(null);
  const [logFilter, setLogFilter] = useState("DECISION");
  const config = TEAM_CONFIG[teamId];

  useEffect(() => {
    if (config?.api.learning) {
      fetch(config.api.learning).then((r) => r.ok ? r.json() : null).then(setLearning).catch(() => {});
    }
  }, [teamId, config?.api.learning]);

  if (!learning) {
    return (
      <div>
        <div className="agent-logs-empty">
          Aucune donnée de learning disponible. Les données apparaissent après le premier cycle journal + learning.
        </div>
        <LogSection agentName={config.agents.learning} logFilter={logFilter} setLogFilter={setLogFilter} />
      </div>
    );
  }

  // Team 1 & 2: adjustments-based learning
  const adjustments = learning?.adjustments || {};
  const decomp = learning?.decomposition || {};
  const sessionAdj = learning?.session_adj || {};
  const directionAdj = learning?.direction_adj || {};
  const regimeAdj = learning?.regime_adj || {};
  const delayBias = learning?.delay_bias_adj;

  // Teams 3 & 4: weekly config-based learning
  const isWeeklyConfig = teamId === "3" || teamId === "4";

  return (
    <div>
      {isWeeklyConfig ? (
        /* Weekly config display for Teams 3 & 4 */
        <div className="section-card">
          <h3>Configuration hebdomadaire</h3>
          {learning.strategies && (
            <div style={{ marginBottom: 16 }}>
              <h4 style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>Stratégies</h4>
              <div className="learning-grid">
                {Object.entries(learning.strategies || {}).map(([name, cfg]) => (
                  <div key={name} className={`learning-item ${cfg.enabled !== false ? "boost" : "penalty"}`}>
                    <div className="learning-item-ticker">{name}</div>
                    <div className="learning-item-mult" style={{ color: cfg.enabled !== false ? "var(--green)" : "var(--red)" }}>
                      {cfg.enabled !== false ? "Actif" : "Inactif"}
                    </div>
                    {cfg.weight != null && (
                      <div className="learning-item-detail">Poids: {cfg.weight.toFixed(2)}</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
          {learning.weights && (
            <div>
              <h4 style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>Poids</h4>
              <div className="learning-dim-cards">
                {Object.entries(learning.weights).map(([k, v]) => (
                  <div key={k} className="learning-dim-card">
                    <div className="learning-dim-card-label">{k}</div>
                    <div className="mult-badge" style={{ color: "var(--text-primary)" }}>
                      {typeof v === "number" ? v.toFixed(3) : String(v)}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {/* Raw config display */}
          {!learning.strategies && !learning.weights && (
            <div className="compact-table">
              <table>
                <thead><tr><th>Clé</th><th>Valeur</th></tr></thead>
                <tbody>
                  {Object.entries(learning).slice(0, 20).map(([k, v]) => (
                    <tr key={k}>
                      <td className="ticker-cell">{k}</td>
                      <td style={{ fontSize: 12 }}>{typeof v === "object" ? JSON.stringify(v).slice(0, 100) : String(v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ) : (
        /* Adjustments display for Teams 1 & 2 */
        <div>
          {/* KPIs */}
          <div className="kpi-row">
            <div className="kpi-card">
              <div className="kpi-value">{Object.keys(adjustments).length}</div>
              <div className="kpi-label">Ajustements</div>
            </div>
            <div className="kpi-card">
              <div className="kpi-value" style={{ color: "var(--green)" }}>
                {Object.values(adjustments).filter((v) => v > 1).length}
              </div>
              <div className="kpi-label">Boosts</div>
            </div>
            <div className="kpi-card">
              <div className="kpi-value" style={{ color: "var(--red)" }}>
                {Object.values(adjustments).filter((v) => v < 1).length}
              </div>
              <div className="kpi-label">Pénalités</div>
            </div>
          </div>

          {/* Per-ticker grid */}
          {Object.keys(adjustments).length > 0 && (
            <div className="section-card">
              <h3>Ajustements par ticker ({Object.keys(adjustments).length})</h3>
              <div className="learning-grid">
                {Object.entries(adjustments)
                  .sort((a, b) => Math.abs(b[1] - 1) - Math.abs(a[1] - 1))
                  .map(([ticker, mult]) => {
                    const d = decomp[ticker] || {};
                    return (
                      <div key={ticker} className={`learning-item ${mult >= 1 ? "boost" : "penalty"}`}>
                        <div className="learning-item-ticker">{tickerName(ticker)}</div>
                        <div className="learning-item-mult" style={{ color: mult >= 1 ? "var(--green)" : "var(--red)" }}>
                          {mult.toFixed(3)}x
                        </div>
                        {d.ticker_mult != null && (
                          <div className="learning-item-detail">
                            ticker: {d.ticker_mult?.toFixed(2)} | cat: {d.cat_mult?.toFixed(2)}
                          </div>
                        )}
                      </div>
                    );
                  })}
              </div>
            </div>
          )}

          {/* Other dimensions */}
          <div className="section-card">
            <h3>Dimensions supplémentaires</h3>
            <div className="learning-dimensions">
              {Object.keys(sessionAdj).length > 0 && (
                <div className="learning-dim">
                  <span className="learning-dim-label">Session</span>
                  {Object.entries(sessionAdj).map(([k, v]) => (
                    <span key={k} className="learning-dim-value" style={{ color: v >= 1 ? "var(--green)" : "var(--red)" }}>
                      {k}: {v.toFixed(3)}
                    </span>
                  ))}
                </div>
              )}
              {Object.keys(directionAdj).length > 0 && (
                <div className="learning-dim">
                  <span className="learning-dim-label">Direction</span>
                  {Object.entries(directionAdj).map(([k, v]) => (
                    <span key={k} className="learning-dim-value" style={{ color: v >= 1 ? "var(--green)" : "var(--red)" }}>
                      {k}: {v.toFixed(3)}
                    </span>
                  ))}
                </div>
              )}
              {Object.keys(regimeAdj).length > 0 && (
                <div className="learning-dim">
                  <span className="learning-dim-label">Régime VIX</span>
                  {Object.entries(regimeAdj).map(([k, v]) => (
                    <span key={k} className="learning-dim-value" style={{ color: v >= 1 ? "var(--green)" : "var(--red)" }}>
                      {k}: {v.toFixed(3)}
                    </span>
                  ))}
                </div>
              )}
              {delayBias != null && (
                <div className="learning-dim">
                  <span className="learning-dim-label">Delay Bias</span>
                  <span className="learning-dim-value" style={{ color: delayBias >= 1 ? "var(--green)" : "var(--red)" }}>
                    {delayBias.toFixed(3)}
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Learning agent logs */}
      <LogSection agentName={config.agents.learning} logFilter={logFilter} setLogFilter={setLogFilter} />
    </div>
  );
}

// ── Overview section with positions + perf + agent status + logs ──
function OverviewSection({ teamId, agents }) {
  const config = TEAM_CONFIG[teamId];
  const [trades, setTrades] = useState([]);
  const [perf, setPerf] = useState(null);
  const [livePrices, setLivePrices] = useState({});

  const agentMap = {};
  (agents || []).forEach((a) => { agentMap[a.name] = a; });
  const teamAgentNames = Object.values(config.agents);

  useEffect(() => {
    if (config?.api.trades) {
      fetch(config.api.trades)
        .then((r) => r.ok ? r.json() : (config.api.tradesFormat === "dict" ? {} : []))
        .then((d) => setTrades(parseTradesResponse(d, config.api.tradesFormat)))
        .catch(() => setTrades([]));
    }
    fetch("/api/performance/report")
      .then((r) => r.ok ? r.json() : null)
      .then((report) => {
        if (report && config?.perfKey) setPerf(report[config.perfKey] || null);
      })
      .catch(() => {});
  }, [teamId, config?.api.trades, config?.api.tradesFormat, config?.perfKey]);

  const active = trades.filter((t) => t._isActive);
  const wrField = teamId === "2" ? "flip_win_rate" : "win_rate";
  const pnlField = teamId === "2" ? "realized_pnl" : "pnl_total";

  // Fetch live prices for active positions
  const fetchLivePrices = useCallback(async () => {
    const tickers = [...new Set(active.map((p) => p.ticker).filter(Boolean))];
    if (tickers.length === 0) return;
    try {
      const res = await fetch(`/api/prices/current?tickers=${encodeURIComponent(tickers.join(","))}`);
      if (res.ok) setLivePrices(await res.json());
    } catch { /* silent */ }
  }, [active]);

  useEffect(() => {
    if (active.length === 0) return;
    fetchLivePrices();
    const id = setInterval(fetchLivePrices, 60_000);
    return () => clearInterval(id);
  }, [fetchLivePrices, active.length]);

  // Compute live P&L
  function computeLivePnl(entry_price, current_price, direction) {
    if (!entry_price || current_price == null) return null;
    const pct = ((current_price - entry_price) / entry_price) * 100;
    return direction === "SHORT" ? -pct : pct;
  }

  return (
    <div>
      {/* KPIs */}
      {perf && (
        <div className="kpi-row">
          <div className="kpi-card">
            <div className="kpi-value" style={{ color: (perf[wrField] || 0) >= 50 ? "var(--green)" : "var(--red)" }}>
              {perf[wrField] != null ? `${perf[wrField].toFixed(1)}%` : "N/A"}
            </div>
            <div className="kpi-label">Win rate</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-value" style={{ color: pnlColor(perf[pnlField]) }}>
              {perf[pnlField] != null ? `${perf[pnlField] > 0 ? "+" : ""}${perf[pnlField].toFixed(2)}%` : "N/A"}
            </div>
            <div className="kpi-label">P&L</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-value">{perf.total_trades || perf.total_flips || 0}</div>
            <div className="kpi-label">Trades</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-value" style={{ color: active.length > 0 ? "var(--accent)" : "var(--text-muted)" }}>{active.length}</div>
            <div className="kpi-label">En cours</div>
          </div>
        </div>
      )}

      {/* Open positions */}
      {active.length > 0 ? (
        <div className="section-card">
          <h3>Positions ouvertes ({active.length})</h3>
          <div className="compact-table">
            <table>
              <thead>
                <tr>
                  <th>Actif</th>
                  <th>Direction</th>
                  <th>Entrée</th>
                  <th>Actuel</th>
                  <th>P&L</th>
                  <th>Ouvert depuis</th>
                  {teamId === "1" && <th>Raison</th>}
                  {teamId === "2" && <th>Catalyseurs</th>}
                  {teamId === "3" && <th>Stratégie</th>}
                  {teamId === "4" && <th>Confluence</th>}
                </tr>
              </thead>
              <tbody>
                {active.map((t, i) => {
                  const curPrice = livePrices[t.ticker] ?? t.current_price ?? null;
                  const livePnl = computeLivePnl(t.entry_price, curPrice, t.direction);
                  const displayPnl = livePnl ?? t.unrealized_pnl_pct ?? t.pnl_pct ?? null;
                  const reason = t.news_headline || t.catalyst || t.reasoning || t.strategy || "";
                  const entryTime = t.timestamp || t.entry_time || t.last_change_time;
                  return (
                    <tr key={`${t.ticker}-${t.strategy || ""}-${i}`}>
                      <td className="ticker-cell">{tickerName(t.ticker)}</td>
                      <td><span className={`direction-badge ${(t.direction || "").toLowerCase()}`}>{t.direction}</span></td>
                      <td>{formatPrice(t.entry_price, t.ticker)}</td>
                      <td style={{ fontWeight: 500, color: curPrice ? "var(--text-primary)" : "var(--text-muted)" }}>{formatPrice(curPrice, t.ticker)}</td>
                      <td style={{ color: pnlColor(displayPnl), fontWeight: 600 }}>
                        {displayPnl != null ? `${displayPnl > 0 ? "+" : ""}${displayPnl.toFixed(2)}%` : "\u2014"}
                      </td>
                      <td style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                        {entryTime ? formatDate(entryTime) + " " + formatTime(entryTime) : "\u2014"}
                      </td>
                      <td style={{ fontSize: 11, maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={reason}>
                        {reason || "\u2014"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="section-card">
          <div className="agent-logs-empty" style={{ padding: "16px 0" }}>
            Aucune position ouverte actuellement.
          </div>
        </div>
      )}

      {/* Agent status cards */}
      <div className="agent-cards-row">
        {teamAgentNames.map((name) => (
          <AgentStatusCard key={name} agent={agentMap[name]} />
        ))}
      </div>
    </div>
  );
}

// ── Main TeamPage ──
const TABS = [
  { id: "overview", label: "Vue d'ensemble" },
  { id: "scoring", label: "Scoring" },
  { id: "trader", label: "Trader" },
  { id: "journal", label: "Journal" },
  { id: "learning", label: "Learning" },
];

export default function TeamPage({ teamId, isActive, agents, onNavigateBack }) {
  const [activeTab, setActiveTab] = useState("overview");

  const config = TEAM_CONFIG[teamId];
  if (!config) return <div className="agent-logs-empty">Équipe inconnue</div>;

  return (
    <div className="agent-page">
      <div className="page-header">
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {onNavigateBack && (
            <button className="trigger-btn" onClick={onNavigateBack} style={{ padding: "4px 10px" }}>
              ← Équipes
            </button>
          )}
          <div>
            <div className="page-title">{config.name} {config.subtitle}</div>
            <div className="page-subtitle">{config.desc}</div>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="team-tabs" style={{ position: "sticky", top: 48, zIndex: 10, background: "var(--bg-primary)", paddingBottom: 4 }}>
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={`team-tab ${activeTab === tab.id ? "active" : ""}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Overview tab — now shows positions + perf + agents + logs */}
      {activeTab === "overview" && <OverviewSection teamId={teamId} agents={agents} />}

      {/* Scoring tab */}
      {activeTab === "scoring" && <ScoringSection teamId={teamId} />}

      {/* Trader tab */}
      {activeTab === "trader" && <TraderSection teamId={teamId} />}

      {/* Journal tab */}
      {activeTab === "journal" && <JournalSection teamId={teamId} />}

      {/* Learning tab */}
      {activeTab === "learning" && <LearningSection teamId={teamId} />}
    </div>
  );
}
