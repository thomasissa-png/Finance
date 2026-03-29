import React, { useState, useEffect, useCallback } from "react";
import { pnlColor } from "../utils/format";
import { POLL_NORMAL } from "../utils/constants";
import { apiFetch } from "../utils/api";
import { ErrorBanner, LastUpdated, LogSection } from "./shared";

const DIM_DESCRIPTIONS = {
  "Per-ticker": "Ajustement par ticker × catégorie d'actif. Booste ou pénalise chaque instrument selon ses performances historiques.",
  "Session": "Europe vs US \u2014 ajuste selon la performance par session de trading.",
  "News Category": "Par catégorie de news + ticker (cross-dimension). Ex: weather+ZW=F.",
  "Régime VIX": "Low vol vs High vol \u2014 ajuste selon le régime de volatilité du marché.",
  "Direction": "LONG vs SHORT \u2014 ajuste selon la précision directionnelle historique.",
  "Delay Bias": "Ajustement global basé sur la précision des prédictions de délai de transmission.",
};

function MultiplierBadge({ value }) {
  if (value == null) return <span className="mult-badge neutral">&mdash;</span>;
  const isBoost = value >= 1;
  return (
    <span className={`mult-badge ${isBoost ? "boost" : "penalty"}`}
      style={{ color: isBoost ? "var(--green)" : "var(--red)" }}>
      {value.toFixed(3)}&times;
    </span>
  );
}

export default function LearningPage({ isActive }) {
  const [learning, setLearning] = useState(null);
  const [perf, setPerf] = useState(null);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const [lRes, pRes, logRes] = await Promise.all([
        apiFetch("/api/learning", {}, null),
        apiFetch("/api/performance", {}, null),
        apiFetch(`/api/agents/learning/logs?limit=50${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`, {}, []),
      ]);
      setLearning(lRes);
      setPerf(pRes);
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

  const adjustments = learning?.adjustments || {};
  const decomp = learning?.decomposition || {};
  const sessionAdj = learning?.session_adj || {};
  const newscatAdj = learning?.newscat_adj || {};
  const regimeAdj = learning?.regime_adj || {};
  const directionAdj = learning?.direction_adj || {};
  const delayBias = learning?.delay_bias_adj;

  const totalAdj = Object.keys(adjustments).length;
  const boosts = Object.values(adjustments).filter((v) => v > 1).length;
  const penalties = Object.values(adjustments).filter((v) => v < 1).length;

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>{"\ud83e\udde0"} Agent Learning</h2>
        <span className="agent-page-desc">6 dimensions d'apprentissage adaptatif, détection d'anomalies, optimisation continue</span>
        <LastUpdated date={lastUpdate} />
      </div>

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement des données...</div>}
      <ErrorBanner error={error} onRetry={fetchData} />

      {/* KPIs */}
      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-value">{totalAdj}</div>
          <div className="kpi-label">Ajustements actifs</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value" style={{ color: "var(--green)" }}>{boosts}</div>
          <div className="kpi-label">Boosts</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value" style={{ color: "var(--red)" }}>{penalties}</div>
          <div className="kpi-label">Pénalités</div>
        </div>
        {perf && (
          <div className="kpi-card">
            <div className="kpi-value" style={{ color: pnlColor(perf.total_pnl_pct) }}>
              {perf.total_pnl_pct != null ? `${perf.total_pnl_pct > 0 ? "+" : ""}${perf.total_pnl_pct.toFixed(2)}%` : "--"}
            </div>
            <div className="kpi-label">P&L total</div>
          </div>
        )}
      </div>

      {/* Dimension 1: Per-ticker */}
      {totalAdj > 0 && (
        <div className="section-card">
          <h3>1. Per-ticker ({totalAdj} tickers)</h3>
          <p className="dim-desc">{DIM_DESCRIPTIONS["Per-ticker"]}</p>
          <div className="learning-grid">
            {Object.entries(adjustments)
              .sort((a, b) => Math.abs(b[1] - 1) - Math.abs(a[1] - 1))
              .map(([ticker, mult]) => {
                const d = decomp[ticker] || {};
                return (
                  <div key={ticker} className={`learning-item ${mult >= 1 ? "boost" : "penalty"}`}>
                    <div className="learning-item-ticker">{ticker}</div>
                    <MultiplierBadge value={mult} />
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

      {/* Dimension 2: Session */}
      {Object.keys(sessionAdj).length > 0 && (
        <div className="section-card">
          <h3>2. Session</h3>
          <p className="dim-desc">{DIM_DESCRIPTIONS["Session"]}</p>
          <div className="learning-dim-cards">
            {Object.entries(sessionAdj).map(([k, v]) => (
              <div key={k} className="learning-dim-card">
                <div className="learning-dim-card-label">{k}</div>
                <MultiplierBadge value={v} />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Dimension 3: Newscat */}
      {Object.keys(newscatAdj).length > 0 && (
        <div className="section-card">
          <h3>3. Catégorie de news ({Object.keys(newscatAdj).length})</h3>
          <p className="dim-desc">{DIM_DESCRIPTIONS["News Category"]}</p>
          <div className="learning-grid">
            {Object.entries(newscatAdj)
              .sort((a, b) => Math.abs(b[1] - 1) - Math.abs(a[1] - 1))
              .map(([cat, mult]) => (
                <div key={cat} className={`learning-item ${mult >= 1 ? "boost" : "penalty"}`}>
                  <div className="learning-item-ticker">{cat}</div>
                  <MultiplierBadge value={mult} />
                </div>
              ))}
          </div>
        </div>
      )}

      {/* Dimension 4: Regime VIX */}
      {Object.keys(regimeAdj).length > 0 && (
        <div className="section-card">
          <h3>4. Régime VIX</h3>
          <p className="dim-desc">{DIM_DESCRIPTIONS["Régime VIX"]}</p>
          <div className="learning-dim-cards">
            {Object.entries(regimeAdj).map(([k, v]) => (
              <div key={k} className="learning-dim-card">
                <div className="learning-dim-card-label">{k}</div>
                <MultiplierBadge value={v} />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Dimension 5: Direction */}
      {Object.keys(directionAdj).length > 0 && (
        <div className="section-card">
          <h3>5. Direction</h3>
          <p className="dim-desc">{DIM_DESCRIPTIONS["Direction"]}</p>
          <div className="learning-dim-cards">
            {Object.entries(directionAdj).map(([k, v]) => (
              <div key={k} className="learning-dim-card">
                <div className="learning-dim-card-label">{k}</div>
                <MultiplierBadge value={v} />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Dimension 6: Delay Bias */}
      <div className="section-card">
        <h3>6. Delay Bias</h3>
        <p className="dim-desc">{DIM_DESCRIPTIONS["Delay Bias"]}</p>
        <div className="learning-dim-cards">
          <div className="learning-dim-card">
            <div className="learning-dim-card-label">Global</div>
            <MultiplierBadge value={delayBias} />
          </div>
        </div>
      </div>

      {/* Agent logs */}
      <LogSection logs={logs} logFilter={logFilter} setLogFilter={setLogFilter} title="Logs Agent Learning" />
    </div>
  );
}
