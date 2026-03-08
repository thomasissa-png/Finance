import React, { useState, useEffect, useCallback, useMemo } from "react";
import { pnlColor } from "../utils/format";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, LineChart, Line, CartesianGrid } from "recharts";

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

  const agentMap = useMemo(() => {
    const m = {};
    (agents || []).forEach((a) => { m[a.name] = a; });
    return m;
  }, [agents]);

  const workingCount = (agents || []).filter((a) => a.status === "working").length;
  const errorCount = (agents || []).filter((a) => a.status === "error").length;

  // P2.3: Win rate by week chart data
  const wrChartData = useMemo(() => {
    if (!Array.isArray(history) || history.length < 2) return [];
    return history.map((h) => ({
      date: h.date || h.timestamp ? new Date(h.date || h.timestamp).toLocaleDateString("fr-FR", { day: "2-digit", month: "short" }) : "",
      wr: h.win_rate ?? 0,
      pnl: h.total_pnl_pct ?? h.pnl ?? 0,
    })).slice(-14);
  }, [history]);

  // P2.4: Team comparison data
  const teamCompare = useMemo(() => {
    if (!report) return [];
    const t1 = report.trader_1 || {};
    const t2 = report.trader_2 || {};
    const t3 = report.trader_3 || {};
    const t4 = report.trader_4 || {};
    return [
      { name: "Éq. 1", wr: t1.win_rate ?? 0, pnl: t1.pnl_total ?? 0, trades: t1.total_trades ?? 0 },
      { name: "Éq. 2", wr: t2.flip_win_rate ?? 0, pnl: t2.realized_pnl ?? 0, trades: t2.total_flips ?? 0 },
      { name: "Éq. 3", wr: t3.win_rate ?? 0, pnl: t3.pnl_total ?? 0, trades: t3.total_trades ?? 0 },
      { name: "Éq. 4", wr: t4.win_rate ?? 0, pnl: t4.pnl_total ?? 0, trades: t4.total_trades ?? 0 },
    ];
  }, [report]);

  // P4.3: Dynamic agent versions from live agents data
  const agentVersions = useMemo(() => {
    return (agents || [])
      .filter((a) => a.version)
      .map((a) => ({
        agent: a.name?.replace(/_/g, " "),
        version: a.version,
        status: a.status,
        name: a.name,
      }))
      .sort((a, b) => a.agent.localeCompare(b.agent));
  }, [agents]);

  const t1 = report?.trader_1 || {};
  const t2 = report?.trader_2 || {};

  return (
    <div className="agent-page page-fade-in">
      <div className="page-header">
        <div className="page-title">Performance</div>
        <div className="page-subtitle">KPIs consolidés, évolution des équipes, versions des agents</div>
      </div>

      {loading && (
        <div className="agent-loading"><span className="spinner" /> Chargement...</div>
      )}

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

      {/* P2.4: Team comparison bar chart */}
      {teamCompare.some((t) => t.trades > 0) && (
        <div className="section-card" style={{ padding: 20 }}>
          <h3>Comparaison des équipes</h3>
          <div style={{ width: "100%", height: 200, marginTop: 12 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={teamCompare} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
                <XAxis dataKey="name" tick={{ fontSize: 11, fill: "#8B9DC3" }} tickLine={false} axisLine={{ stroke: "#1E2D4A" }} />
                <YAxis tick={{ fontSize: 10, fill: "#8B9DC3" }} tickLine={false} axisLine={false} />
                <Tooltip contentStyle={{ background: "#131D33", border: "1px solid #1E2D4A", borderRadius: 6, fontSize: 12, color: "#E8ECF4" }} />
                <Bar dataKey="wr" name="Win Rate %" fill="#3B82F6" radius={[4, 4, 0, 0]} />
                <Bar dataKey="pnl" name="P&L %" fill="#10B981" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* P2.3: Win rate evolution chart */}
      {wrChartData.length >= 2 && (
        <div className="section-card" style={{ padding: 20 }}>
          <h3>Évolution win rate et P&L</h3>
          <div style={{ width: "100%", height: 200, marginTop: 12 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={wrChartData} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1E2D4A" />
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#8B9DC3" }} tickLine={false} axisLine={{ stroke: "#1E2D4A" }} />
                <YAxis tick={{ fontSize: 10, fill: "#8B9DC3" }} tickLine={false} axisLine={false} />
                <Tooltip contentStyle={{ background: "#131D33", border: "1px solid #1E2D4A", borderRadius: 6, fontSize: 12, color: "#E8ECF4" }} />
                <Line type="monotone" dataKey="wr" name="Win Rate %" stroke="#3B82F6" strokeWidth={2} dot={{ r: 3 }} />
                <Line type="monotone" dataKey="pnl" name="P&L %" stroke="#10B981" strokeWidth={2} dot={{ r: 3 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Per-team performance */}
      <div className="section-card">
        <h3>Performance par équipe</h3>
        <div className="perf-grid">
          <div className="perf-card">
            <div className="perf-card-title">Équipe 1 Intraday</div>
            <div className="perf-card-value" style={{ color: (t1.win_rate || 0) >= 50 ? "var(--green)" : "var(--red)" }}>
              {t1.win_rate != null ? `${t1.win_rate.toFixed(1)}% WR` : "N/A"}
            </div>
            <div className="perf-card-sub">
              {t1.total_trades || 0} trades | P&L {t1.pnl_total != null ? `${t1.pnl_total > 0 ? "+" : ""}${t1.pnl_total.toFixed(2)}%` : "N/A"}
            </div>
          </div>
          <div className="perf-card">
            <div className="perf-card-title">Équipe 2 Tendance</div>
            <div className="perf-card-value" style={{ color: (t2.flip_win_rate || 0) >= 50 ? "var(--green)" : "var(--red)" }}>
              {t2.flip_win_rate != null ? `${t2.flip_win_rate.toFixed(1)}% WR` : "N/A"}
            </div>
            <div className="perf-card-sub">
              P&L réalisé {t2.realized_pnl != null ? `${t2.realized_pnl > 0 ? "+" : ""}${t2.realized_pnl.toFixed(2)}%` : "N/A"}
            </div>
          </div>
          <div className="perf-card">
            <div className="perf-card-title">Équipe 3 Technique</div>
            <div className="perf-card-value" style={{ color: (report?.trader_3?.win_rate || 0) >= 50 ? "var(--green)" : "var(--red)" }}>
              {report?.trader_3?.win_rate != null ? `${report.trader_3.win_rate.toFixed(1)}% WR` : "N/A"}
            </div>
            <div className="perf-card-sub">
              {report?.trader_3?.total_trades || 0} trades
            </div>
          </div>
          <div className="perf-card">
            <div className="perf-card-title">Équipe 4 Meta</div>
            <div className="perf-card-value" style={{ color: (report?.trader_4?.win_rate || 0) >= 50 ? "var(--green)" : "var(--red)" }}>
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

      {/* P4.3: Dynamic version tracking from live agent data */}
      <div className="section-card">
        <h3>Versions des agents (live)</h3>
        <div className="compact-table">
          <table className="version-table">
            <thead>
              <tr>
                <th>Agent</th>
                <th>Version</th>
                <th>Statut</th>
              </tr>
            </thead>
            <tbody>
              {agentVersions.map((av) => (
                <tr key={av.name}>
                  <td className="ticker-cell">{av.agent}</td>
                  <td style={{ color: "var(--accent)" }}>v{av.version}</td>
                  <td>
                    <span className={`status-dot ${av.status === "working" ? "online" : av.status === "error" ? "offline" : ""}`}
                      style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", marginRight: 6,
                        background: av.status === "working" ? "var(--accent)" : av.status === "error" ? "var(--red)" : "var(--text-muted)" }} />
                    {av.status === "working" ? "Actif" : av.status === "error" ? "Erreur" : "En attente"}
                  </td>
                </tr>
              ))}
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
