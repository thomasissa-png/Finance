import React, { useState, useEffect, useCallback, useMemo } from "react";
import { formatDate, formatTime, pnlColor, RESULT_LABELS, CATEGORY_COLORS, scanLabel, tickerName, paginate, totalPages } from "../utils/format";

const TEAM_CONFIG = {
  "1": {
    name: "Equipe 1 — Intraday",
    desc: "Day trading event-driven, 0-1 trade par scan, TP/SL intraday",
    agents: { scoring: "scoring", trader: "trader_1", journal: "journal", learning: "learning" },
    api: {
      trades: "/api/trades",
      perf: "/api/performance",
      learning: "/api/learning",
      scoring: "/api/scan-history?limit=20",
      journal: "/api/journal",
    },
  },
  "2": {
    name: "Equipe 2 — Tendance",
    desc: "Trend following commodities, positions longue duree",
    agents: { scoring: "scoring_2", trader: "trader_2", journal: "journal_2", learning: "learning_2" },
    api: {
      trades: "/api/trader2/positions",
      perf: "/api/performance",
      learning: "/api/learning2/adjustments",
      scoring: null,
      journal: "/api/journal2/entries",
    },
  },
  "3": {
    name: "Equipe 3 — Technique",
    desc: "Trading sur indicateurs techniques (RSI, MACD, Bollinger), positions heures a 3 jours",
    agents: { scoring: "scoring_3", trader: "trader_3", journal: "journal_3", learning: "learning_3" },
    api: {
      trades: null,
      perf: "/api/performance",
      learning: "/api/learning3/weekly-config",
      scoring: null,
      journal: "/api/journal3/weekly-summary",
    },
  },
  "4": {
    name: "Equipe 4 — Meta",
    desc: "Ensemble confluence-driven, combine les signaux des 3 equipes",
    agents: { scoring: "scoring_4", trader: "trader_4", journal: "journal_4", learning: "learning_4" },
    api: {
      trades: null,
      perf: "/api/performance",
      learning: "/api/learning4/weekly-config",
      scoring: null,
      journal: null,
    },
  },
};

