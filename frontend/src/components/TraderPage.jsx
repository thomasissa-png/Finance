import React, { useState, useEffect, useCallback, useMemo } from "react";
import { formatDate, formatTime, pnlColor, RESULT_LABELS, CATEGORY_COLORS, scanLabel, paginate, totalPages, PAGE_SIZE } from "../utils/format";

const LEVEL_ICONS = { INFO: "\u2139\ufe0f", WARN: "\u26a0\ufe0f", ERROR: "\u274c", DECISION: "\u26a1" };
const LEVEL_COLORS = { INFO: "var(--text-secondary)", WARN: "var(--yellow)", ERROR: "var(--red)", DECISION: "var(--cyan)" };

export default function TraderPage({ isActive }) {
  const [trades, setTrades] = useState([]);
  const [perf, setPerf] = useState(null);
  const [learning, setLearning] = useState(null);
  const [logs, setLogs] = useState([]);
  const [filterResult, setFilterResult] = useState("");
  const [filterDirection, setFilterDirection] = useState("");
  const [filterCategory, setFilterCategory] = useState("");
  const [page, setPage] = useState(1);
  const [logFilter, setLogFilter] = useState("DECISION");
  const [expandedTrade, setExpandedTrade] = useState(null);

  const fetchData = useCallback(async () => {
    const [tRes, pRes, lRes, logRes] = await Promise.all([
      fetch("/api/trades").then((r) => r.json()).catch(() => []),
      fetch("/api/performance").then((r) => r.json()).catch(() => null),
      fetch("/api/learning").then((r) => r.json()).catch(() => null),
      fetch(`/api/agents/trader_1/logs?limit=50${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`)
        .then((r) => r.json()).catch(() => []),
    ]);
    setTrades(Array.isArray(tRes) ? tRes : []);
    setPerf(pRes);
    setLearning(lRes);
    setLogs(Array.isArray(logRes) ? logRes : []);
  }, [logFilter]);

  useEffect(() => {
    if (isActive) fetchData();
    const id = setInterval(fetchData, 60_000);
    return () => clearInterval(id);
  }, [isActive, fetchData]);

  // Filter & sort trades
  const filteredTrades = useMemo(() => {
    let list = [...trades].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
    if (filterResult) list = list.filter((t) => t.result === filterResult);
    if (filterDirection) list = list.filter((t) => (t.direction || "").toUpperCase() === filterDirection);
    if (filterCategory) list = list.filter((t) => t.news_category === filterCategory);
    return list;
  }, [trades, filterResult, filterDirection, filterCategory]);

  const pending = trades.filter((t) => t.result === "PENDING");
  const paged = paginate(filteredTrades, page);
  const tp = totalPages(filteredTrades);

  // Learning decomposition
  const decomp = learning?.decomposition || {};
  const adjustments = learning?.adjustments || {};

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>\ud83d\udcb9 Agent Trader</h2>
        <span className="agent-page-desc">D\u00e9cisions d'investissement, position monitoring, risk management</span>
      </div>

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
            <div className="kpi-value">{perf.wins || 0} / {perf.losses || 0}</div>
            <div className="kpi-label">Gains / Pertes</div>
          </div>
        </div>
      )}

      {/* Pending trades */}
      {pending.length > 0 && (
        <div className="section-card">
          <h3>Positions en cours ({pending.length})</h3>
          <div className="compact-table">
            <table>
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Direction</th>
                  <th>Entr\u00e9e</th>
                  <th>Target</th>
                  <th>Stop</th>
                  <th>R/R</th>
                  <th>Confiance</th>
                </tr>
              </thead>
              <tbody>
                {pending.map((t, i) => (
                  <tr key={`${t.ticker}-${t.timestamp}-${i}`}>
                    <td className="ticker-cell">{t.ticker}</td>
                    <td><span className={`direction-badge ${(t.direction || "").toLowerCase()}`}>{t.direction}</span></td>
                    <td>{t.entry_price?.toFixed(2)}</td>
                    <td style={{ color: "var(--green)" }}>{t.target_price?.toFixed(2)}</td>
                    <td style={{ color: "var(--red)" }}>{t.stop_price?.toFixed(2)}</td>
                    <td>{t.risk_reward?.toFixed(2)}</td>
                    <td>{t.confidence}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Trades history */}
      <div className="section-card">
        <div className="section-header">
          <h3>Historique des trades ({filteredTrades.length})</h3>
          <div className="filter-row">
            <select value={filterResult} onChange={(e) => { setFilterResult(e.target.value); setPage(1); }} className="filter-select">
              <option value="">Tous r\u00e9sultats</option>
              <option value="TP_HIT">TP</option>
              <option value="SL_HIT">SL</option>
              <option value="EXPIRED">Expir\u00e9</option>
            </select>
            <select value={filterDirection} onChange={(e) => { setFilterDirection(e.target.value); setPage(1); }} className="filter-select">
              <option value="">Toutes directions</option>
              <option value="LONG">LONG</option>
              <option value="SHORT">SHORT</option>
            </select>
            <select value={filterCategory} onChange={(e) => { setFilterCategory(e.target.value); setPage(1); }} className="filter-select">
              <option value="">Toutes cat\u00e9gories</option>
              {Object.keys(CATEGORY_COLORS).map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        </div>
        <div className="compact-table desktop-only">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Scan</th>
                <th>Ticker</th>
                <th>Dir</th>
                <th>Cat\u00e9gorie</th>
                <th>Entr\u00e9e</th>
                <th>R/R</th>
                <th>Score</th>
                <th>Learn.\u00d7</th>
                <th>R\u00e9sultat</th>
                <th>P&L</th>
              </tr>
            </thead>
            <tbody>
              {paged.map((t, i) => {
                const res = RESULT_LABELS[t.result] || { label: t.result, cls: "" };
                return (
                  <tr key={`${t.timestamp}-${t.ticker}-${i}`}
                    className={expandedTrade === i ? "expanded-row" : ""}
                    onClick={() => setExpandedTrade(expandedTrade === i ? null : i)}>
                    <td>{formatDate(t.timestamp)}</td>
                    <td className="scan-cell">{scanLabel(t.scan_type)}</td>
                    <td className="ticker-cell">{t.ticker}</td>
                    <td><span className={`direction-badge ${(t.direction || "").toLowerCase()}`}>{t.direction}</span></td>
                    <td>
                      <span className="cat-badge" style={{ backgroundColor: CATEGORY_COLORS[t.news_category] || "#90a4ae" }}>
                        {t.news_category || "—"}
                      </span>
                    </td>
                    <td>{t.entry_price?.toFixed(2)}</td>
                    <td>{t.risk_reward?.toFixed(2)}</td>
                    <td>{t.raw_claude_score?.toFixed(0) || "—"}</td>
                    <td style={{ color: (t.learning_multiplier || 1) >= 1 ? "var(--green)" : "var(--red)" }}>
                      {t.learning_multiplier?.toFixed(2) || "1.00"}
                    </td>
                    <td><span className={`result-badge ${res.cls}`}>{res.label}</span></td>
                    <td style={{ color: pnlColor(t.pnl_pct), fontWeight: 600 }}>
                      {t.pnl_pct != null ? `${t.pnl_pct > 0 ? "+" : ""}${t.pnl_pct.toFixed(2)}%` : "—"}
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
                  <span className="ticker-cell">{t.ticker}</span>
                  <span className={`direction-badge ${(t.direction || "").toLowerCase()}`}>{t.direction}</span>
                  <span className={`result-badge ${res.cls}`}>{res.label}</span>
                </div>
                <div className="trade-mobile-body">
                  <span>{formatDate(t.timestamp)}</span>
                  <span>R/R: {t.risk_reward?.toFixed(2)}</span>
                  <span style={{ color: pnlColor(t.pnl_pct), fontWeight: 600 }}>
                    {t.pnl_pct != null ? `${t.pnl_pct > 0 ? "+" : ""}${t.pnl_pct.toFixed(2)}%` : "—"}
                  </span>
                </div>
              </div>
            );
          })}
        </div>

        {/* Pagination */}
        {tp > 1 && (
          <div className="pagination">
            <button disabled={page <= 1} onClick={() => setPage(page - 1)}>&laquo;</button>
            <span>{page} / {tp}</span>
            <button disabled={page >= tp} onClick={() => setPage(page + 1)}>&raquo;</button>
          </div>
        )}
      </div>

      {/* Learning adjustments */}
      {learning && Object.keys(adjustments).length > 0 && (
        <div className="section-card">
          <h3>Ajustements Learning actifs</h3>
          <div className="learning-grid">
            {Object.entries(adjustments)
              .sort((a, b) => Math.abs(b[1] - 1) - Math.abs(a[1] - 1))
              .map(([ticker, mult]) => {
                const d = decomp[ticker] || {};
                const isBoost = mult >= 1;
                return (
                  <div key={ticker} className={`learning-item ${isBoost ? "boost" : "penalty"}`}>
                    <div className="learning-item-ticker">{ticker}</div>
                    <div className="learning-item-mult" style={{ color: isBoost ? "var(--green)" : "var(--red)" }}>
                      {mult.toFixed(3)}\u00d7
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

          {/* Other dimensions */}
          <div className="learning-dimensions">
            {learning.session_adj && Object.keys(learning.session_adj).length > 0 && (
              <div className="learning-dim">
                <span className="learning-dim-label">Session</span>
                {Object.entries(learning.session_adj).map(([k, v]) => (
                  <span key={k} className="learning-dim-value" style={{ color: v >= 1 ? "var(--green)" : "var(--red)" }}>
                    {k}: {v.toFixed(3)}
                  </span>
                ))}
              </div>
            )}
            {learning.direction_adj && Object.keys(learning.direction_adj).length > 0 && (
              <div className="learning-dim">
                <span className="learning-dim-label">Direction</span>
                {Object.entries(learning.direction_adj).map(([k, v]) => (
                  <span key={k} className="learning-dim-value" style={{ color: v >= 1 ? "var(--green)" : "var(--red)" }}>
                    {k}: {v.toFixed(3)}
                  </span>
                ))}
              </div>
            )}
            {learning.delay_bias_adj != null && (
              <div className="learning-dim">
                <span className="learning-dim-label">Delay Bias</span>
                <span className="learning-dim-value" style={{ color: learning.delay_bias_adj >= 1 ? "var(--green)" : "var(--red)" }}>
                  {learning.delay_bias_adj.toFixed(3)}
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Agent logs */}
      <div className="section-card">
        <div className="section-header">
          <h3>Logs Agent Trader</h3>
          <div className="log-filter-row">
            {["DECISION", "ALL", "INFO", "WARN", "ERROR"].map((level) => (
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
                  {log.duration_ms != null && <span className="agent-log-duration">{log.duration_ms}ms</span>}
                </div>
                {log.details && Object.keys(log.details).length > 0 && (
                  <div className="agent-log-details">
                    {Object.entries(log.details).slice(0, 4).map(([k, v]) => (
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
