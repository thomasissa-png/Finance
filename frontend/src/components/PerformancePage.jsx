import React, { useState, useEffect, useCallback, useMemo } from "react";
import { pnlColor } from "../utils/format";
import { POLL_SLOW, TEAM_COLORS, CHART_TOOLTIP_STYLE } from "../utils/constants";
import { apiFetch } from "../utils/api";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, LineChart, Line, CartesianGrid, Legend, Cell, PieChart, Pie } from "recharts";

/* ── Agent version table from registry ── */
const AGENT_VERSIONS_STATIC = [
  { agent: "News", key: "news", team: "Partagé" },
  { agent: "Scoring", key: "scoring", team: "Partagé" },
  { agent: "Scoring 2", key: "scoring_2", team: "Éq. 2" },
  { agent: "Scoring 3", key: "scoring_3", team: "Éq. 3" },
  { agent: "Scoring 4", key: "scoring_4", team: "Éq. 4" },
  { agent: "Trader 1", key: "trader_1", team: "Éq. 1" },
  { agent: "Trader 2", key: "trader_2", team: "Éq. 2" },
  { agent: "Trader 3", key: "trader_3", team: "Éq. 3" },
  { agent: "Trader 4", key: "trader_4", team: "Éq. 4" },
  { agent: "Journal 1", key: "journal", team: "Éq. 1" },
  { agent: "Journal 2", key: "journal_2", team: "Éq. 2" },
  { agent: "Journal 3", key: "journal_3", team: "Éq. 3" },
  { agent: "Journal 4", key: "journal_4", team: "Éq. 4" },
  { agent: "Learning 1", key: "learning", team: "Éq. 1" },
  { agent: "Learning 2", key: "learning_2", team: "Éq. 2" },
  { agent: "Learning 3", key: "learning_3", team: "Éq. 3" },
  { agent: "Learning 4", key: "learning_4", team: "Éq. 4" },
  { agent: "Infrastructure", key: "infrastructure", team: "Partagé" },
  { agent: "Performance", key: "performance", team: "Partagé" },
  { agent: "Auditeur", key: "auditor", team: "Partagé" },
];

function StatBar({ value, max = 100, color = "var(--accent)", label, sublabel }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 2 }}>
        <span style={{ color: "var(--text-secondary)" }}>{label}</span>
        <span style={{ fontWeight: 600, color }}>{sublabel || `${value?.toFixed?.(1) ?? value}%`}</span>
      </div>
      <div style={{ height: 6, background: "var(--bg-secondary)", borderRadius: 3 }}>
        <div style={{ height: "100%", width: `${pct}%`, background: color, borderRadius: 3, transition: "width 0.3s" }} />
      </div>
    </div>
  );
}

