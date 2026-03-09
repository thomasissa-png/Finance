import React, { useState, useEffect, useCallback } from "react";
import { DIR_COLORS, DIR_ARROWS, POLL_FAST } from "../utils/constants";
import { apiFetch } from "../utils/api";
import { pnlColor } from "../utils/format";
import { ErrorBanner, EmptyState, LastUpdated, LogSection } from "./shared";

export default function Trader2Page({ isActive }) {
  const [positions, setPositions] = useState({});
  const [logs, setLogs] = useState([]);
  const [learningAdj, setLearningAdj] = useState(null);
  const [expandedTicker, setExpandedTicker] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [logFilter, setLogFilter] = useState("ALL");

  const fetchData = useCallback(async () => {
    try {
      const [posRes, logRes, adjRes] = await Promise.all([
        apiFetch("/api/trader2/positions", {}, {}),
        apiFetch("/api/agents/trader_2/logs?limit=50", {}, []),
        apiFetch("/api/learning2/adjustments", {}, null),
      ]);
      setPositions(posRes || {});
      setLogs(Array.isArray(logRes) ? logRes : []);
      setLearningAdj(adjRes);
      setError(null);
      setLastUpdate(new Date());
    } catch (err) {
      setError(err.message || "Impossible de charger les données Trader 2");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isActive) fetchData();
    const id = setInterval(fetchData, POLL_FAST);
    return () => clearInterval(id);
  }, [isActive, fetchData]);

  const tickers = Object.values(positions);
  const totalRealized = tickers.reduce((s, p) => s + (p.realized_pnl_pct || 0), 0);
  const totalUnrealized = tickers.reduce((s, p) => s + (p.unrealized_pnl_pct || 0), 0);
  const totalSwitches = tickers.reduce((s, p) => s + (p.total_switches || 0), 0);

  const filteredLogs = logFilter === "ALL" ? logs : logs.filter((l) => l.level === logFilter);

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>Agent Trader 2 Trend Commodities</h2>
        <span className="agent-page-desc">
          Positions de tendance sur cuivre, cacao, café, blé. Change de direction quand les news l'exigent
        </span>
        <LastUpdated date={lastUpdate} />
      </div>

      <ErrorBanner error={error} onRetry={fetchData} />

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement...</div>}

      {/* KPIs */}
      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-value">{tickers.length}</div>
          <div className="kpi-label">Actifs suivis</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value" style={{ color: pnlColor(totalRealized) }}>
            {totalRealized >= 0 ? "+" : ""}{totalRealized.toFixed(2)}%
          </div>
          <div className="kpi-label">P&L Réalisé</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value" style={{ color: pnlColor(totalUnrealized) }}>
            {totalUnrealized >= 0 ? "+" : ""}{totalUnrealized.toFixed(2)}%
          </div>
          <div className="kpi-label">P&L Latent</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{totalSwitches}</div>
          <div className="kpi-label">Changements</div>
        </div>
      </div>

      {/* Learning 2 context */}
      {learningAdj && learningAdj.stats && learningAdj.stats.sufficient_data && (
        <div className="section-card" style={{ padding: "12px 16px" }}>
          <h3 style={{ marginBottom: 8 }}>Learning 2 &mdash; Ajustements actifs</h3>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 13 }}>
            {Object.entries(learningAdj.ticker_adj || {}).map(([t, v]) => (
              <span key={t} style={{ color: v >= 1 ? "var(--green)" : "var(--red)" }}>
                {t}: {v.toFixed(2)}x
              </span>
            ))}
            {learningAdj.signal_calibration?.threshold_adj !== 1.0 && (
              <span style={{ color: "var(--cyan)" }}>
                Seuil: {(20 * (learningAdj.signal_calibration?.threshold_adj || 1)).toFixed(1)}
              </span>
            )}
          </div>
          {(learningAdj.anomalies || []).length > 0 && (
            <div style={{ marginTop: 6, fontSize: 12, color: "var(--yellow)" }}>
              ! {learningAdj.anomalies.join(" | ")}
            </div>
          )}
        </div>
      )}

      {/* Position Cards */}
      <div className="section-card">
        <h3>Positions actuelles</h3>
        <div className="trend-positions-grid">
          {tickers.map((pos) => (
            <div
              key={pos.ticker}
              className={`trend-position-card ${expandedTicker === pos.ticker ? "expanded" : ""}`}
              onClick={() => setExpandedTicker(expandedTicker === pos.ticker ? null : pos.ticker)}
            >
              <div className="trend-position-header">
                <div className="trend-position-ticker">
                  <span className="trend-dir-arrow" style={{ color: DIR_COLORS[pos.direction] }}>
                    {DIR_ARROWS[pos.direction] || "\u2022"}
                  </span>
                  <span className="trend-ticker-name">{pos.ticker}</span>
                  <span className="trend-asset-name">{pos.name}</span>
                </div>
                <div className="trend-position-dir" style={{ color: DIR_COLORS[pos.direction] }}>
                  {pos.direction}
                </div>
              </div>

              <div className="trend-position-prices">
                <div>
                  <span className="trend-label">Entrée</span>
                  <span className="trend-value">{pos.entry_price ? pos.entry_price.toFixed(2) : "\u2014"}</span>
                </div>
                <div>
                  <span className="trend-label">Actuel</span>
                  <span className="trend-value">{pos.current_price ? pos.current_price.toFixed(2) : "\u2014"}</span>
                </div>
                <div>
                  <span className="trend-label">P&L</span>
                  <span className="trend-value" style={{ color: pnlColor(pos.unrealized_pnl_pct) }}>
                    {(pos.unrealized_pnl_pct || 0) >= 0 ? "+" : ""}{(pos.unrealized_pnl_pct || 0).toFixed(2)}%
                  </span>
                </div>
              </div>

              <div className="trend-position-confidence">
                <div className="trend-confidence-bar" style={{ width: `${pos.confidence || 0}%` }} />
                <span className="trend-confidence-label">Confiance {pos.confidence || 0}%</span>
              </div>

              <div className="trend-position-reasoning">{pos.reasoning}</div>

              {/* Expanded: history */}
              {expandedTicker === pos.ticker && (
                <div className="trend-position-detail">
                  <div className="trend-detail-section">
                    <h4>Catalyseurs clés</h4>
                    {(pos.key_catalysts || []).slice(0, 5).map((c, i) => (
                      <div key={i} className="trend-catalyst">
                        <span className="trend-catalyst-cat">{c.category}</span>
                        <span className="trend-catalyst-title">{c.title}</span>
                        <span className="trend-catalyst-score">score {c.score}</span>
                      </div>
                    ))}
                    {(!pos.key_catalysts || pos.key_catalysts.length === 0) && (
                      <div className="trend-no-data">Aucun catalyseur enregistré</div>
                    )}
                  </div>

                  <div className="trend-detail-section">
                    <h4>Historique ({pos.total_switches || 0} changements), P&L réalisé : {(pos.realized_pnl_pct || 0).toFixed(2)}%</h4>
                    <div className="trend-history-list">
                      {(pos.history || []).slice(0, 10).map((h, i) => (
                        <div key={i} className="trend-history-entry">
                          <span className="trend-history-time">
                            {new Date(h.time).toLocaleDateString("fr-FR", { day: "2-digit", month: "short" })}
                          </span>
                          <span style={{ color: DIR_COLORS[h.from_direction] }}>{h.from_direction}</span>
                          <span>&rarr;</span>
                          <span style={{ color: DIR_COLORS[h.to_direction] }}>{h.to_direction}</span>
                          <span className="trend-history-pnl" style={{ color: pnlColor(h.pnl_pct) }}>
                            {(h.pnl_pct || 0) >= 0 ? "+" : ""}{(h.pnl_pct || 0).toFixed(2)}%
                          </span>
                          <span className="trend-history-reason">{h.reason}</span>
                        </div>
                      ))}
                      {(!pos.history || pos.history.length === 0) && (
                        <div className="trend-no-data">Aucun changement enregistré</div>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          ))}
          {tickers.length === 0 && !loading && (
            <EmptyState message="Aucune position" detail="L'agent sera initialisé au prochain scan" />
          )}
        </div>
      </div>

      {/* Decision Logs */}
      <LogSection logs={filteredLogs} logFilter={logFilter} setLogFilter={setLogFilter} />
    </div>
  );
}
