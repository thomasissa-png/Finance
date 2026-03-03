import React, { useEffect, useState, useMemo, useCallback } from "react";

const RESULT_LABELS = {
  TP_HIT: { label: "TP", cls: "tp" },
  SL_HIT: { label: "SL", cls: "sl" },
  EXPIRED: { label: "EXP", cls: "expired" },
  PENDING: { label: "...", cls: "pending" },
};

const CATEGORY_COLORS = {
  weather: "#4fc3f7",
  commodity: "#ffb74d",
  geopolitical: "#ef5350",
  supply_chain: "#ab47bc",
  sector: "#66bb6a",
  regulatory: "#78909c",
  macro: "#9e9e9e",
  earnings: "#757575",
  central_bank_subtle: "#8d6e63",
  m_a: "#5c6bc0",
  other: "#90a4ae",
};

function formatDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("fr-FR", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

// (D14) P&L color with flat for near-zero
function pnlClass(val) {
  if (val == null) return "";
  if (Math.abs(val) < 0.05) return "pnl-flat";
  return "";
}

function pnlColor(val) {
  if (val == null) return "var(--text-muted)";
  if (Math.abs(val) < 0.05) return undefined; // handled by pnl-flat class
  if (val > 0) return "var(--green)";
  if (val < 0) return "var(--red)";
  return "var(--text-muted)";
}

/* ── Sub-components ────────────────────────────────────── */

function TradesTable({ trades }) {
  return (
    <div style={{ overflowX: "auto" }}>
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
          {trades
            .slice()
            .reverse()
            .map((t) => {
              const r = RESULT_LABELS[t.result] || RESULT_LABELS.PENDING;
              return (
                <tr key={`${t.timestamp}-${t.ticker}`}>
                  <td>{formatDate(t.timestamp)}</td>
                  <td>{t.scan_type === "europe" ? "Europe" : "US"}</td>
                  <td>
                    <div>
                      {t.asset_name}{" "}
                      <span style={{ color: "var(--text-muted)" }}>
                        {t.ticker}
                      </span>
                    </div>
                    {t.binary_event_warning && (
                      <div className="trade-warning-inline" style={{ fontSize: 10 }}>
                        Évt. binaire
                      </div>
                    )}
                  </td>
                  <td>
                    <span
                      className={`direction-badge ${t.direction === "LONG" ? "long" : "short"}`}
                      style={{ fontSize: 11, padding: "2px 6px" }}
                    >
                      {t.direction}
                    </span>
                  </td>
                  <td>{t.entry_price}</td>
                  <td style={{ color: "var(--green)" }}>{t.target_price}</td>
                  <td style={{ color: "var(--red)" }}>{t.stop_price}</td>
                  <td style={{ color: "var(--cyan)" }}>{t.risk_reward}</td>
                  <td>{t.confidence}%</td>
                  <td>
                    <span className={`result-badge ${r.cls}`}>{r.label}</span>
                  </td>
                  <td
                    className={pnlClass(t.pnl_pct)}
                    style={{
                      color: pnlColor(t.pnl_pct),
                      fontWeight: 600,
                    }}
                  >
                    {t.pnl_pct != null ? `${t.pnl_pct > 0 ? "+" : ""}${t.pnl_pct}%` : "—"}
                  </td>
                </tr>
              );
            })}
        </tbody>
      </table>
    </div>
  );
}

function ScanHistorySection({ scanHistory }) {
  const [expandedScan, setExpandedScan] = useState(null);

  if (!scanHistory.length) {
    return (
      <div style={{ color: "var(--text-muted)", padding: "12px 0" }}>
        Aucun historique de scan disponible. Les données apparaîtront après le prochain scan.
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {scanHistory.map((scan, idx) => {
        const key = `${scan.timestamp}-${scan.scan_type}`;
        const isExpanded = expandedScan === key;
        const scoredNews = scan.all_scored_news || [];
        const rejections = scan.rejection_log || [];
        const tradeSelected = scan.has_trade;

        return (
          <div
            key={key}
            style={{
              background: "var(--surface, #1e1e2e)",
              border: "1px solid var(--border, #333)",
              borderRadius: 8,
              overflow: "hidden",
            }}
          >
            {/* Scan header — clickable */}
            <div
              onClick={() => setExpandedScan(isExpanded ? null : key)}
              style={{
                padding: "10px 14px",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: 12,
                justifyContent: "space-between",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <span style={{ color: "var(--text-muted)", fontSize: 12, minWidth: 110 }}>
                  {formatDate(scan.timestamp)}
                </span>
                <span
                  className={`direction-badge ${scan.scan_type === "europe" ? "long" : "short"}`}
                  style={{ fontSize: 11, padding: "2px 8px" }}
                >
                  {scan.scan_type === "europe" ? "Europe" : "US"}
                </span>
                <span style={{ fontSize: 13 }}>
                  {scoredNews.length} signal{scoredNews.length !== 1 ? "s" : ""} scoré{scoredNews.length !== 1 ? "s" : ""}
                </span>
                {tradeSelected ? (
                  <span className="result-badge tp" style={{ fontSize: 10, padding: "1px 6px" }}>
                    Trade
                  </span>
                ) : (
                  <span className="result-badge expired" style={{ fontSize: 10, padding: "1px 6px" }}>
                    Aucun trade
                  </span>
                )}
                {scan.reason_no_trade && !tradeSelected && (
                  <span style={{ color: "var(--text-muted)", fontSize: 11 }}>
                    {scan.reason_no_trade}
                  </span>
                )}
              </div>
              <span style={{ color: "var(--text-muted)", fontSize: 14 }}>
                {isExpanded ? "\u25b2" : "\u25bc"}
              </span>
            </div>

            {/* Expanded detail */}
            {isExpanded && (
              <div style={{ padding: "0 14px 14px", borderTop: "1px solid var(--border, #333)" }}>
                {/* Decision summary */}
                {scan.decision_summary && (
                  <div style={{
                    margin: "10px 0",
                    padding: "8px 12px",
                    background: "var(--bg, #0d0d1a)",
                    borderRadius: 6,
                    fontSize: 12,
                    lineHeight: 1.5,
                    color: "var(--text-secondary, #ccc)",
                  }}>
                    {scan.decision_summary}
                  </div>
                )}

                {/* Scored news table */}
                {scoredNews.length > 0 && (
                  <div style={{ overflowX: "auto", marginTop: 8 }}>
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
                            // Check if this news was rejected
                            const rejection = rejections.find(
                              (r) => r.title === news.title
                            );
                            const isSelected =
                              tradeSelected &&
                              scan.recommendation &&
                              news.impacted_tickers?.some(
                                (t) => t === scan.recommendation.ticker
                              ) &&
                              !rejection;
                            const catColor = CATEGORY_COLORS[news.news_category] || CATEGORY_COLORS.other;

                            return (
                              <tr
                                key={`${news.title}-${i}`}
                                style={{
                                  opacity: rejection ? 0.6 : 1,
                                  background: isSelected ? "rgba(76, 175, 80, 0.08)" : undefined,
                                }}
                              >
                                <td style={{ fontWeight: 600, color: "var(--cyan)" }}>
                                  {(news.total_score || 0).toFixed(1)}
                                </td>
                                <td>
                                  {news.direction && news.direction !== "NEUTRAL" ? (
                                    <span
                                      className={`direction-badge ${news.direction === "LONG" ? "long" : "short"}`}
                                      style={{ fontSize: 10, padding: "1px 5px" }}
                                    >
                                      {news.direction}
                                    </span>
                                  ) : (
                                    <span style={{ color: "var(--text-muted)", fontSize: 10 }}>
                                      —
                                    </span>
                                  )}
                                </td>
                                <td>
                                  <span
                                    style={{
                                      fontSize: 10,
                                      padding: "1px 6px",
                                      borderRadius: 4,
                                      background: catColor + "22",
                                      color: catColor,
                                      whiteSpace: "nowrap",
                                    }}
                                  >
                                    {news.news_category || "other"}
                                  </span>
                                </td>
                                <td
                                  style={{
                                    maxWidth: 320,
                                    overflow: "hidden",
                                    textOverflow: "ellipsis",
                                    whiteSpace: "nowrap",
                                  }}
                                  title={news.reasoning || news.title}
                                >
                                  {news.title}
                                </td>
                                <td style={{ color: "var(--text-muted)", fontSize: 11 }}>
                                  {(news.impacted_tickers || []).join(", ") || "—"}
                                </td>
                                <td style={{ textAlign: "center" }}>
                                  {news.transmission_delay ?? "—"}
                                </td>
                                <td style={{ textAlign: "center" }}>
                                  {news.market_awareness ?? "—"}
                                </td>
                                <td>
                                  {isSelected ? (
                                    <span className="result-badge tp" style={{ fontSize: 10, padding: "1px 6px" }}>
                                      Sélectionné
                                    </span>
                                  ) : rejection ? (
                                    <span
                                      style={{
                                        fontSize: 10,
                                        color: "var(--text-muted)",
                                      }}
                                      title={rejection.reason}
                                    >
                                      {rejection.reason?.length > 30
                                        ? rejection.reason.slice(0, 30) + "..."
                                        : rejection.reason || "Rejeté"}
                                    </span>
                                  ) : (
                                    <span style={{ color: "var(--text-muted)", fontSize: 10 }}>
                                      —
                                    </span>
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

/* ── Main component ────────────────────────────────────── */

export default function History() {
  const [trades, setTrades] = useState([]);
  const [scanHistory, setScanHistory] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [activeTab, setActiveTab] = useState("trades");

  const fetchData = useCallback(() => {
    if (document.hidden) return;
    Promise.all([
      fetch("/api/trades").then((r) => (r.ok ? r.json() : [])).catch(() => []),
      fetch("/api/scan-history?limit=50").then((r) => (r.ok ? r.json() : [])).catch(() => []),
    ])
      .then(([t, sh]) => {
        setTrades(t);
        setScanHistory(sh);
      })
      .finally(() => setIsLoading(false));
  }, []);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 60_000);
    return () => clearInterval(id);
  }, [fetchData]);

  if (isLoading) {
    return (
      <div className="no-trade">
        <div className="no-trade-icon">...</div>
        <div className="no-trade-title">Chargement...</div>
      </div>
    );
  }

  const hasTrades = trades.length > 0;
  const hasScans = scanHistory.length > 0;

  if (!hasTrades && !hasScans) {
    return (
      <div className="no-trade">
        <div className="no-trade-icon">—</div>
        <div className="no-trade-title">Aucun trade enregistré</div>
        <div className="no-trade-reason">
          Les trades apparaîtront ici après le premier scan.
        </div>
        <div className="no-trade-meta">
          <span className="no-trade-tag">Scans : 07:50, 11:15, 14:50, 17:00 CET</span>
          <span className="no-trade-tag">Lun-Ven uniquement</span>
        </div>
      </div>
    );
  }

  return (
    <div>
      {/* Tab switcher */}
      <div style={{ display: "flex", gap: 0, marginBottom: 16 }}>
        <button
          onClick={() => setActiveTab("trades")}
          className={`trigger-btn ${activeTab === "trades" ? "" : "export"}`}
          style={{
            borderRadius: "6px 0 0 6px",
            borderRight: "none",
            minWidth: 120,
          }}
        >
          Trades ({trades.length})
        </button>
        <button
          onClick={() => setActiveTab("scans")}
          className={`trigger-btn ${activeTab === "scans" ? "" : "export"}`}
          style={{
            borderRadius: "0 6px 6px 0",
            minWidth: 120,
          }}
        >
          Signaux scorés ({scanHistory.length})
        </button>
        {activeTab === "trades" && (
          <a
            href="/api/export/trades"
            className="trigger-btn export"
            style={{ textDecoration: "none", marginLeft: "auto" }}
          >
            Export CSV
          </a>
        )}
      </div>

      {/* Tab content */}
      {activeTab === "trades" ? (
        hasTrades ? (
          <TradesTable trades={trades} />
        ) : (
          <div style={{ color: "var(--text-muted)", padding: "12px 0" }}>
            Aucun trade enregistré.
          </div>
        )
      ) : (
        <ScanHistorySection scanHistory={scanHistory} />
      )}
    </div>
  );
}
