import React, { useState, useEffect, useCallback, useMemo } from "react";
import { pnlColor } from "../utils/format";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";

const TEAMS = [
  {
    id: "1",
    name: "Équipe 1",
    subtitle: "Day Trading Intraday",
    desc: "News trading event-driven, 0-1 trade par scan, TP/SL intraday",
    agents: ["scoring", "trader_1", "journal", "learning"],
    perfKey: "trader_1",
  },
  {
    id: "2",
    name: "Équipe 2",
    subtitle: "Tendance Commodities",
    desc: "Trend following sur 4 commodities, positions longue durée",
    agents: ["scoring_2", "trader_2", "journal_2", "learning_2"],
    perfKey: "trader_2",
  },
  {
    id: "3",
    name: "Équipe 3",
    subtitle: "Indicateurs Techniques",
    desc: "RSI, MACD, Bollinger, positions heures à 3 jours",
    agents: ["scoring_3", "trader_3", "journal_3", "learning_3"],
    perfKey: "trader_3",
  },
  {
    id: "4",
    name: "Équipe 4",
    subtitle: "Meta / Ensemble",
    desc: "Confluence-driven, combine les signaux des 3 équipes",
    agents: ["scoring_4", "trader_4", "journal_4", "learning_4"],
    perfKey: "trader_4",
  },
];

export default function TeamsOverviewPage({ isActive, agents, onNavigate }) {
  const [report, setReport] = useState(null);

  const fetchReport = useCallback(async () => {
    try {
      const r = await fetch("/api/performance/report");
      if (r.ok) setReport(await r.json());
    } catch { /* */ }
  }, []);

  useEffect(() => {
    fetchReport();
    const id = setInterval(fetchReport, 120_000);
    return () => clearInterval(id);
  }, [fetchReport]);

  useEffect(() => { if (isActive) fetchReport(); }, [isActive, fetchReport]);

  const agentMap = useMemo(() => {
    const m = {};
    (agents || []).forEach((a) => { m[a.name] = a; });
    return m;
  }, [agents]);

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

  // P2.4: Team comparison chart data
  const compareData = useMemo(() => {
    if (!report) return [];
    return TEAMS.map((team) => {
      const d = report[team.perfKey] || {};
      return {
        name: team.name.replace("Équipe ", "Éq. "),
        "Win Rate": team.id === "2" ? (d.flip_win_rate ?? 0) : (d.win_rate ?? 0),
        "P&L": team.id === "2" ? (d.realized_pnl ?? 0) : (d.pnl_total ?? 0),
      };
    });
  }, [report]);

  return (
    <div className="agent-page page-fade-in">
      <div className="page-header">
        <div className="page-title">Équipes de Trading</div>
        <div className="page-subtitle">Vue d'ensemble des 4 équipes et de leur performance</div>
      </div>

      {/* P2.4: Team comparison chart */}
      {compareData.some((d) => d["Win Rate"] > 0 || d["P&L"] !== 0) && (
        <div className="section-card" style={{ padding: 20 }}>
          <h3>Comparaison des équipes</h3>
          <div style={{ width: "100%", height: 180, marginTop: 12 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={compareData} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
                <XAxis dataKey="name" tick={{ fontSize: 11, fill: "#8B9DC3" }} tickLine={false} axisLine={{ stroke: "#1E2D4A" }} />
                <YAxis tick={{ fontSize: 10, fill: "#8B9DC3" }} tickLine={false} axisLine={false} />
                <Tooltip contentStyle={{ background: "#131D33", border: "1px solid #1E2D4A", borderRadius: 6, fontSize: 12, color: "#E8ECF4" }} />
                <Bar dataKey="Win Rate" fill="#3B82F6" radius={[4, 4, 0, 0]} />
                <Bar dataKey="P&L" fill="#10B981" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      <div className="teams-grid">
        {TEAMS.map((team) => {
          const perfData = report?.[team.perfKey] || {};
          const wr = team.id === "2" ? perfData.flip_win_rate : perfData.win_rate;
          const pnl = team.id === "2" ? perfData.realized_pnl : perfData.pnl_total;
          const trades = team.id === "2" ? (perfData.total_flips || 0) : (perfData.total_trades || 0);

          const teamAgents = team.agents.map((n) => agentMap[n]).filter(Boolean);
          const working = teamAgents.filter((a) => a.status === "working").length;
          const errors = teamAgents.filter((a) => a.status === "error").length;

          return (
            <div
              key={team.id}
              className="team-overview-card"
              onClick={() => onNavigate(`team${team.id}`)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onNavigate(`team${team.id}`); } }}
            >
              <div className="team-overview-header">
                <div>
                  <div className="team-overview-name">{team.name}</div>
                  <div className="team-overview-subtitle">{team.subtitle}</div>
                </div>
                <div className="team-overview-status">
                  {errors > 0 && <span className="status-dot offline" title="Agents en erreur" />}
                  {working > 0 && <span className="status-dot online" title="Agents actifs" />}
                  {errors === 0 && working === 0 && <span className="status-dot" style={{ background: "var(--text-muted)" }} />}
                </div>
              </div>

              <div className="team-overview-desc">{team.desc}</div>

              <div className="team-overview-kpis">
                <div className="team-overview-kpi">
                  <span className="team-overview-kpi-value" style={{ color: wr != null && wr >= 50 ? "var(--green)" : wr != null ? "var(--red)" : undefined }}>
                    {wr != null ? `${wr.toFixed(1)}%` : "N/A"}
                  </span>
                  <span className="team-overview-kpi-label">Win rate</span>
                </div>
                <div className="team-overview-kpi">
                  <span className="team-overview-kpi-value" style={{ color: pnlColor(pnl) }}>
                    {pnl != null ? `${pnl > 0 ? "+" : ""}${pnl.toFixed(2)}%` : "N/A"}
                  </span>
                  <span className="team-overview-kpi-label">P&L</span>
                </div>
                <div className="team-overview-kpi">
                  <span className="team-overview-kpi-value">{trades}</span>
                  <span className="team-overview-kpi-label">Trades</span>
                </div>
              </div>

              <div className="team-overview-agents">
                {team.agents.map((name) => {
                  const a = agentMap[name];
                  const color = a?.status === "working" ? "var(--accent)" : a?.status === "error" ? "var(--red)" : "var(--text-muted)";
                  return (
                    <span key={name} className="team-overview-agent-dot" title={name.replace(/_/g, " ")}>
                      <span style={{ width: 6, height: 6, borderRadius: "50%", background: color, display: "inline-block" }} />
                      <span className="team-overview-agent-name">{name.replace(/_/g, " ")}</span>
                    </span>
                  );
                })}
              </div>

              <div className="team-overview-action">
                Voir les détails →
              </div>
            </div>
          );
        })}
      </div>

      {/* Agent versions (live) */}
      <div className="section-card" style={{ marginTop: 20 }}>
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
                    <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", marginRight: 6,
                      background: av.status === "working" ? "var(--accent)" : av.status === "error" ? "var(--red)" : "var(--text-muted)" }} />
                    {av.status === "working" ? "Actif" : av.status === "error" ? "Erreur" : "En attente"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
