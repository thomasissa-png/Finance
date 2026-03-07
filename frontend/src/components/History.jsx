import React, { useEffect, useState, useMemo, useCallback } from "react";
import {
  RESULT_LABELS, CATEGORY_COLORS, formatDateTime, formatPnl,
  pnlColor, pnlClass, scanLabel, timeAgo, paginate, totalPages,
} from "../utils/format";

/* ── Trades table (desktop) ──────────────────────────────── */

function TradesTable({ trades, page, setPage }) {
  const pages = totalPages(trades);
  const visible = useMemo(() => paginate(trades.slice().reverse(), page), [trades, page]);

  return (
    <>
      <div className="table-wrapper">
        <table className="history-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Scan</th>
              <th>Actif</th>
              <th>Dir.</th>
              <th>Entrée</th>
              <th>Objectif</th>
              <th>Stop</th>
              <th>R/R</th>
              <th>Conf.</th>
              <th>Résultat</th>
              <th>P&L</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((t) => {
              const r = RESULT_LABELS[t.result] || RESULT_LABELS.PENDING;
              return (
                <tr key={`${t.timestamp}-${t.ticker}`}>
                  <td>{formatDateTime(t.timestamp)}</td>
                  <td>{scanLabel(t.scan_type)}</td>
                  <td>
                    <div>
                      {t.asset_name}{" "}
                      <span style={{ color: "var(--text-muted)" }}>{t.ticker}</span>
                    </div>
                    {t.binary_event_warning && (
                      <div className="trade-warning-inline">Évt. binaire</div>
                    )}
                  </td>
                  <td>
                    <span className={`direction-badge sm ${t.direction === "LONG" ? "long" : "short"}`}>
                      {t.direction}
                    </span>
                  </td>
                  <td>{t.entry_price}</td>
                  <td style={{ color: "var(--green)" }}>{t.target_price}</td>
                  <td style={{ color: "var(--red)" }}>{t.stop_price}</td>
                  <td style={{ color: "var(--cyan)" }}>{t.risk_reward}</td>
                  <td>{t.confidence}%</td>
                  <td><span className={`result-badge ${r.cls}`}>{r.label}</span></td>
                  <td className={pnlClass(t.pnl_pct)} style={{ color: pnlColor(t.pnl_pct), fontWeight: 600 }}>
                    {formatPnl(t.pnl_pct)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="pagination">
          <button className="pagination-btn" disabled={page <= 1} onClick={() => setPage(page - 1)}>&larr; Préc.</button>
          <span className="pagination-info">{page} / {pages}</span>
          <button className="pagination-btn" disabled={page >= pages} onClick={() => setPage(page + 1)}>Suiv. &rarr;</button>
        </div>
      )}
    </>
  );
}

/* ── (O6) Trades mobile cards ────────────────────────────── */

function TradesMobileCards({ trades, page, setPage }) {
  const pages = totalPages(trades);
  const visible = useMemo(() => paginate(trades.slice().reverse(), page), [trades, page]);

  return (
    <>
      {visible.map((t) => {
        const r = RESULT_LABELS[t.result] || RESULT_LABELS.PENDING;
        return (
          <div key={`m-${t.timestamp}-${t.ticker}`} className="trade-mobile-card">
            <div className="trade-mobile-card-header">
              <div>
                <div style={{ fontWeight: 600, color: "var(--text-primary)" }}>{t.asset_name}</div>
                <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  {formatDateTime(t.timestamp)} — {t.ticker} — {scanLabel(t.scan_type)}
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span className={`direction-badge sm ${t.direction === "LONG" ? "long" : "short"}`}>{t.direction}</span>
                <span className={`result-badge sm ${r.cls}`}>{r.label}</span>
              </div>
            </div>
            <div className="trade-mobile-card-body">
              <div>
                <div className="trade-mobile-label">Entrée</div>
                <div>{t.entry_price}</div>
              </div>
              <div>
                <div className="trade-mobile-label">R/R</div>
                <div style={{ color: "var(--cyan)" }}>{t.risk_reward}</div>
              </div>
              <div>
                <div className="trade-mobile-label">P&L</div>
                <div style={{ color: pnlColor(t.pnl_pct), fontWeight: 600 }}>{formatPnl(t.pnl_pct)}</div>
              </div>
            </div>
          </div>
        );
      })}
      {pages > 1 && (
        <div className="pagination">
          <button className="pagination-btn" disabled={page <= 1} onClick={() => setPage(page - 1)}>&larr;</button>
          <span className="pagination-info">{page} / {pages}</span>
          <button className="pagination-btn" disabled={page >= pages} onClick={() => setPage(page + 1)}>&rarr;</button>
        </div>
      )}
    </>
  );
}

/* ── Scan history section (M1 — inline styles extracted) ──── */

function ScanHistorySection({ scanHistory }) {
  const [expandedScan, setExpandedScan] = useState(null);

  if (!scanHistory.length) {
    return (
      <div style={{ color: "var(--text-muted)", padding: "12px 0" }}>
        Aucun historique de scan disponible.
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {scanHistory.map((scan) => {
        const key = `${scan.timestamp}-${scan.scan_type}`;
        const isExpanded = expandedScan === key;
        const scoredNews = scan.all_scored_news || [];
        const rejections = scan.rejection_log || [];
        const tradeSelected = scan.has_trade;

        return (
          <div key={key} className={`scan-card ${isExpanded ? "expanded" : ""}`}>
            {/* Header */}
            <div className="scan-card-header" onClick={() => setExpandedScan(isExpanded ? null : key)}>
              <div className="scan-card-header-left">
                <span className="scan-card-date">{formatDateTime(scan.timestamp)}</span>
                <span className={`direction-badge sm ${scan.scan_type?.includes("us") ? "short" : "long"}`}>
                  {scanLabel(scan.scan_type)}
                </span>
                <span className="scan-card-signals">
                  {scoredNews.length} signal{scoredNews.length !== 1 ? "s" : ""} scoré{scoredNews.length !== 1 ? "s" : ""}
                </span>
                {tradeSelected ? (
                  <span className="result-badge sm tp">Trade</span>
                ) : (
                  <span className="result-badge sm expired">Aucun trade</span>
                )}
                {scan.reason_no_trade && !tradeSelected && (
                  <span style={{ color: "var(--text-muted)", fontSize: 11 }}>{scan.reason_no_trade}</span>
                )}
              </div>
              <span className="scan-card-chevron">{isExpanded ? "\u25B2" : "\u25BC"}</span>
            </div>

            {/* Expanded detail */}
            {isExpanded && (
              <div className="scan-card-detail">
                {scan.decision_summary && (
                  <div className="scan-decision-summary">{scan.decision_summary}</div>
                )}

                {scoredNews.length > 0 && (
                  <div className="table-wrapper" style={{ marginTop: 8 }}>
                    <table className="history-table" style={{ fontSize: 12 }}>
                      <thead>
                        <tr>
                          <th>Score</th>
                          <th>Dir.</th>
                          <th>Catégorie</th>
                          <th>Headline</th>
                          <th>Tickers</th>
                          <th>Delay</th>
                          <th>Aware</th>
                          <th>Statut</th>
                        </tr>
                      </thead>
                      <tbody>
                        {scoredNews
                          .slice()
                          .sort((a, b) => (b.total_score || 0) - (a.total_score || 0))
                          .map((news, i) => {
                            const rejection = rejections.find((r) => r.title === news.title);
                            const isSelected =
                              tradeSelected &&
                              scan.recommendation &&
                              news.impacted_tickers?.some((t) => t === scan.recommendation.ticker) &&
                              !rejection;
                            const catColor = CATEGORY_COLORS[news.news_category] || CATEGORY_COLORS.other;

                            return (
                              <tr
                                key={`${news.title}-${i}`}
                                className={`${rejection ? "scan-news-row-rejected" : ""} ${isSelected ? "scan-news-row-selected" : ""}`}
                              >
                                <td style={{ fontWeight: 600, color: "var(--cyan)" }}>
                                  {(news.total_score || 0).toFixed(1)}
                                </td>
                                <td>
                                  {news.direction && news.direction !== "NEUTRAL" ? (
                                    <span className={`direction-badge sm ${news.direction === "LONG" ? "long" : "short"}`}>
                                      {news.direction}
                                    </span>
                                  ) : (
                                    <span style={{ color: "var(--text-muted)", fontSize: 10 }}>—</span>
                                  )}
                                </td>
                                <td>
                                  <span className="cat-badge" style={{ background: catColor + "22", color: catColor }}>
                                    {news.news_category || "other"}
                                  </span>
                                </td>
                                <td className="scan-news-headline" title={news.reasoning || news.title}>
                                  {news.title}
                                </td>
                                <td style={{ color: "var(--text-muted)", fontSize: 11 }}>
                                  {(news.impacted_tickers || []).join(", ") || "—"}
                                </td>
                                <td style={{ textAlign: "center" }}>{news.transmission_delay ?? "—"}</td>
                                <td style={{ textAlign: "center" }}>{news.market_awareness ?? "—"}</td>
                                <td>
                                  {isSelected ? (
                                    <span className="result-badge sm tp">Sélectionné</span>
                                  ) : rejection ? (
                                    <span className="scan-rejection-reason" title={rejection.reason}>
                                      {rejection.reason?.length > 30 ? rejection.reason.slice(0, 30) + "..." : rejection.reason || "Rejeté"}
                                    </span>
                                  ) : (
                                    <span style={{ color: "var(--text-muted)", fontSize: 10 }}>—</span>
                                  )}
                                </td>
                              </tr>
                            );
                          })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ── Main History component ──────────────────────────────── */

export default function History({ isActive }) {
  const [trades, setTrades] = useState([]);
  const [scanHistory, setScanHistory] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [activeTab, setActiveTab] = useState("trades");
  const [lastUpdate, setLastUpdate] = useState(null);
  // (C5) Filters
  const [filterTicker, setFilterTicker] = useState("");
  const [filterResult, setFilterResult] = useState("");
  const [filterDirection, setFilterDirection] = useState("");
  // (M2) Pagination
  const [tradePage, setTradePage] = useState(1);
  // (O2) Export feedback
  const [exporting, setExporting] = useState(false);
  const [closing, setClosing] = useState(false);

  const fetchData = useCallback(() => {
    if (document.hidden) return;
    Promise.all([
      fetch("/api/trades").then((r) => (r.ok ? r.json() : [])).catch(() => []),
      fetch("/api/scan-history?limit=50").then((r) => (r.ok ? r.json() : [])).catch(() => []),
    ]).then(([t, sh]) => {
      setTrades(t);
      setScanHistory(sh);
      setLastUpdate(new Date());
    }).finally(() => setIsLoading(false));
  }, []);

  // Fetch on mount + poll every 60s
  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 60_000);
    return () => clearInterval(id);
  }, [fetchData]);

  // Refetch when tab becomes active
  useEffect(() => {
    if (isActive) fetchData();
  }, [isActive, fetchData]);

  // Refetch when browser tab regains focus
  useEffect(() => {
    const onVisible = () => { if (!document.hidden) fetchData(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [fetchData]);

  // (C5) Filter trades
  const filteredTrades = useMemo(() => {
    let result = trades;
    if (filterTicker) {
      const q = filterTicker.toLowerCase();
      result = result.filter(
        (t) => (t.ticker || "").toLowerCase().includes(q) || (t.asset_name || "").toLowerCase().includes(q)
      );
    }
    if (filterResult) result = result.filter((t) => t.result === filterResult);
    if (filterDirection) result = result.filter((t) => t.direction === filterDirection);
    return result;
  }, [trades, filterTicker, filterResult, filterDirection]);

  // Reset page on filter change
  useEffect(() => setTradePage(1), [filterTicker, filterResult, filterDirection]);

  const hasFilters = filterTicker || filterResult || filterDirection;
  const clearFilters = () => { setFilterTicker(""); setFilterResult(""); setFilterDirection(""); };

  // (O2) Export with feedback
  const handleExport = async () => {
    setExporting(true);
    try {
      const res = await fetch("/api/export/trades");
      if (res.ok) {
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "trades.csv";
        a.click();
        URL.revokeObjectURL(url);
      }
    } catch (err) {
      console.error("Export failed:", err);
    }
    setExporting(false);
  };

  const pendingTrades = useMemo(() => trades.filter((t) => t.result === "PENDING"), [trades]);

  // Force-close PENDING trades by triggering the journal
  const forceClosePending = async () => {
    setClosing(true);
    try {
      await fetch("/api/journal/trigger", { method: "POST" });
      // Wait a moment for backend processing, then refresh
      setTimeout(() => { fetchData(); setClosing(false); }, 3000);
    } catch {
      setClosing(false);
    }
  };

  if (isLoading) {
    return (
      <div>
        <div className="skeleton skeleton-row" />
        <div className="skeleton skeleton-row" />
        <div className="skeleton skeleton-row" />
        <div className="skeleton skeleton-row" />
      </div>
    );
  }

  if (!trades.length && !scanHistory.length) {
    return (
      <div className="no-trade">
        <div className="no-trade-icon">—</div>
        <div className="no-trade-title">Aucun trade enregistré</div>
        <div className="no-trade-reason">Les trades apparaîtront ici après le premier scan.</div>
        <div className="no-trade-meta">
          <span className="no-trade-tag">Scans : 07:50, 11:15, 14:50, 17:00 CET</span>
          <span className="no-trade-tag">Lun-Ven uniquement</span>
        </div>
      </div>
    );
  }

  return (
    <div>
      {/* Pending trades banner */}
      {pendingTrades.length > 0 && (
        <div className="pending-banner">
          <span className="pending-dot" />
          <strong>{pendingTrades.length} trade{pendingTrades.length > 1 ? "s" : ""} en cours</strong>
          <span style={{ color: "var(--text-muted)", marginLeft: 8 }}>
            {pendingTrades.map((t) => `${t.ticker} ${t.direction}`).join(", ")}
          </span>
          <button
            className="trigger-btn"
            style={{ marginLeft: "auto", padding: "4px 12px", fontSize: 12 }}
            onClick={forceClosePending}
            disabled={closing}
          >
            {closing ? "Fermeture..." : "Clore les trades"}
          </button>
        </div>
      )}

      {/* Sub-tab switcher */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
        <div className="sub-tabs">
          <button className={`sub-tab ${activeTab === "trades" ? "active" : ""}`} onClick={() => setActiveTab("trades")}>
            Trades ({trades.length})
          </button>
          <button className={`sub-tab ${activeTab === "scans" ? "active" : ""}`} onClick={() => setActiveTab("scans")}>
            Signaux scorés ({scanHistory.length})
          </button>
        </div>
        <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
          {activeTab === "trades" && (
            <button className="trigger-btn export" onClick={handleExport} disabled={exporting}>
              {exporting ? <><span className="spinner spinner-inline" />Export...</> : "Export CSV"}
            </button>
          )}
          <button className="refresh-btn" onClick={fetchData} title="Rafraîchir">{"\u21BB"}</button>
        </span>
      </div>

      {activeTab === "trades" && (
        <>
          {/* (C5) Filters */}
          <div className="filters-bar">
            <input
              className="filter-input"
              placeholder="Ticker / actif..."
              value={filterTicker}
              onChange={(e) => setFilterTicker(e.target.value)}
            />
            <select className="filter-select" value={filterResult} onChange={(e) => setFilterResult(e.target.value)}>
              <option value="">Tous résultats</option>
              <option value="TP_HIT">TP</option>
              <option value="SL_HIT">SL</option>
              <option value="EXPIRED">Expiré</option>
              <option value="PENDING">Pending</option>
            </select>
            <select className="filter-select" value={filterDirection} onChange={(e) => setFilterDirection(e.target.value)}>
              <option value="">Toutes directions</option>
              <option value="LONG">LONG</option>
              <option value="SHORT">SHORT</option>
            </select>
            {hasFilters && <button className="filter-clear" onClick={clearFilters}>Effacer</button>}
            <span className="last-update">{filteredTrades.length} trade{filteredTrades.length !== 1 ? "s" : ""}</span>
          </div>

          {/* Desktop table */}
          <div className="desktop-only">
            {filteredTrades.length > 0 ? (
              <TradesTable trades={filteredTrades} page={tradePage} setPage={setTradePage} />
            ) : (
              <div style={{ color: "var(--text-muted)", padding: "12px 0" }}>Aucun trade trouvé.</div>
            )}
          </div>

          {/* (O6) Mobile cards */}
          <div className="mobile-only">
            {filteredTrades.length > 0 ? (
              <TradesMobileCards trades={filteredTrades} page={tradePage} setPage={setTradePage} />
            ) : (
              <div style={{ color: "var(--text-muted)", padding: "12px 0" }}>Aucun trade trouvé.</div>
            )}
          </div>
        </>
      )}

      {activeTab === "scans" && <ScanHistorySection scanHistory={scanHistory} />}

      {/* (O1) Last update */}
      {lastUpdate && <div className="last-update">MAJ {timeAgo(lastUpdate)}</div>}
    </div>
  );
}
