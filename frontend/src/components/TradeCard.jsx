import React, { useState } from "react";
import { formatTime, confidenceColor, confidenceLabel } from "../utils/format";

export default function TradeCard({ scan, label }) {
  const [open, setOpen] = useState(false);

  if (!scan || !scan.has_trade) {
    const reason = scan?.reason_no_trade || "En attente du prochain scan...";
    const analyzed = scan?.news_analyzed || 0;
    return (
      <div className="no-trade">
        <div className="no-trade-icon">—</div>
        <div className="no-trade-title">{label}</div>
        <div className="no-trade-reason">{reason}</div>
        {analyzed > 0 && (
          <div className="no-trade-meta">
            <span className="no-trade-tag">{analyzed} news analysées</span>
          </div>
        )}
      </div>
    );
  }

  const t = scan.recommendation;
  const isLong = t.direction === "LONG";
  const confLabel = confidenceLabel(t.confidence);

  return (
    <div className="trade-card">
      <div className="trade-card-header">
        <span className="scan-label">{label}</span>
        <span className="scan-time">{formatTime(t.timestamp)}</span>
      </div>
      <div className="trade-card-body">
        {/* Summary — always visible */}
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
          <div className="trade-headline">{t.news_headline}</div>
        )}
        <div className="catalyst">{t.catalyst}</div>

        {t.binary_event_warning && (
          <div className="trade-warning">{t.binary_event_warning}</div>
        )}

        {/* Price grid — always visible */}
        <div className="trade-grid">
          <div className="trade-metric">
            <div className="trade-metric-label">Entrée</div>
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

        {/* (N5) Confidence bar with contextual label */}
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
          <span className="confidence-value" style={{ color: confidenceColor(t.confidence) }}>
            {t.confidence}%
          </span>
          <span className="confidence-context" style={{ color: confidenceColor(t.confidence) }}>
            {confLabel}
          </span>
        </div>

        {/* (M6) Accordion toggle for details */}
        <button className="trade-toggle-btn" onClick={() => setOpen(!open)}>
          {open ? "Masquer détails \u25B2" : "Voir détails \u25BC"}
        </button>

        {open && (
          <div className="trade-details-section">
            {/* Secondary metrics */}
            <div className="trade-grid">
              <div className="trade-metric">
                <div className="trade-metric-label">R/R</div>
                <div className="trade-metric-value cyan">{t.risk_reward}</div>
              </div>
              <div className="trade-metric">
                <div className="trade-metric-label">Fenêtre</div>
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
                    Délai pricing: {t.transmission_delay}/100
                  </span>
                )}
                {t.market_awareness != null && (
                  <span className="trade-info-badge neutral">
                    Visibilité: {t.market_awareness}/100
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
                    <span className={`direction-badge sm ${cr.direction === "LONG" ? "long" : "short"}`}>
                      {cr.direction}
                    </span>
                    <span className="chain-ticker">{cr.ticker}</span>
                    <span className="chain-reason">{cr.reason}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Position size + convergence info */}
            {(t.position_size_pct != null || t.convergence_count > 0) && (
              <div className="trade-info-row">
                {t.position_size_pct != null && (
                  <span className="trade-info-badge cyan-bg">
                    Taille: {t.position_size_pct}%
                  </span>
                )}
                {t.convergence_count > 0 && (
                  <span className="trade-info-badge positive">
                    {t.convergence_count} sources convergentes
                    {t.convergence_boost && ` (${t.convergence_boost.toFixed(2)}x)`}
                  </span>
                )}
                {t.publication_move_pct != null && (
                  <span className="trade-info-badge neutral">
                    Move depuis pub: {t.publication_move_pct > 0 ? "+" : ""}{t.publication_move_pct.toFixed(2)}%
                  </span>
                )}
              </div>
            )}

            {/* Volume + Pre-move info row */}
            {(t.volume_confirmed != null || t.pre_move_pct != null) && (
              <div className="trade-info-row">
                {t.volume_confirmed != null && (
                  <span className={`trade-info-badge ${t.volume_confirmed ? "positive" : "neutral"}`}>
                    Vol: {t.volume_confirmed ? "confirmé" : "faible"}
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
          </div>
        )}
      </div>
    </div>
  );
}
