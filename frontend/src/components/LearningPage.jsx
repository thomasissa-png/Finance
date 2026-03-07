import React, { useState, useEffect, useCallback } from "react";
import { pnlColor } from "../utils/format";

const LEVEL_ICONS = { INFO: "\u2139\ufe0f", WARN: "\u26a0\ufe0f", ERROR: "\u274c", DECISION: "\u26a1" };
const LEVEL_COLORS = { INFO: "var(--text-secondary)", WARN: "var(--yellow)", ERROR: "var(--red)", DECISION: "var(--cyan)" };

const DIM_DESCRIPTIONS = {
  "Per-ticker": "Ajustement par ticker \u00d7 cat\u00e9gorie d'actif. Booste ou p\u00e9nalise chaque instrument selon ses performances historiques.",
  "Session": "Europe vs US \u2014 ajuste selon la performance par session de trading.",
  "News Category": "Par cat\u00e9gorie de news + ticker (cross-dimension). Ex: weather+ZW=F.",
  "R\u00e9gime VIX": "Low vol vs High vol \u2014 ajuste selon le r\u00e9gime de volatilit\u00e9 du march\u00e9.",
  "Direction": "LONG vs SHORT \u2014 ajuste selon la pr\u00e9cision directionnelle historique.",
  "Delay Bias": "Ajustement global bas\u00e9 sur la pr\u00e9cision des pr\u00e9dictions de d\u00e9lai de transmission.",
};

function MultiplierBadge({ value }) {
  if (value == null) return <span className="mult-badge neutral">—</span>;
  const isBoost = value >= 1;
  return (
    <span className={`mult-badge ${isBoost ? "boost" : "penalty"}`}
      style={{ color: isBoost ? "var(--green)" : "var(--red)" }}>
      {value.toFixed(3)}\u00d7
    </span>
  );
}

export default function LearningPage({ isActive }) {
  const [learning, setLearning] = useState(null);
  const [perf, setPerf] = useState(null);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const [lRes, pRes, logRes] = await Promise.all([
        fetch("/api/learning").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch("/api/performance").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch(`/api/agents/learning/logs?limit=50${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`)
          .then((r) => r.ok ? r.json() : []).catch(() => []),
      ]);
      setLearning(lRes);
      setPerf(pRes);
      setLogs(Array.isArray(logRes) ? logRes : []);
      setFetchError(null);
    } catch (err) {
      setFetchError(err.message || "Erreur de chargement");
    } finally {
      setLoading(false);
    }
  }, [logFilter]);

  useEffect(() => {
    if (isActive) fetchData();
    const id = setInterval(fetchData, 60_000);
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
        <span className="agent-page-desc">6 dimensions d'apprentissage adaptatif, d\u00e9tection d'anomalies, optimisation continue</span>
      </div>

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement des données...</div>}
      {fetchError && <div className="agent-error-banner">Erreur : {fetchError}</div>}

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
          <div className="kpi-label">P\u00e9nalit\u00e9s</div>
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
          <h3>3. Cat\u00e9gorie de news ({Object.keys(newscatAdj).length})</h3>
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
          <h3>4. R\u00e9gime VIX</h3>
          <p className="dim-desc">{DIM_DESCRIPTIONS["R\u00e9gime VIX"]}</p>
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
      <div className="section-card">
        <div className="section-header">
          <h3>Logs Agent Learning</h3>
          <div className="log-filter-row">
            {["ALL", "DECISION", "INFO", "WARN", "ERROR"].map((level) => (
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
            ))
          )}
        </div>
      </div>
    </div>
  );
}
