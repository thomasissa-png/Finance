import React from "react";

function formatTime(isoStr) {
  if (!isoStr) return "--";
  const d = new Date(isoStr);
  return d.toLocaleString("fr-FR", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function confidenceColor(val) {
  if (val >= 70) return "var(--green)";
  if (val >= 50) return "var(--yellow)";
  return "var(--red)";
}

export default function TradeCard({ scan, label }) {
  if (!scan || !scan.has_trade) {
    const reason = scan?.reason_no_trade || "En attente du prochain scan...";
    const analyzed = scan?.news_analyzed || 0;
    return (
      <div className="no-trade">
        <div className="no-trade-icon">—</div>
        <div className="no-trade-title">Pas de trade</div>
        <div className="no-trade-reason">{reason}</div>
        {analyzed > 0 && (
          <div className="no-trade-reason" style={{ marginTop: 8 }}>
            {analyzed} news analysees
          </div>
        )}
      </div>
    );
  }

  const t = scan.recommendation;
  const isLong = t.direction === "LONG";

  return (
    <div className="trade-card">
      <div className="trade-card-header">
        <span className="scan-label">{label}</span>
        <span className="scan-time">{formatTime(t.timestamp)}</span>
      </div>
      <div className="trade-card-body">
        <div className="trade-main-row">
          <div>
            <div className="trade-asset">{t.asset_name}</div>
            <div className="trade-ticker">{t.ticker}</div>
          </div>
          <span className={`direction-badge ${isLong ? "long" : "short"}`}>
            {isLong ? "LONG" : "SHORT"} {isLong ? "\u2191" : "\u2193"}
          </span>
        </div>

        {t.news_headline && (
          <div className="catalyst" style={{ color: "var(--text-primary)", fontWeight: 500 }}>
            {t.news_headline}
          </div>
        )}
        <div className="catalyst">{t.catalyst}</div>

        <div className="trade-grid">
          <div className="trade-metric">
            <div className="trade-metric-label">Entree</div>
            <div className="trade-metric-value cyan">{t.entry_price}</div>
          </div>
          <div className="trade-metric">
            <div className="trade-metric-label">Objectif</div>
            <div className="trade-metric-value green">
              {t.target_price} (+{t.target_pct}%)
            </div>
          </div>
          <div className="trade-metric">
            <div className="trade-metric-label">Stop</div>
            <div className="trade-metric-value red">
              {t.stop_price} (-{t.stop_pct}%)
            </div>
          </div>
        </div>

        <div className="trade-grid">
          <div className="trade-metric">
            <div className="trade-metric-label">R/R</div>
            <div className="trade-metric-value cyan">{t.risk_reward}</div>
          </div>
          <div className="trade-metric">
            <div className="trade-metric-label">Fenetre</div>
            <div className="trade-metric-value">{t.time_window}</div>
          </div>
          <div className="trade-metric">
            <div className="trade-metric-label">Source</div>
            <div className="trade-metric-value" style={{ fontSize: 12 }}>
              {t.news_sources?.[0] || "—"}
            </div>
          </div>
        </div>

        <div className="confidence-row">
          <span className="confidence-label">Confiance</span>
          <div className="confidence-bar">
            <div
              className="confidence-fill"
              style={{
                width: `${t.confidence}%`,
                background: confidenceColor(t.confidence),
              }}
            />
          </div>
          <span
            className="confidence-value"
            style={{ color: confidenceColor(t.confidence) }}
          >
            {t.confidence}%
          </span>
        </div>
      </div>
    </div>
  );
}