const DIM_LABELS = {
  surprise: "Surprise",
  directional_clarity: "Clarte",
  transmission_delay: "Delai transmission",
  market_awareness: "Awareness marche",
  signal_reliability: "Fiabilite",
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

  useEffect(() => {
    const url = `/api/agents/${agentName}/logs?limit=30${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`;
    fetch(url).then((r) => r.ok ? r.json() : []).then((data) => setLogs(Array.isArray(data) ? data : [])).catch(() => {});
  }, [agentName, logFilter]);

  return (
    <div className="section-card">
      <div className="section-header">
        <h3>Logs — {agentName.replace(/_/g, " ")}</h3>
        <div className="log-filter-row">
          {["ALL", "DECISION", "INFO", "WARN", "ERROR"].map((level) => (
            <button key={level} className={`log-filter-btn ${logFilter === level ? "active" : ""}`}
              onClick={() => setLogFilter(level)}>{level}</button>
          ))}
        </div>
      </div>
      <div className="agent-logs compact-logs">
        {logs.length === 0 ? (
          <div className="agent-logs-empty">Aucun log</div>
        ) : logs.slice(0, 15).map((log, i) => (
          <div key={`${log.timestamp}-${i}`} className={`agent-log-entry ${(log.level || "info").toLowerCase()}`}>
            <div className="agent-log-header">
              <span className="agent-log-icon">{LEVEL_ICONS[log.level] || "i"}</span>
              <span className="agent-log-action" style={{ color: log.level === "ERROR" ? "var(--red)" : log.level === "WARN" ? "var(--yellow)" : log.level === "DECISION" ? "var(--accent)" : "var(--text-secondary)" }}>
                {log.action}
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
                    {typeof v === "object" ? JSON.stringify(v).slice(0, 80) : String(v).slice(0, 80)}
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

// ── Team 1 Scoring tab: shows scored news ──
function ScoringSection({ teamId }) {
  const [history, setHistory] = useState([]);
  const [expandedScan, setExpandedScan] = useState(null);
  const [learning, setLearning] = useState(null);

  useEffect(() => {
    if (teamId === "1") {
      fetch("/api/scan-history?limit=20").then((r) => r.ok ? r.json() : []).then((d) => setHistory(Array.isArray(d) ? d : [])).catch(() => {});
      fetch("/api/learning").then((r) => r.ok ? r.json() : null).then(setLearning).catch(() => {});
    }
  }, [teamId]);

  if (teamId !== "1") {
    return <div className="agent-logs-empty">Scoring de cette equipe : pas de news scorees directement visibles. Consultez les logs de l'agent.</div>;
  }

  const newscatAdj = learning?.newscat_adj || {};

  return (
    <div>
      {/* Newscat adjustments */}
      {Object.keys(newscatAdj).length > 0 && (
        <div className="section-card">
          <h3>Ajustements par categorie de news</h3>
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

      {/* Scan history with scored news */}
      <div className="section-card">
        <h3>Historique des scans ({history.length})</h3>
        <div className="scoring-history">
          {history.length === 0 ? (
            <div className="agent-logs-empty">Aucun scan enregistre. Les donnees apparaissent apres le premier scan.</div>
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
                      <div className="scoring-decision"><strong>Decision :</strong> {scan.decision_summary}</div>
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
    </div>
  );
}

// ── Trader section ──
function TraderSection({ teamId }) {
  const [trades, setTrades] = useState([]);
  const [perf, setPerf] = useState(null);
  const [filterResult, setFilterResult] = useState("");
  const [page, setPage] = useState(1);
  const [expandedTrade, setExpandedTrade] = useState(null);

  useEffect(() => {
    const config = TEAM_CONFIG[teamId];
    if (config?.api.trades) {
      fetch(config.api.trades).then((r) => r.ok ? r.json() : []).then((d) => setTrades(Array.isArray(d) ? d : [])).catch(() => {});
    }
    fetch("/api/performance").then((r) => r.ok ? r.json() : null).then(setPerf).catch(() => {});
  }, [teamId]);

  const filteredTrades = useMemo(() => {
    let list = [...trades].sort((a, b) => new Date(b.timestamp || b.entry_time || 0) - new Date(a.timestamp || a.entry_time || 0));
    if (filterResult) list = list.filter((t) => t.result === filterResult);
    return list;
  }, [trades, filterResult]);

  const pending = trades.filter((t) => t.result === "PENDING");
  const paged = paginate(filteredTrades, page);
  const tp = totalPages(filteredTrades);

  return (
    <div>
      {/* KPIs */}
      {perf && (
        <div className="kpi-row">
          <div className="kpi-card">
            <div className="kpi-value" style={{ color: (perf.win_rate || 0) >= 50 ? "var(--green)" : "var(--red)" }}>
              {(perf.win_rate || 0).toFixed(1)}%
            </div>
            <div className="kpi-label">Win rate</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-value" style={{ color: pnlColor(perf.total_pnl_pct) }}>
              {perf.total_pnl_pct != null ? `${perf.total_pnl_pct > 0 ? "+" : ""}${perf.total_pnl_pct.toFixed(2)}%` : "--"}
            </div>
            <div className="kpi-label">P&L total</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-value">{perf.total_trades || 0}</div>
            <div className="kpi-label">Trades</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-value">{pending.length}</div>
            <div className="kpi-label">En cours</div>
          </div>
        </div>
      )}

      {/* Pending positions */}
      {pending.length > 0 && (
        <div className="section-card">
          <h3>Positions en cours ({pending.length})</h3>
          <div className="compact-table">
            <table>
              <thead>
                <tr>
                  <th>Actif</th>
                  <th>Direction</th>
                  <th>Entree</th>
                  <th>Target</th>
                  <th>Stop</th>
                  <th>R/R</th>
                </tr>
              </thead>
              <tbody>
                {pending.map((t, i) => (
                  <tr key={`${t.ticker}-${i}`}>
                    <td className="ticker-cell">{tickerName(t.ticker)}</td>
                    <td><span className={`direction-badge ${(t.direction || "").toLowerCase()}`}>{t.direction}</span></td>
                    <td>{t.entry_price?.toFixed(2)}</td>
                    <td style={{ color: "var(--green)" }}>{t.target_price?.toFixed(2)}</td>
                    <td style={{ color: "var(--red)" }}>{t.stop_price?.toFixed(2)}</td>
                    <td>{t.risk_reward?.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Trade history */}
      <div className="section-card">
        <div className="section-header">
          <h3>Historique des trades ({filteredTrades.length})</h3>
          <div className="filter-row">
            <select value={filterResult} onChange={(e) => { setFilterResult(e.target.value); setPage(1); }} className="filter-select">
              <option value="">Tous resultats</option>
              <option value="TP_HIT">TP</option>
              <option value="SL_HIT">SL</option>
              <option value="EXPIRED">Expire</option>
            </select>
          </div>
        </div>

        {/* Desktop table */}
        <div className="compact-table desktop-only">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Actif</th>
                <th>Dir</th>
                <th>Categorie</th>
                <th>Entree</th>
                <th>R/R</th>
                <th>Score</th>
                <th>Resultat</th>
                <th>P&L</th>
              </tr>
            </thead>
            <tbody>
              {paged.map((t, i) => {
                const res = RESULT_LABELS[t.result] || { label: t.result, cls: "" };
                return (
                  <tr key={`${t.timestamp}-${t.ticker}-${i}`}>
                    <td>{formatDate(t.timestamp)}</td>
                    <td className="ticker-cell">{tickerName(t.ticker)}</td>
                    <td><span className={`direction-badge ${(t.direction || "").toLowerCase()}`}>{t.direction}</span></td>
                    <td>
                      {t.news_category && (
                        <span className="cat-badge" style={{ backgroundColor: CATEGORY_COLORS[t.news_category] || "#5A6F94" }}>
                          {t.news_category}
                        </span>
                      )}
                    </td>
                    <td>{t.entry_price?.toFixed(2)}</td>
                    <td>{t.risk_reward?.toFixed(2)}</td>
                    <td>{t.raw_claude_score?.toFixed(0) || ""}</td>
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

        {/* Mobile cards */}
        <div className="mobile-only">
          {paged.map((t, i) => {
            const res = RESULT_LABELS[t.result] || { label: t.result, cls: "" };
            return (
              <div key={`m-${t.timestamp}-${i}`} className="trade-mobile-card">
                <div className="trade-mobile-header">
                  <span className="ticker-cell">{tickerName(t.ticker)}</span>
                  <span className={`direction-badge ${(t.direction || "").toLowerCase()}`}>{t.direction}</span>
                  <span className={`result-badge ${res.cls}`}>{res.label}</span>
                </div>
                <div className="trade-mobile-body">
                  <span>{formatDate(t.timestamp)}</span>
                  <span>R/R: {t.risk_reward?.toFixed(2)}</span>
                  <span style={{ color: pnlColor(t.pnl_pct), fontWeight: 600 }}>
                    {t.pnl_pct != null ? `${t.pnl_pct > 0 ? "+" : ""}${t.pnl_pct.toFixed(2)}%` : ""}
                  </span>
                </div>
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
    </div>
  );
}

// ── Learning section ──
function LearningSection({ teamId }) {
  const [learning, setLearning] = useState(null);

  useEffect(() => {
    const url = teamId === "1" ? "/api/learning" : teamId === "2" ? "/api/learning2/adjustments" : null;
    if (url) {
      fetch(url).then((r) => r.ok ? r.json() : null).then(setLearning).catch(() => {});
    }
  }, [teamId]);

  if (!learning) {
    return <div className="agent-logs-empty">Aucune donnee de learning disponible. Les donnees apparaissent apres le premier cycle journal + learning.</div>;
  }

  const adjustments = learning?.adjustments || {};
  const decomp = learning?.decomposition || {};
  const sessionAdj = learning?.session_adj || {};
  const directionAdj = learning?.direction_adj || {};
  const regimeAdj = learning?.regime_adj || {};
  const delayBias = learning?.delay_bias_adj;

  return (
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
          <div className="kpi-label">Penalites</div>
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
        <h3>Dimensions supplementaires</h3>
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
              <span className="learning-dim-label">Regime VIX</span>
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
  );
}

// ── Main TeamPage ──
const TABS = [
  { id: "overview", label: "Vue d'ensemble" },
  { id: "scoring", label: "Scoring & News" },
  { id: "trader", label: "Trader" },
  { id: "journal", label: "Journal" },
  { id: "learning", label: "Learning" },
];

export default function TeamPage({ teamId, isActive, agents }) {
  const [activeTab, setActiveTab] = useState("overview");
  const [logFilter, setLogFilter] = useState("DECISION");

  const config = TEAM_CONFIG[teamId];
  if (!config) return <div className="agent-logs-empty">Equipe inconnue</div>;

  const agentMap = {};
  (agents || []).forEach((a) => { agentMap[a.name] = a; });

  const teamAgentNames = Object.values(config.agents);

  return (
    <div className="agent-page">
      <div className="page-header">
        <div className="page-title">{config.name}</div>
        <div className="page-subtitle">{config.desc}</div>
      </div>

      {/* Tabs */}
      <div className="team-tabs">
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

      {/* Overview tab — agents cards */}
      {activeTab === "overview" && (
        <div>
          <div className="agent-cards-row">
            {teamAgentNames.map((name) => (
              <AgentStatusCard key={name} agent={agentMap[name]} />
            ))}
          </div>
          {/* Show logs of all team agents */}
          {teamAgentNames.map((name) => (
            <LogSection key={name} agentName={name} logFilter={logFilter} setLogFilter={setLogFilter} />
          ))}
        </div>
      )}

      {/* Scoring tab */}
      {activeTab === "scoring" && <ScoringSection teamId={teamId} />}

      {/* Trader tab */}
      {activeTab === "trader" && <TraderSection teamId={teamId} />}

      {/* Journal tab */}
      {activeTab === "journal" && (
        <div>
          {teamId === "1" ? (
            <JournalSection1 />
          ) : (
            <div className="agent-logs-empty">
              Journal de l'equipe {teamId}. Les donnees apparaissent apres la cloture quotidienne a 22h.
            </div>
          )}
          <LogSection agentName={config.agents.journal} logFilter={logFilter} setLogFilter={setLogFilter} />
        </div>
      )}

      {/* Learning tab */}
      {activeTab === "learning" && (
        <div>
          <LearningSection teamId={teamId} />
          <LogSection agentName={config.agents.learning} logFilter={logFilter} setLogFilter={setLogFilter} />
        </div>
      )}
    </div>
  );
}

// ── Journal Section for Team 1 (inline, uses existing Journal component pattern) ──
function JournalSection1() {
  const [entries, setEntries] = useState([]);
  const [page, setPage] = useState(1);

  useEffect(() => {
    fetch("/api/journal")
      .then((r) => r.ok ? r.json() : [])
      .then((d) => setEntries(Array.isArray(d) ? d : []))
      .catch(() => {});
  }, []);

  const sorted = useMemo(() =>
    [...entries].sort((a, b) => new Date(b.entry_time || b.timestamp || 0) - new Date(a.entry_time || a.timestamp || 0)),
    [entries]
  );

  const paged = paginate(sorted, page);
  const tp = totalPages(sorted);

  if (sorted.length === 0) {
    return <div className="agent-logs-empty">Aucune entree journal. Les donnees apparaissent apres la cloture quotidienne a 22h.</div>;
  }

  return (
    <div className="section-card">
      <h3>Journal des trades ({sorted.length} entrees)</h3>
      <div className="compact-table desktop-only">
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th>Actif</th>
              <th>Direction</th>
              <th>Resultat</th>
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

      {/* Mobile */}
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
                <span>{formatDate(e.entry_time)}</span>
                <span style={{ color: pnlColor(e.pnl_pct), fontWeight: 600 }}>
                  {e.pnl_pct != null ? `${e.pnl_pct > 0 ? "+" : ""}${e.pnl_pct.toFixed(2)}%` : ""}
                </span>
              </div>
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
  );
}
