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
            <div className="trade-ticker">
              {t.ticker}
              {t.news_category && t.news_category !== "other" && (
                <span className="trade-news-cat-badge">{t.news_category}</span>
              )}
            </div>
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

        {/* (#24) Binary event warning */}
        {t.binary_event_warning && (
          <div className="trade-warning">
            {t.binary_event_warning}
          </div>
        )}

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

        {/* Edge detection info */}
        {t.edge_score != null && (
          <div className="trade-edge-row">
            <span className={`trade-info-badge ${t.edge_score >= 0.4 ? "edge-high" : t.edge_score >= 0.15 ? "edge-mid" : "edge-low"}`}>
              Edge: {(t.edge_score * 100).toFixed(0)}%
            </span>
            {t.transmission_delay != null && (
              <span className="trade-info-badge neutral">
                Delai pricing: {t.transmission_delay}/100
              </span>
            )}
            {t.market_awareness != null && (
              <span className="trade-info-badge neutral">
                Visibilite: {t.market_awareness}/100
              </span>
            )}
          </div>
        )}

        {/* Chain reactions */}
        {t.chain_reactions && t.chain_reactions.length > 0 && (
          <div className="chain-reactions">
            <div className="chain-reactions-title">Effets de second ordre</div>
            {t.chain_reactions.map((cr, i) => (
              <div key={i} className="chain-reaction-item">
                <span className={`direction-badge ${cr.direction === "LONG" ? "long" : "short"}`} style={{ fontSize: 10, padding: "1px 5px" }}>
                  {cr.direction}
                </span>
                <span className="chain-ticker">{cr.ticker}</span>
                <span className="chain-reason">{cr.reason}</span>
              </div>
            ))}
          </div>
        )}

        {/* Volume + Pre-move info row */}
        {(t.volume_confirmed != null || t.pre_move_pct != null) && (
          <div className="trade-info-row">
            {t.volume_confirmed != null && (
              <span className={`trade-info-badge ${t.volume_confirmed ? "positive" : "neutral"}`}>
                Vol: {t.volume_confirmed ? "confirme" : "faible"}
              </span>
            )}
            {t.pre_move_pct != null && (
              <span className="trade-info-badge neutral">
                Pre-move: {t.pre_move_pct > 0 ? "+" : ""}{t.pre_move_pct.toFixed(2)}%
              </span>
            )}
            {t.gap_buffer_applied && (
              <span className="trade-info-badge neutral">Gap buffer</span>
            )}
          </div>
        )}

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
