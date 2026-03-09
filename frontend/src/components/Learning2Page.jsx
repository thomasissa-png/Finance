import React, { useState, useEffect, useCallback } from "react";
import { LEVEL_ICONS, LEVEL_COLORS, POLL_NORMAL } from "../utils/constants";
import { apiFetch } from "../utils/api";
import { ErrorBanner, EmptyState, LastUpdated, AdjBar, LogSection } from "./shared";

export default function Learning2Page({ isActive }) {
  const [data, setData] = useState(null);
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [error, setError] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [logFilter, setLogFilter] = useState("ALL");

  const fetchData = useCallback(async () => {
    try {
      const [adjRes, logRes] = await Promise.all([
        apiFetch("/api/learning2/adjustments", {}, null),
        apiFetch("/api/agents/learning_2/logs?limit=30", {}, []),
      ]);
      setData(adjRes);
      setLogs(Array.isArray(logRes) ? logRes : []);
      setError(null);
      setLastUpdate(new Date());
    } catch (err) {
      setError(err.message || "Impossible de charger les données Learning 2");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isActive) fetchData();
    const id = setInterval(fetchData, POLL_NORMAL);
    return () => clearInterval(id);
  }, [isActive, fetchData]);

  const triggerLearning = async () => {
    setTriggering(true);
    try {
      await apiFetch("/api/learning2/trigger", { method: "POST" }, null);
      setTimeout(fetchData, 2000);
    } catch (err) {
      setError(err.message || "Échec du recalcul Learning 2");
    } finally {
      setTriggering(false);
    }
  };

  const tickerAdj = data?.ticker_adj || {};
  const newscatAdj = data?.newscat_adj || {};
  const directionAdj = data?.direction_adj || {};
  const signalCal = data?.signal_calibration || {};
  const anomalies = data?.anomalies || [];
  const stats = data?.stats || {};

  const filteredLogs = logFilter === "ALL"
    ? logs
    : logs.filter((l) => l.level === logFilter);

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>{"\ud83d\udca1"} Agent Learning 2 — Trend Learning</h2>
        <span className="agent-page-desc">
          Apprentissage adaptatif pour Trader 2 — 4 dimensions (ticker, newscat, direction, seuil)
        </span>
        <LastUpdated date={lastUpdate} />
      </div>

      <ErrorBanner error={error} onRetry={fetchData} />

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement...</div>}

      {/* Stats KPIs */}
      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-value">{stats.total_periods || 0}</div>
          <div className="kpi-label">Périodes analysées</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{stats.win_rate || 0}%</div>
          <div className="kpi-label">Win Rate</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value" style={{ color: (stats.avg_pnl_pct || 0) >= 0 ? "var(--green)" : "var(--red)" }}>
            {(stats.avg_pnl_pct || 0) >= 0 ? "+" : ""}{(stats.avg_pnl_pct || 0).toFixed(2)}%
          </div>
          <div className="kpi-label">P&L Moyen</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{signalCal.threshold_adj ? signalCal.threshold_adj.toFixed(2) : "1.00"}</div>
          <div className="kpi-label">Seuil Adj.</div>
        </div>
      </div>

      {/* Trigger button */}
      <div style={{ marginBottom: 16 }}>
        <button className="trigger-btn" onClick={triggerLearning} disabled={triggering}>
          {triggering ? "En cours..." : "Recalculer Learning 2"}
        </button>
      </div>

      {/* Anomalies */}
      {anomalies.length > 0 && (
        <div className="section-card" style={{ borderLeft: "3px solid var(--yellow)" }}>
          <h3>{"\u26a0\ufe0f"} Anomalies détectées</h3>
          {anomalies.map((a, i) => (
            <div key={i} style={{ padding: "4px 0", fontSize: 13 }}>{a}</div>
          ))}
        </div>
      )}

      {/* Per-ticker adjustments */}
      <div className="section-card">
        <h3>Ajustements par ticker</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          Multiplieur appliqué au signal de chaque ticker (1.0 = neutre)
        </p>
        {Object.keys(tickerAdj).length > 0 ? (
          Object.entries(tickerAdj).map(([ticker, val]) => (
            <AdjBar key={ticker} label={ticker} value={val} />
          ))
        ) : (
          <EmptyState message="Pas assez de données" detail="Minimum 4 flips par ticker requis" />
        )}
      </div>

      {/* Per-newscat adjustments */}
      <div className="section-card">
        <h3>Ajustements par catégorie de news</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          Quelles catégories produisent de bons retournements ?
        </p>
        {Object.keys(newscatAdj).length > 0 ? (
          Object.entries(newscatAdj).map(([cat, val]) => (
            <AdjBar key={cat} label={cat} value={val} />
          ))
        ) : (
          <EmptyState message="Pas assez de données" detail="Minimum 3 flips par catégorie requis" />
        )}
      </div>

      {/* Per-direction adjustments */}
      <div className="section-card">
        <h3>Ajustements par direction</h3>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          LONG vs SHORT — quelle direction performe mieux ?
        </p>
        {Object.keys(directionAdj).length > 0 ? (
          Object.entries(directionAdj).map(([dir, val]) => (
            <AdjBar key={dir} label={dir} value={val} />
          ))
        ) : (
          <EmptyState message="Pas assez de données" detail="Minimum 4 flips par direction requis" />
        )}
      </div>

      {/* Signal calibration */}
      <div className="section-card">
        <h3>Calibration du seuil de flip</h3>
        <div style={{ display: "flex", gap: 24, padding: 8 }}>
          <div>
            <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>Seuil ajusté</div>
            <div style={{ fontSize: 18, fontWeight: 600 }}>
              {(20 * (signalCal.threshold_adj || 1.0)).toFixed(1)}
            </div>
          </div>
          <div>
            <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>Force moyenne</div>
            <div style={{ fontSize: 18, fontWeight: 600 }}>
              {(signalCal.avg_strength || 0).toFixed(1)}
            </div>
          </div>
        </div>
      </div>

      {/* Agent Logs */}
      <LogSection
        logs={filteredLogs}
        logFilter={logFilter}
        setLogFilter={setLogFilter}
      />
    </div>
  );
}
