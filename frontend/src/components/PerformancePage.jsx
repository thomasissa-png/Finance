import React, { useState, useEffect, useCallback } from "react";
import { pnlColor } from "../utils/format";

const AGENT_VERSIONS = [
  { agent: "News", version: "7.5" },
  { agent: "Scoring", version: "7.4" },
  { agent: "Scoring 2", version: "7.3" },
  { agent: "Scoring 3", version: "2.0" },
  { agent: "Scoring 4", version: "2.0" },
  { agent: "Trader 1", version: "6.5" },
  { agent: "Trader 2", version: "7.2" },
  { agent: "Trader 3", version: "2.0" },
  { agent: "Trader 4", version: "2.0" },
  { agent: "Journal 1", version: "4.1" },
  { agent: "Journal 2", version: "7.1" },
  { agent: "Journal 3", version: "2.0" },
  { agent: "Journal 4", version: "2.0" },
  { agent: "Learning 1", version: "5.2" },
  { agent: "Learning 2", version: "7.1" },
  { agent: "Learning 3", version: "2.0" },
  { agent: "Learning 4", version: "2.0" },
  { agent: "Infrastructure", version: "7.5" },
  { agent: "Performance", version: "8.1" },
  { agent: "Auditeur", version: "8.1" },
];

export default function PerformancePage({ isActive, agents }) {
  const [perf, setPerf] = useState(null);
  const [report, setReport] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const [pRes, rRes, hRes] = await Promise.all([
        fetch("/api/performance").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch("/api/performance/report").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch("/api/performance/history?limit=30").then((r) => r.ok ? r.json() : []).catch(() => []),
      ]);
      setPerf(pRes);
      setReport(rRes);
      setHistory(Array.isArray(hRes) ? hRes : []);
    } catch { /* */ } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 120_000);
    return () => clearInterval(id);
  }, [fetchData]);

  useEffect(() => { if (isActive) fetchData(); }, [isActive, fetchData]);

  const agentMap = {};
  (agents || []).forEach((a) => { agentMap[a.name] = a; });

  const workingCount = (agents || []).filter((a) => a.status === "working").length;
  const errorCount = (agents || []).filter((a) => a.status === "error").length;

  // Extract per-team KPIs from report
  const t1 = report?.trader_1 || {};
  const t2 = report?.trader_2 || {};

  return (
    <div className="agent-page">
      <div className="page-header">
        <div className="page-title">Performance</div>
        <div className="page-subtitle">KPIs consolides, evolution des equipes, versions des agents</div>
      </div>

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement...</div>}

      {/* Global KPIs */}
      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-value">{(agents || []).length}</div>
          <div className="kpi-label">Agents actifs</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value" style={{ color: workingCount > 0 ? "var(--accent)" : undefined }}>
            {workingCount}
          </div>
          <div className="kpi-label">En cours</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value" style={{ color: errorCount > 0 ? "var(--red)" : "var(--green)" }}>
            {errorCount}
          </div>
          <div className="kpi-label">Erreurs</div>
        </div>
        {perf && (
          <>
            <div className="kpi-card">
              <div className="kpi-value">{perf.total_trades || 0}</div>
              <div className="kpi-label">Trades total</div>
            </div>
            <div className="kpi-card">
              <div className="kpi-value" style={{ color: (perf.win_rate || 0) >= 50 ? "var(--green)" : "var(--red)" }}>
                {(perf.win_rate || 0).toFixed(1)}%
              </div>
              <div className="kpi-label">Win rate global</div>
            </div>
            <div className="kpi-card">
              <div className="kpi-value" style={{ color: pnlColor(perf.total_pnl_pct) }}>
                {perf.total_pnl_pct != null ? `${perf.total_pnl_pct > 0 ? "+" : ""}${perf.total_pnl_pct.toFixed(2)}%` : "--"}
              </div>
              <div className="kpi-label">P&L total</div>
            </div>
          </>
        )}
      </div>

      {/* Per-team performance */}
      <div className="section-card">
        <h3>Performance par equipe</h3>
        <div className="perf-grid">
          <div className="perf-card">
            <div className="perf-card-title">Equipe 1 — Intraday</div>
            <div className="perf-card-value" style={{ color: (t1.win_rate || 0) >= 50 ? "var(--green)" : "var(--red)" }}>
              {t1.win_rate != null ? `${t1.win_rate.toFixed(1)}% WR` : "N/A"}
            </div>
            <div className="perf-card-sub">
              {t1.total_trades || 0} trades | P&L {t1.pnl_total != null ? `${t1.pnl_total > 0 ? "+" : ""}${t1.pnl_total.toFixed(2)}%` : "N/A"}
            </div>
          </div>
          <div className="perf-card">
            <div className="perf-card-title">Equipe 2 — Tendance</div>
            <div className="perf-card-value" style={{ color: (t2.flip_win_rate || 0) >= 50 ? "var(--green)" : "var(--red)" }}>
              {t2.flip_win_rate != null ? `${t2.flip_win_rate.toFixed(1)}% WR` : "N/A"}
            </div>
            <div className="perf-card-sub">
              P&L realise {t2.realized_pnl != null ? `${t2.realized_pnl > 0 ? "+" : ""}${t2.realized_pnl.toFixed(2)}%` : "N/A"}
            </div>
          </div>
          <div className="perf-card">
            <div className="perf-card-title">Equipe 3 — Technique</div>
            <div className="perf-card-value" style={{ color: "var(--text-secondary)" }}>
              {report?.trader_3?.win_rate != null ? `${report.trader_3.win_rate.toFixed(1)}% WR` : "N/A"}
            </div>
            <div className="perf-card-sub">
              {report?.trader_3?.total_trades || 0} trades
            </div>
          </div>
          <div className="perf-card">
            <div className="perf-card-title">Equipe 4 — Meta</div>
            <div className="perf-card-value" style={{ color: "var(--text-secondary)" }}>
              {report?.trader_4?.win_rate != null ? `${report.trader_4.win_rate.toFixed(1)}% WR` : "N/A"}
            </div>
            <div className="perf-card-sub">
              {report?.trader_4?.total_trades || 0} trades | Objectif 80% WR
            </div>
          </div>
        </div>
      </div>

      {/* Agent status overview */}
      <div className="section-card">
        <h3>Statut des agents ({(agents || []).length})</h3>
        <div className="agent-cards-row">
          {(agents || []).map((agent) => {
            const statusColor = agent.status === "working" ? "var(--accent)" : agent.status === "error" ? "var(--red)" : "var(--text-muted)";
            return (
              <div key={agent.name} className="agent-mini-card">
                <div className="agent-mini-card-header">
                  <span className="agent-mini-name">
                    {agent.name?.replace(/_/g, " ")}
                    {agent.version && <span className="agent-mini-version">v{agent.version}</span>}
                  </span>
                  <span className="agent-mini-status" style={{ backgroundColor: statusColor }} />
                </div>
                {agent.last_action && (
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                    {agent.last_action.slice(0, 50)}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Version tracking */}
      <div className="section-card">
        <h3>Versions des agents</h3>
        <div className="compact-table">
          <table className="version-table">
            <thead>
              <tr>
                <th>Agent</th>
                <th>Version actuelle</th>
                <th>Version live</th>
              </tr>
            </thead>
            <tbody>
              {AGENT_VERSIONS.map((av) => {
                const liveAgent = (agents || []).find((a) =>
                  a.name?.replace(/_/g, " ").toLowerCase() === av.agent.toLowerCase() ||
                  a.name === av.agent.toLowerCase().replace(/ /g, "_")
                );
                return (
                  <tr key={av.agent}>
                    <td className="ticker-cell">{av.agent}</td>
                    <td>v{av.version}</td>
                    <td style={{ color: liveAgent?.version ? "var(--green)" : "var(--text-muted)" }}>
                      {liveAgent?.version ? `v${liveAgent.version}` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Alerts from report */}
      {report?.alerts && report.alerts.length > 0 && (
        <div className="section-card">
          <h3>Alertes actives ({report.alerts.length})</h3>
          <div className="agent-logs">
            {report.alerts.map((alert, i) => (
              <div key={i} className={`agent-log-entry ${alert.level === "CRITICAL" ? "error" : "warn"}`}>
                <div className="agent-log-header">
                  <span className="agent-log-icon">{alert.level === "CRITICAL" ? "x" : "!"}</span>
                  <span className="agent-log-action" style={{ color: alert.level === "CRITICAL" ? "var(--red)" : "var(--yellow)" }}>
                    {alert.message || alert.description || JSON.stringify(alert)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