export default function PerformancePage({ isActive, agents }) {
  const [perf, setPerf] = useState(null);
  const [report, setReport] = useState(null);
  const [dailyReports, setDailyReports] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState("overview");

  const fetchData = useCallback(async () => {
    try {
      const [pRes, rRes, hRes] = await Promise.all([
        apiFetch("/api/performance", {}, null),
        apiFetch("/api/performance/report", {}, null),
        apiFetch("/api/performance/history?limit=30", {}, {}),
      ]);
      setPerf(pRes);
      setReport(rRes);
      const reports = hRes?.daily_reports || [];
      setDailyReports(Array.isArray(reports) ? reports : []);
    } catch { /* */ } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, POLL_SLOW);
    return () => clearInterval(id);
  }, [fetchData]);

  useEffect(() => { if (isActive) fetchData(); }, [isActive, fetchData]);

  // ── Derived data ──
  const agentVersionMap = useMemo(() => {
    const map = {};
    (agents || []).forEach((a) => { map[a.name] = a; });
    return map;
  }, [agents]);

  const t1 = report?.trader_1 || {};
  const t2 = report?.trader_2 || {};
  const t3 = report?.trader_3 || {};
  const t4 = report?.trader_4 || {};

  // Chart: WR + P&L evolution from daily reports
  const evolutionData = useMemo(() => {
    if (!dailyReports.length) return [];
    return dailyReports.map((r) => {
      const t1d = r.trader_1 || {};
      const t2d = r.trader_2 || {};
      const t3d = r.trader_3 || {};
      const t4d = r.trader_4 || {};
      return {
        date: r.date ? new Date(r.date).toLocaleDateString("fr-FR", { day: "2-digit", month: "short" }) : "",
        wr1: t1d.win_rate ?? null,
        wr3: t3d.win_rate ?? null,
        pnl1: t1d.total_pnl ?? t1d.pnl_total ?? null,
        pnl2: t2d.total_realized_pnl ?? t2d.realized_pnl ?? null,
        pnl3: t3d.total_realized_pnl ?? t3d.pnl_total ?? null,
        pnl4: t4d.total_realized_pnl ?? t4d.pnl_total ?? null,
      };
    }).slice(-14);
  }, [dailyReports]);

  // Team comparison
  const teamCompare = useMemo(() => {
    if (!report) return [];
    return [
      { name: "Éq. 1 Intraday", wr: t1.win_rate ?? 0, pnl: t1.pnl_total ?? t1.total_pnl ?? 0, trades: t1.total_trades ?? 0, color: TEAM_COLORS["1"] },
      { name: "Éq. 2 Tendance", wr: t2.flip_win_rate ?? 0, pnl: t2.total_realized_pnl ?? t2.realized_pnl ?? 0, trades: t2.flip_count ?? t2.total_flips ?? 0, color: TEAM_COLORS["2"] },
      { name: "Éq. 3 Technique", wr: t3.win_rate ?? 0, pnl: t3.pnl_total ?? t3.total_realized_pnl ?? 0, trades: t3.total_trades ?? 0, color: TEAM_COLORS["3"] },
      { name: "Éq. 4 Meta", wr: t4.win_rate ?? 0, pnl: t4.pnl_total ?? t4.total_realized_pnl ?? 0, trades: t4.total_trades ?? 0, color: TEAM_COLORS["4"] },
    ];
  }, [report, t1, t2, t3, t4]);

  // By category (Team 1)
  const categoryData = useMemo(() => {
    const cats = t1.by_category || {};
    return Object.entries(cats).map(([cat, stats]) => ({
      name: cat,
      trades: stats.trades || 0,
      wr: stats.win_rate || 0,
      pnl: stats.pnl || 0,
    })).sort((a, b) => b.trades - a.trades);
  }, [t1]);

  // By ticker (Team 2)
  const tickerData2 = useMemo(() => {
    const tickers = t2.by_ticker || {};
    return Object.entries(tickers).map(([ticker, stats]) => ({
      name: ticker.replace("=F", ""),
      flips: stats.flips || 0,
      wr: stats.win_rate || 0,
      pnl: stats.pnl || 0,
      avgDur: stats.avg_duration_days,
    }));
  }, [t2]);

  const workingCount = (agents || []).filter((a) => a.status === "working").length;
  const errorCount = (agents || []).filter((a) => a.status === "error").length;

  const TABS = [
    { id: "overview", label: "Vue globale" },
    { id: "teams", label: "Par équipe" },
    { id: "evolution", label: "Évolution" },
    { id: "versions", label: "Versions agents" },
    { id: "alerts", label: `Alertes${report?.alerts?.length ? ` (${report.alerts.length})` : ""}` },
  ];

  return (
    <div className="agent-page page-fade-in">
      <div className="page-header">
        <div>
          <div className="page-title">Performance</div>
          <div className="page-subtitle">Suivi détaillé, versioning, évolution</div>
        </div>
        <button className="refresh-btn" onClick={fetchData} title="Rafraîchir">&#x21BB;</button>
      </div>

      {loading && (
        <div className="agent-loading"><span className="spinner" /> Chargement...</div>
      )}

      {/* Tabs */}
      <div className="perf-tabs">
        {TABS.map((tab) => (
          <button key={tab.id} className={`perf-tab ${activeTab === tab.id ? "active" : ""}`} onClick={() => setActiveTab(tab.id)}>
            {tab.label}
          </button>
        ))}
      </div>

      {/* ══════════════════════ TAB: Overview ══════════════════════ */}
      {activeTab === "overview" && (() => {
        // ── Consolidated KPIs across all 4 teams ──
        const teamStats = [
          { wr: t1.win_rate, pnl: t1.pnl_total ?? t1.total_pnl ?? 0, n: t1.total_trades || 0, wins: perf?.wins || 0, losses: perf?.losses || 0, expired: perf?.expired || 0, pending: perf?.pending || 0 },
          { wr: t2.flip_win_rate, pnl: t2.total_realized_pnl ?? t2.realized_pnl ?? 0, n: t2.flip_count || 0, wins: t2.flip_wins || 0, losses: t2.flip_losses || 0, expired: 0, pending: 0 },
          { wr: t3.win_rate, pnl: t3.pnl_total ?? t3.total_realized_pnl ?? 0, n: t3.total_trades || 0, wins: t3.wins || 0, losses: t3.losses || 0, expired: t3.expired || 0, pending: 0 },
          { wr: t4.win_rate, pnl: t4.pnl_total ?? t4.total_realized_pnl ?? 0, n: t4.total_trades || 0, wins: t4.wins || 0, losses: t4.losses || 0, expired: t4.expired || 0, pending: 0 },
        ];
        const totalTrades = teamStats.reduce((s, x) => s + x.n, 0);
        const withWr = teamStats.filter((x) => x.wr != null && x.n > 0);
        const totalN = withWr.reduce((s, x) => s + x.n, 0);
        const consolidatedWr = totalN > 0 ? withWr.reduce((s, x) => s + x.wr * x.n, 0) / totalN : 0;
        const totalPnl = teamStats.reduce((s, x) => s + x.pnl, 0);
        const totalWins = teamStats.reduce((s, x) => s + x.wins, 0);
        const totalLosses = teamStats.reduce((s, x) => s + x.losses, 0);
        const totalExpired = teamStats.reduce((s, x) => s + x.expired, 0);
        const totalPending = teamStats.reduce((s, x) => s + x.pending, 0);
        const totalClosed = totalWins + totalLosses + totalExpired;

        return (
        <>
          {/* Consolidated KPIs */}
          <div className="kpi-row">
            <div className="kpi-card">
              <div className="kpi-value">{totalTrades}</div>
              <div className="kpi-label">Trades total (4 éq.)</div>
            </div>
            <div className="kpi-card">
              <div className="kpi-value" style={{ color: consolidatedWr >= 50 ? "var(--green)" : consolidatedWr > 0 ? "var(--red)" : undefined }}>
                {consolidatedWr > 0 ? `${consolidatedWr.toFixed(1)}%` : "N/A"}
              </div>
              <div className="kpi-label">Win rate consolidé</div>
            </div>
            <div className="kpi-card">
              <div className="kpi-value" style={{ color: pnlColor(totalPnl) }}>
                {`${totalPnl > 0 ? "+" : ""}${totalPnl.toFixed(2)}%`}
              </div>
              <div className="kpi-label">P&L consolidé</div>
            </div>
            <div className="kpi-card">
              <div className="kpi-value">{(agents || []).length}</div>
              <div className="kpi-label">Agents</div>
            </div>
            <div className="kpi-card">
              <div className="kpi-value" style={{ color: errorCount > 0 ? "var(--red)" : "var(--green)" }}>
                {errorCount}
              </div>
              <div className="kpi-label">Erreurs</div>
            </div>
          </div>

          {/* Per-team P&L and WR summary table */}
          {report && (
            <div className="section-card" style={{ padding: 20 }}>
              <h3>Résumé par équipe</h3>
              <div className="compact-table">
                <table>
                  <thead>
                    <tr>
                      <th>Équipe</th>
                      <th>Trades</th>
                      <th>Win Rate</th>
                      <th>P&L</th>
                      <th>Meilleur</th>
                      <th>Pire</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      { name: "Éq. 1 — Intraday", color: TEAM_COLORS["1"], wr: t1.win_rate, pnl: t1.pnl_total ?? t1.total_pnl, n: t1.total_trades || 0, best: t1.best_ticker, worst: t1.worst_ticker },
                      { name: "Éq. 2 — Tendance", color: TEAM_COLORS["2"], wr: t2.flip_win_rate, pnl: t2.total_realized_pnl ?? t2.realized_pnl, n: t2.flip_count || 0, best: t2.best_ticker, worst: t2.worst_ticker },
                      { name: "Éq. 3 — Technique", color: TEAM_COLORS["3"], wr: t3.win_rate, pnl: t3.pnl_total ?? t3.total_realized_pnl, n: t3.total_trades || 0, best: t3.best_strategy || t3.best_ticker, worst: t3.worst_strategy || t3.worst_ticker },
                      { name: "Éq. 4 — Meta", color: TEAM_COLORS["4"], wr: t4.win_rate, pnl: t4.pnl_total ?? t4.total_realized_pnl, n: t4.total_trades || 0, best: t4.best_combo, worst: t4.worst_combo },
                    ].map((team) => (
                      <tr key={team.name}>
                        <td style={{ fontWeight: 600 }}>
                          <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: team.color, marginRight: 6 }} />
                          {team.name}
                        </td>
                        <td>{team.n}</td>
                        <td style={{ color: team.wr != null && team.wr >= 50 ? "var(--green)" : team.wr != null ? "var(--red)" : "var(--text-muted)", fontWeight: 600 }}>
                          {team.wr != null ? `${team.wr.toFixed(1)}%` : "N/A"}
                        </td>
                        <td style={{ color: pnlColor(team.pnl), fontWeight: 600 }}>
                          {team.pnl != null ? `${team.pnl > 0 ? "+" : ""}${team.pnl.toFixed(2)}%` : "N/A"}
                        </td>
                        <td style={{ fontSize: 11, color: "var(--green)" }}>{team.best || "--"}</td>
                        <td style={{ fontSize: 11, color: "var(--red)" }}>{team.worst || "--"}</td>
                      </tr>
                    ))}
                    <tr style={{ borderTop: "2px solid var(--border)", fontWeight: 700 }}>
                      <td>TOTAL</td>
                      <td>{totalTrades}</td>
                      <td style={{ color: consolidatedWr >= 50 ? "var(--green)" : "var(--red)" }}>
                        {consolidatedWr > 0 ? `${consolidatedWr.toFixed(1)}%` : "N/A"}
                      </td>
                      <td style={{ color: pnlColor(totalPnl) }}>
                        {`${totalPnl > 0 ? "+" : ""}${totalPnl.toFixed(2)}%`}
                      </td>
                      <td colSpan="2" />
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Team comparison chart */}
          {teamCompare.some((t) => t.trades > 0) && (
            <div className="section-card" style={{ padding: 20 }}>
              <h3>Comparaison visuelle</h3>
              <div style={{ width: "100%", height: 220, marginTop: 12 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={teamCompare} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
                    <XAxis dataKey="name" tick={{ fontSize: 10, fill: "#8B9DC3" }} tickLine={false} axisLine={{ stroke: "#1E2D4A" }} />
                    <YAxis tick={{ fontSize: 10, fill: "#8B9DC3" }} tickLine={false} axisLine={false} />
                    <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                    <Legend wrapperStyle={{ fontSize: 11, color: "#8B9DC3" }} />
                    <Bar dataKey="wr" name="Win Rate %" fill="#3B82F6" radius={[4, 4, 0, 0]} />
                    <Bar dataKey="pnl" name="P&L %" fill="#10B981" radius={[4, 4, 0, 0]} />
                    <Bar dataKey="trades" name="Trades" fill="#F59E0B" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* Consolidated results distribution */}
          {totalClosed > 0 && (
            <div className="section-card" style={{ padding: 20 }}>
              <h3>Distribution des résultats (toutes équipes)</h3>
              <div style={{ display: "flex", gap: 24, alignItems: "center", flexWrap: "wrap" }}>
                <div style={{ width: 180, height: 180 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={[
                          { name: "TP Hit / Win", value: totalWins, color: "#10B981" },
                          { name: "SL Hit / Loss", value: totalLosses, color: "#EF4444" },
                          { name: "Expired", value: totalExpired, color: "#6B7280" },
                        ].filter(d => d.value > 0)}
                        cx="50%" cy="50%" innerRadius={40} outerRadius={70}
                        paddingAngle={3} dataKey="value"
                      >
                        {[
                          { color: "#10B981" }, { color: "#EF4444" }, { color: "#6B7280" },
                        ].map((entry, i) => (
                          <Cell key={i} fill={entry.color} />
                        ))}
                      </Pie>
                      <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div style={{ flex: 1 }}>
                  <StatBar value={totalWins} max={totalClosed || 1} color="var(--green)" label="Gains (TP/Win)" sublabel={`${totalWins} trades`} />
                  <StatBar value={totalLosses} max={totalClosed || 1} color="var(--red)" label="Pertes (SL/Loss)" sublabel={`${totalLosses} trades`} />
                  <StatBar value={totalExpired} max={totalClosed || 1} color="#6B7280" label="Expired" sublabel={`${totalExpired} trades`} />
                  {totalPending > 0 && (
                    <StatBar value={totalPending} max={totalTrades || 1} color="var(--accent)" label="En cours" sublabel={`${totalPending} trades`} />
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Scoring & News agent metrics */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
            {report?.scoring && (
              <div className="section-card" style={{ padding: 16 }}>
                <h3>Agent Scoring</h3>
                <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
                  <StatBar value={report.scoring.total_scored || 0} max={Math.max(report.scoring.total_scored || 1, 100)} color="var(--accent)" label="News scorées" sublabel={`${report.scoring.total_scored || 0}`} />
                  <StatBar value={report.scoring.cache_hit_rate || 0} max={100} color="#F59E0B" label="Cache hit rate" />
                  <StatBar value={report.scoring.zero_edge_filtered || 0} max={Math.max(report.scoring.total_scored || 1, 1)} color="#6B7280" label="Zero-edge filtrées" sublabel={`${report.scoring.zero_edge_filtered || 0}`} />
                  {report.scoring.avg_score != null && (
                    <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>
                      Score moyen: <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>{report.scoring.avg_score.toFixed(1)}</span>
                      {report.scoring.tokens_total > 0 && (
                        <> | Tokens: <span style={{ fontWeight: 600 }}>{(report.scoring.tokens_total / 1000).toFixed(1)}k</span></>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}
            {report?.news && (
              <div className="section-card" style={{ padding: 16 }}>
                <h3>Agent News</h3>
                <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
                  <StatBar value={report.news.total_collected || 0} max={Math.max(report.news.total_collected || 1, 500)} color="var(--accent)" label="News collectées" sublabel={`${report.news.total_collected || 0}`} />
                  <StatBar value={report.news.dedup_filtered || 0} max={Math.max(report.news.total_collected || 1, 1)} color="#F59E0B" label="Dedup filtrées" sublabel={`${report.news.dedup_filtered || 0}`} />
                  <StatBar value={report.news.source_errors || 0} max={50} color="var(--red)" label="Erreurs source" sublabel={`${report.news.source_errors || 0}`} />
                  {report.news.avg_items_per_scan != null && (
                    <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>
                      Moy. news/scan: <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>{report.news.avg_items_per_scan.toFixed(0)}</span>
                      {report.news.avg_collection_ms != null && (
                        <> | Vitesse: <span style={{ fontWeight: 600 }}>{(report.news.avg_collection_ms / 1000).toFixed(1)}s</span></>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </>
        );
      })()}

      {/* ══════════════════════ TAB: Teams ══════════════════════ */}
      {activeTab === "teams" && (
        <>
          {/* Team 1 — Intraday */}
          <div className="section-card" style={{ padding: 20, borderLeft: `3px solid ${TEAM_COLORS["1"]}` }}>
            <h3 style={{ color: TEAM_COLORS["1"] }}>Équipe 1 — Day Trading Intraday</h3>
            <div className="kpi-row" style={{ marginTop: 12 }}>
              <div className="kpi-card mini">
                <div className="kpi-value" style={{ color: (t1.win_rate || 0) >= 50 ? "var(--green)" : "var(--red)" }}>
                  {t1.win_rate != null ? `${t1.win_rate.toFixed(1)}%` : "N/A"}
                </div>
                <div className="kpi-label">Win Rate</div>
              </div>
              <div className="kpi-card mini">
                <div className="kpi-value" style={{ color: pnlColor(t1.pnl_total ?? t1.total_pnl) }}>
                  {t1.pnl_total != null ? `${t1.pnl_total > 0 ? "+" : ""}${t1.pnl_total.toFixed(2)}%` : t1.total_pnl != null ? `${t1.total_pnl > 0 ? "+" : ""}${t1.total_pnl.toFixed(2)}%` : "N/A"}
                </div>
                <div className="kpi-label">P&L total</div>
              </div>
              <div className="kpi-card mini">
                <div className="kpi-value">{t1.total_trades || 0}</div>
                <div className="kpi-label">Trades</div>
              </div>
              <div className="kpi-card mini">
                <div className="kpi-value" style={{ color: (t1.expired_rate || 0) > 50 ? "var(--red)" : undefined }}>
                  {t1.expired_rate != null ? `${t1.expired_rate.toFixed(1)}%` : "N/A"}
                </div>
                <div className="kpi-label">Expired rate</div>
              </div>
              <div className="kpi-card mini">
                <div className="kpi-value">{t1.avg_rr_realized != null ? t1.avg_rr_realized.toFixed(2) : "N/A"}</div>
                <div className="kpi-label">R/R réalisé</div>
              </div>
              <div className="kpi-card mini">
                <div className="kpi-value" style={{ color: (t1.direction_accuracy || 0) >= 50 ? "var(--green)" : "var(--red)" }}>
                  {t1.direction_accuracy != null ? `${t1.direction_accuracy.toFixed(1)}%` : "N/A"}
                </div>
                <div className="kpi-label">Direction accuracy</div>
              </div>
            </div>
            {t1.best_ticker && (
              <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 8 }}>
                Meilleur ticker: <span style={{ color: "var(--green)", fontWeight: 600 }}>{t1.best_ticker}</span>
                {t1.worst_ticker && <> | Pire ticker: <span style={{ color: "var(--red)", fontWeight: 600 }}>{t1.worst_ticker}</span></>}
              </div>
            )}

            {/* Top 3 categories */}
            {categoryData.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 6 }}>Top stratégies (par catégorie de news)</div>
                <div className="compact-table">
                  <table>
                    <thead>
                      <tr><th>#</th><th>Catégorie</th><th>Trades</th><th>WR</th><th>P&L</th></tr>
                    </thead>
                    <tbody>
                      {categoryData.slice(0, 5).map((c, i) => (
                        <tr key={c.name}>
                          <td style={{ color: i < 3 ? "var(--accent)" : "var(--text-muted)", fontWeight: 600 }}>{i + 1}</td>
                          <td><code style={{ fontSize: 11 }}>{c.name}</code></td>
                          <td>{c.trades}</td>
                          <td style={{ color: c.wr >= 50 ? "var(--green)" : c.wr > 0 ? "var(--red)" : "var(--text-muted)" }}>{c.wr.toFixed(1)}%</td>
                          <td style={{ color: pnlColor(c.pnl), fontWeight: 600 }}>{c.pnl > 0 ? "+" : ""}{c.pnl.toFixed(2)}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>

          {/* Team 2 — Trend */}
          <div className="section-card" style={{ padding: 20, borderLeft: `3px solid ${TEAM_COLORS["2"]}` }}>
            <h3 style={{ color: TEAM_COLORS["2"] }}>Équipe 2 — Trend Following Commodities</h3>
            <div className="kpi-row" style={{ marginTop: 12 }}>
              <div className="kpi-card mini">
                <div className="kpi-value" style={{ color: (t2.flip_win_rate || 0) >= 50 ? "var(--green)" : "var(--red)" }}>
                  {t2.flip_win_rate != null ? `${t2.flip_win_rate.toFixed(1)}%` : "N/A"}
                </div>
                <div className="kpi-label">Flip Win Rate</div>
              </div>
              <div className="kpi-card mini">
                <div className="kpi-value" style={{ color: pnlColor(t2.total_realized_pnl ?? t2.realized_pnl) }}>
                  {(t2.total_realized_pnl ?? t2.realized_pnl) != null ? `${(t2.total_realized_pnl ?? t2.realized_pnl) > 0 ? "+" : ""}${(t2.total_realized_pnl ?? t2.realized_pnl).toFixed(2)}%` : "N/A"}
                </div>
                <div className="kpi-label">P&L réalisé</div>
              </div>
              <div className="kpi-card mini">
                <div className="kpi-value" style={{ color: pnlColor(t2.total_unrealized_pnl) }}>
                  {t2.total_unrealized_pnl != null ? `${t2.total_unrealized_pnl > 0 ? "+" : ""}${t2.total_unrealized_pnl.toFixed(2)}%` : "N/A"}
                </div>
                <div className="kpi-label">P&L latent</div>
              </div>
              <div className="kpi-card mini">
                <div className="kpi-value">{t2.flip_count || 0}</div>
                <div className="kpi-label">Flips</div>
              </div>
              <div className="kpi-card mini">
                <div className="kpi-value">{t2.positions_coverage || 0}</div>
                <div className="kpi-label">Positions actives</div>
              </div>
              <div className="kpi-card mini">
                <div className="kpi-value">{t2.avg_position_duration_days != null ? `${t2.avg_position_duration_days.toFixed(1)}j` : "N/A"}</div>
                <div className="kpi-label">Durée moy.</div>
              </div>
            </div>

            {/* Per-ticker breakdown */}
            {tickerData2.length > 0 && (
              <div className="compact-table" style={{ marginTop: 12 }}>
                <table>
                  <thead>
                    <tr><th>Ticker</th><th>Flips</th><th>WR</th><th>P&L</th><th>Durée moy.</th></tr>
                  </thead>
                  <tbody>
                    {tickerData2.map((t) => (
                      <tr key={t.name}>
                        <td className="ticker-cell">{t.name}</td>
                        <td>{t.flips}</td>
                        <td style={{ color: t.wr >= 50 ? "var(--green)" : t.wr > 0 ? "var(--red)" : "var(--text-muted)" }}>{t.wr.toFixed(1)}%</td>
                        <td style={{ color: pnlColor(t.pnl), fontWeight: 600 }}>{t.pnl > 0 ? "+" : ""}{t.pnl.toFixed(2)}%</td>
                        <td>{t.avgDur != null ? `${t.avgDur.toFixed(1)}j` : "--"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Team 3 — Technique */}
          <div className="section-card" style={{ padding: 20, borderLeft: `3px solid ${TEAM_COLORS["3"]}` }}>
            <h3 style={{ color: TEAM_COLORS["3"] }}>Équipe 3 — Trading Technique</h3>
            <div className="kpi-row" style={{ marginTop: 12 }}>
              <div className="kpi-card mini">
                <div className="kpi-value" style={{ color: (t3.win_rate || 0) >= 50 ? "var(--green)" : "var(--red)" }}>
                  {t3.win_rate != null ? `${t3.win_rate.toFixed(1)}%` : "N/A"}
                </div>
                <div className="kpi-label">Win Rate</div>
              </div>
              <div className="kpi-card mini">
                <div className="kpi-value" style={{ color: pnlColor(t3.total_realized_pnl ?? t3.pnl_total) }}>
                  {(t3.total_realized_pnl ?? t3.pnl_total) != null ? `${(t3.total_realized_pnl ?? t3.pnl_total) > 0 ? "+" : ""}${(t3.total_realized_pnl ?? t3.pnl_total).toFixed(2)}%` : "N/A"}
                </div>
                <div className="kpi-label">P&L réalisé</div>
              </div>
              <div className="kpi-card mini">
                <div className="kpi-value">{t3.total_trades || 0}</div>
                <div className="kpi-label">Trades</div>
              </div>
              <div className="kpi-card mini">
                <div className="kpi-value">{t3.active_positions || 0}</div>
                <div className="kpi-label">Positions ouvertes</div>
              </div>
              <div className="kpi-card mini">
                <div className="kpi-value">{t3.strategies_active || 0}</div>
                <div className="kpi-label">Stratégies actives</div>
              </div>
              <div className="kpi-card mini">
                <div className="kpi-value" style={{ color: t3.has_weekly_config ? "var(--green)" : "var(--text-muted)" }}>
                  {t3.has_weekly_config ? "Oui" : "Non"}
                </div>
                <div className="kpi-label">Config hebdo</div>
              </div>
            </div>
            {t3.best_strategy && (
              <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 8 }}>
                Meilleure stratégie: <span style={{ color: "var(--green)", fontWeight: 600 }}>{t3.best_strategy}</span>
                {t3.worst_strategy && <> | Pire: <span style={{ color: "var(--red)", fontWeight: 600 }}>{t3.worst_strategy}</span></>}
              </div>
            )}

            {/* Top strategies table */}
            {t3.by_strategy && Object.keys(t3.by_strategy).length > 0 && (() => {
              const sorted = Object.entries(t3.by_strategy)
                .sort(([, a], [, b]) => (b.pnl || 0) - (a.pnl || 0));
              return (
                <div style={{ marginTop: 12 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 6 }}>Performance par stratégie</div>
                  <div className="compact-table">
                    <table>
                      <thead>
                        <tr><th>#</th><th>Stratégie</th><th>Trades</th><th>WR</th><th>P&L</th></tr>
                      </thead>
                      <tbody>
                        {sorted.map(([strat, stats], i) => (
                          <tr key={strat}>
                            <td style={{ color: i < 3 ? "var(--accent)" : "var(--text-muted)", fontWeight: 600 }}>{i + 1}</td>
                            <td><code style={{ fontSize: 11 }}>{strat}</code></td>
                            <td>{stats.trades || 0}</td>
                            <td style={{ color: (stats.win_rate || 0) >= 50 ? "var(--green)" : "var(--red)" }}>{(stats.win_rate || 0).toFixed(1)}%</td>
                            <td style={{ color: pnlColor(stats.pnl), fontWeight: 600 }}>{(stats.pnl || 0) > 0 ? "+" : ""}{(stats.pnl || 0).toFixed(2)}%</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              );
            })()}
          </div>

          {/* Team 4 — Meta */}
          <div className="section-card" style={{ padding: 20, borderLeft: `3px solid ${TEAM_COLORS["4"]}` }}>
            <h3 style={{ color: TEAM_COLORS["4"] }}>Équipe 4 — Meta/Ensemble</h3>
            <div className="kpi-row" style={{ marginTop: 12 }}>
              <div className="kpi-card mini">
                <div className="kpi-value" style={{ color: (t4.win_rate || 0) >= 80 ? "var(--green)" : (t4.win_rate || 0) >= 50 ? "var(--yellow)" : "var(--red)" }}>
                  {t4.win_rate != null ? `${t4.win_rate.toFixed(1)}%` : "N/A"}
                </div>
                <div className="kpi-label">Win Rate (obj. 80%)</div>
              </div>
              <div className="kpi-card mini">
                <div className="kpi-value" style={{ color: pnlColor(t4.total_realized_pnl ?? t4.pnl_total) }}>
                  {(t4.total_realized_pnl ?? t4.pnl_total) != null ? `${(t4.total_realized_pnl ?? t4.pnl_total) > 0 ? "+" : ""}${(t4.total_realized_pnl ?? t4.pnl_total).toFixed(2)}%` : "N/A"}
                </div>
                <div className="kpi-label">P&L réalisé</div>
              </div>
              <div className="kpi-card mini">
                <div className="kpi-value">{t4.total_trades || 0}</div>
                <div className="kpi-label">Trades</div>
              </div>
              <div className="kpi-card mini">
                <div className="kpi-value">{t4.open_positions || 0}</div>
                <div className="kpi-label">Positions ouvertes</div>
              </div>
              <div className="kpi-card mini">
                <div className="kpi-value" style={{ color: pnlColor(t4.total_unrealized_pnl) }}>
                  {t4.total_unrealized_pnl != null ? `${t4.total_unrealized_pnl > 0 ? "+" : ""}${t4.total_unrealized_pnl.toFixed(2)}%` : "N/A"}
                </div>
                <div className="kpi-label">P&L latent</div>
              </div>
              <div className="kpi-card mini">
                <div className="kpi-value" style={{ color: t4.upstream_ready ? "var(--green)" : "var(--yellow)" }}>
                  {t4.upstream_ready ? "Prêt" : "Attente"}
                </div>
                <div className="kpi-label">Upstream</div>
              </div>
            </div>
            {t4.best_combo && (
              <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 8 }}>
                Meilleure combo: <span style={{ color: "var(--green)", fontWeight: 600 }}>{t4.best_combo}</span>
                {t4.worst_combo && <> | Pire: <span style={{ color: "var(--red)", fontWeight: 600 }}>{t4.worst_combo}</span></>}
              </div>
            )}

            {/* By combo table */}
            {t4.by_combo && Object.keys(t4.by_combo).length > 0 && (() => {
              const sorted = Object.entries(t4.by_combo)
                .sort(([, a], [, b]) => (b.pnl || 0) - (a.pnl || 0));
              return (
                <div style={{ marginTop: 12 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 6 }}>Performance par combinaison</div>
                  <div className="compact-table">
                    <table>
                      <thead>
                        <tr><th>#</th><th>Combinaison</th><th>Trades</th><th>WR</th><th>P&L</th></tr>
                      </thead>
                      <tbody>
                        {sorted.map(([combo, stats], i) => (
                          <tr key={combo}>
                            <td style={{ color: i < 3 ? "var(--accent)" : "var(--text-muted)", fontWeight: 600 }}>{i + 1}</td>
                            <td><code style={{ fontSize: 11 }}>{combo}</code></td>
                            <td>{stats.trades || 0}</td>
                            <td style={{ color: (stats.win_rate || 0) >= 50 ? "var(--green)" : "var(--red)" }}>{(stats.win_rate || 0).toFixed(1)}%</td>
                            <td style={{ color: pnlColor(stats.pnl), fontWeight: 600 }}>{(stats.pnl || 0) > 0 ? "+" : ""}{(stats.pnl || 0).toFixed(2)}%</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              );
            })()}

            <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 8 }}>
              Objectif: 80% WR | Activation: 16 mars 2026 | Combine les signaux des 3 équipes
            </div>
          </div>
        </>
      )}

      {/* ══════════════════════ TAB: Evolution ══════════════════════ */}
      {activeTab === "evolution" && (
        <>
          {evolutionData.length >= 2 ? (
            <>
              {/* P&L evolution per team */}
              <div className="section-card" style={{ padding: 20 }}>
                <h3>Évolution P&L par équipe (14 derniers jours)</h3>
                <div style={{ width: "100%", height: 260, marginTop: 12 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={evolutionData} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1E2D4A" />
                      <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#8B9DC3" }} tickLine={false} axisLine={{ stroke: "#1E2D4A" }} />
                      <YAxis tick={{ fontSize: 10, fill: "#8B9DC3" }} tickLine={false} axisLine={false} tickFormatter={(v) => `${v > 0 ? "+" : ""}${v.toFixed(1)}%`} width={50} />
                      <Tooltip contentStyle={CHART_TOOLTIP_STYLE} formatter={(v) => v != null ? [`${v > 0 ? "+" : ""}${v.toFixed(2)}%`] : ["N/A"]} />
                      <Legend wrapperStyle={{ fontSize: 11, color: "#8B9DC3" }} />
                      <Line type="monotone" dataKey="pnl1" name="Éq. 1 Intraday" stroke={TEAM_COLORS["1"]} strokeWidth={2} dot={{ r: 3 }} connectNulls />
                      <Line type="monotone" dataKey="pnl2" name="Éq. 2 Tendance" stroke={TEAM_COLORS["2"]} strokeWidth={2} dot={{ r: 3 }} connectNulls />
                      <Line type="monotone" dataKey="pnl3" name="Éq. 3 Technique" stroke={TEAM_COLORS["3"]} strokeWidth={2} dot={{ r: 3 }} connectNulls />
                      <Line type="monotone" dataKey="pnl4" name="Éq. 4 Meta" stroke={TEAM_COLORS["4"]} strokeWidth={2} dot={{ r: 3 }} connectNulls />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* Win rate evolution */}
              <div className="section-card" style={{ padding: 20 }}>
                <h3>Évolution Win Rate</h3>
                <div style={{ width: "100%", height: 220, marginTop: 12 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={evolutionData} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1E2D4A" />
                      <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#8B9DC3" }} tickLine={false} axisLine={{ stroke: "#1E2D4A" }} />
                      <YAxis tick={{ fontSize: 10, fill: "#8B9DC3" }} tickLine={false} axisLine={false} domain={[0, 100]} tickFormatter={(v) => `${v}%`} width={40} />
                      <Tooltip contentStyle={CHART_TOOLTIP_STYLE} formatter={(v) => v != null ? [`${v.toFixed(1)}%`] : ["N/A"]} />
                      <Legend wrapperStyle={{ fontSize: 11, color: "#8B9DC3" }} />
                      <Line type="monotone" dataKey="wr1" name="Éq. 1 WR" stroke={TEAM_COLORS["1"]} strokeWidth={2} dot={{ r: 3 }} connectNulls />
                      <Line type="monotone" dataKey="wr3" name="Éq. 3 WR" stroke={TEAM_COLORS["3"]} strokeWidth={2} dot={{ r: 3 }} connectNulls />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </>
          ) : (
            <div className="section-card" style={{ padding: 20, textAlign: "center", color: "var(--text-muted)" }}>
              Pas assez de rapports quotidiens pour afficher l'évolution (minimum 2 jours).
              <br />
              <span style={{ fontSize: 12 }}>Les rapports sont générés automatiquement à 22h30 CET chaque jour ouvrable.</span>
            </div>
          )}

          {/* By scan type performance */}
          {perf?.by_scan_type && Object.keys(perf.by_scan_type).length > 0 && (
            <div className="section-card" style={{ padding: 20 }}>
              <h3>Performance par session (Éq. 1)</h3>
              <div className="compact-table">
                <table>
                  <thead>
                    <tr><th>Session</th><th>Trades</th><th>Win Rate</th><th>P&L</th></tr>
                  </thead>
                  <tbody>
                    {Object.entries(perf.by_scan_type).map(([scan, stats]) => (
                      <tr key={scan}>
                        <td style={{ fontWeight: 600 }}>{scan === "EUROPE" ? "Europe (07:50-14:50)" : scan === "US" ? "US (14:50-17:00)" : scan}</td>
                        <td>{stats.total || 0}</td>
                        <td style={{ color: (stats.win_rate || 0) >= 50 ? "var(--green)" : "var(--red)" }}>
                          {(stats.win_rate || 0).toFixed(1)}%
                        </td>
                        <td style={{ color: pnlColor(stats.pnl_sum), fontWeight: 600 }}>
                          {(stats.pnl_sum || 0) > 0 ? "+" : ""}{(stats.pnl_sum || 0).toFixed(2)}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}

      {/* ══════════════════════ TAB: Versions ══════════════════════ */}
      {activeTab === "versions" && (
        <div className="section-card" style={{ padding: 20 }}>
          <h3>Versions des agents</h3>
          <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 12 }}>
            Chaque version d'agent est trackée. Les trades sont stampés avec les versions actives au moment de leur création.
            Le learning ne considère que les trades produits par les versions courantes.
          </div>
          <div className="compact-table">
            <table>
              <thead>
                <tr>
                  <th>Agent</th>
                  <th>Équipe</th>
                  <th>Version</th>
                  <th>Status</th>
                  <th>Dernière action</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {AGENT_VERSIONS_STATIC.map((av) => {
                  const agent = agentVersionMap[av.key];
                  const statusColor = agent?.status === "idle" ? "var(--green)"
                    : agent?.status === "working" ? "var(--accent)"
                    : agent?.status === "error" ? "var(--red)"
                    : "var(--text-muted)";
                  return (
                    <tr key={av.key}>
                      <td style={{ fontWeight: 600 }}>{av.agent}</td>
                      <td>
                        <span style={{
                          fontSize: 10, padding: "2px 6px", borderRadius: 4,
                          background: av.team === "Partagé" ? "rgba(59,130,246,0.15)" : av.team.includes("1") ? "rgba(59,130,246,0.1)" : av.team.includes("2") ? "rgba(245,158,11,0.1)" : av.team.includes("3") ? "rgba(139,92,246,0.1)" : "rgba(236,72,153,0.1)",
                          color: av.team === "Partagé" ? "#3B82F6" : av.team.includes("1") ? TEAM_COLORS["1"] : av.team.includes("2") ? TEAM_COLORS["2"] : av.team.includes("3") ? TEAM_COLORS["3"] : TEAM_COLORS["4"],
                        }}>
                          {av.team}
                        </span>
                      </td>
                      <td>
                        <code style={{ fontSize: 12, background: "var(--bg-secondary)", padding: "2px 6px", borderRadius: 4 }}>
                          v{agent?.version || "?"}
                        </code>
                      </td>
                      <td>
                        <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                          <span style={{ width: 8, height: 8, borderRadius: "50%", background: statusColor, display: "inline-block" }} />
                          <span style={{ fontSize: 11, color: statusColor }}>{agent?.status || "unknown"}</span>
                        </span>
                      </td>
                      <td style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                        {agent?.last_action || "--"}
                      </td>
                      <td style={{ fontSize: 11 }}>{agent?.action_count || 0}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ══════════════════════ TAB: Alerts ══════════════════════ */}
      {activeTab === "alerts" && (
        <>
          {report?.alerts && report.alerts.length > 0 ? (
            <div className="section-card" style={{ padding: 20 }}>
              <h3>Alertes actives ({report.alerts.length})</h3>
              <div className="agent-logs" style={{ marginTop: 12 }}>
                {report.alerts.map((alert, i) => {
                  const level = alert.severity || alert.level || "WARN";
                  const msg = typeof alert.message === "string" ? alert.message
                    : typeof alert.description === "string" ? alert.description
                    : typeof alert === "string" ? alert
                    : JSON.stringify(alert);
                  return (
                    <div key={i} className={`agent-log-entry ${level === "CRITICAL" ? "error" : "warn"}`}>
                      <div className="agent-log-header">
                        <span className="agent-log-icon" style={{ fontSize: 14 }}>
                          {level === "CRITICAL" ? "!!" : "!"}
                        </span>
                        <span style={{ fontSize: 11, color: level === "CRITICAL" ? "var(--red)" : "var(--yellow)", fontWeight: 600 }}>
                          {level}
                        </span>
                        <span className="agent-log-action" style={{ color: "var(--text-primary)" }}>
                          {msg}
                        </span>
                      </div>
                      {alert.agent && (
                        <div style={{ fontSize: 11, color: "var(--text-muted)", marginLeft: 24, marginTop: 2 }}>
                          Agent: {typeof alert.agent === "string" ? alert.agent : JSON.stringify(alert.agent)}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ) : (
            <div className="section-card" style={{ padding: 20, textAlign: "center", color: "var(--text-muted)" }}>
              Aucune alerte active. Le système fonctionne normalement.
            </div>
          )}

          {/* Top performers & Underperformers */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
            <div className="section-card" style={{ padding: 16 }}>
              <h3 style={{ color: "var(--green)" }}>Top performers</h3>
              {report?.top_performers?.length > 0 ? (
                <div className="agent-logs" style={{ marginTop: 8 }}>
                  {report.top_performers.map((p, i) => {
                    const agent = typeof p === "string" ? p : (p.agent || "?");
                    const reason = typeof p === "string" ? "" : (p.reason || "");
                    return (
                      <div key={i} className="agent-log-entry" style={{ padding: "6px 8px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <span style={{ fontWeight: 600, color: "var(--green)" }}>{agent}</span>
                        {reason && <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{reason}</span>}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div style={{ color: "var(--text-muted)", fontSize: 12, marginTop: 8 }}>Pas encore de données</div>
              )}
            </div>
            <div className="section-card" style={{ padding: 16 }}>
              <h3 style={{ color: "var(--red)" }}>Sous-performers</h3>
              {report?.underperformers?.length > 0 ? (
                <div className="agent-logs" style={{ marginTop: 8 }}>
                  {report.underperformers.map((p, i) => {
                    const agent = typeof p === "string" ? p : (p.agent || "?");
                    const reason = typeof p === "string" ? "" : (p.reason || "");
                    return (
                      <div key={i} className="agent-log-entry" style={{ padding: "6px 8px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <span style={{ fontWeight: 600, color: "var(--red)" }}>{agent}</span>
                        {reason && <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{reason}</span>}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div style={{ color: "var(--text-muted)", fontSize: 12, marginTop: 8 }}>Aucun agent sous-performant</div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
