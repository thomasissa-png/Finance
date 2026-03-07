import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, BarChart, Bar, Cell } from "recharts";
import { CATEGORY_COLORS, timeAgo, pnlColor } from "../utils/format";

// (M8) Period filter
const PERIODS = [
  { key: "all", label: "Tout" },
  { key: "30d", label: "30j" },
  { key: "7d", label: "7j" },
];

function filterByPeriod(trades, period) {
  if (period === "all" || !trades) return trades;
  const days = period === "7d" ? 7 : 30;
  const cutoff = Date.now() - days * 86400000;
  return trades.filter((t) => new Date(t.timestamp).getTime() >= cutoff);
}

// (C1) Build equity curve data from trades
function buildEquityCurve(trades) {
  if (!trades || !trades.length) return [];
  const sorted = trades
    .filter((t) => t.pnl_pct != null && t.result !== "PENDING")
    .sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));

  let cumPnl = 0;
  return sorted.map((t) => {
    cumPnl += t.pnl_pct;
    return {
      date: new Date(t.timestamp).toLocaleDateString("fr-FR", { day: "2-digit", month: "short" }),
      pnl: parseFloat(cumPnl.toFixed(2)),
      trade: `${t.ticker} ${t.pnl_pct > 0 ? "+" : ""}${t.pnl_pct}%`,
    };
  });
}

// (M8) Build P&L by day of week
function buildDayOfWeekStats(trades) {
  const days = ["Dim", "Lun", "Mar", "Mer", "Jeu", "Ven", "Sam"];
  const stats = {};
  days.forEach((d, i) => { stats[i] = { name: d, pnl: 0, count: 0 }; });

  (trades || []).forEach((t) => {
    if (t.pnl_pct == null || t.result === "PENDING") return;
    const day = new Date(t.timestamp).getDay();
    stats[day].pnl += t.pnl_pct;
    stats[day].count++;
  });

  return Object.values(stats).filter((d) => d.count > 0).map((d) => ({
    ...d,
    pnl: parseFloat(d.pnl.toFixed(2)),
  }));
}

// (M8) Compute enriched stats
function computeEnrichedStats(stats, trades) {
  if (!stats || !trades) return {};
  const closed = trades.filter((t) => t.result && t.result !== "PENDING" && t.pnl_pct != null);
  if (!closed.length) return {};

  // Average R/R realized
  const avgRR = closed.reduce((s, t) => s + (t.risk_reward || 0), 0) / closed.length;

  // Best/worst category
  const catStats = {};
  closed.forEach((t) => {
    const cat = t.news_category || "other";
    if (!catStats[cat]) catStats[cat] = { pnl: 0, count: 0 };
    catStats[cat].pnl += t.pnl_pct;
    catStats[cat].count++;
  });

  let bestCat = null, worstCat = null;
  for (const [cat, data] of Object.entries(catStats)) {
    if (data.count >= 2) {
      if (!bestCat || data.pnl > catStats[bestCat]?.pnl) bestCat = cat;
      if (!worstCat || data.pnl < catStats[worstCat]?.pnl) worstCat = cat;
    }
  }

  // Streak
  let currentStreak = 0, maxStreak = 0, streakType = "";
  const sorted = closed.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
  sorted.forEach((t) => {
    const win = t.result === "TP_HIT";
    if (win) {
      if (streakType === "win") { currentStreak++; } else { currentStreak = 1; streakType = "win"; }
    } else {
      if (streakType === "loss") { currentStreak++; } else { currentStreak = 1; streakType = "loss"; }
    }
    maxStreak = Math.max(maxStreak, currentStreak);
  });

  return { avgRR: avgRR.toFixed(2), bestCat, worstCat, maxStreak, streakType, currentStreak };
}

// Custom tooltip for equity curve
function EquityTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{
      background: "var(--bg-card)", border: "1px solid var(--border)",
      borderRadius: 6, padding: "8px 12px", fontSize: 12, fontFamily: "var(--font-mono)",
    }}>
      <div style={{ color: "var(--text-muted)" }}>{payload[0].payload.date}</div>
      <div style={{ color: payload[0].value >= 0 ? "var(--green)" : "var(--red)", fontWeight: 700 }}>
        {payload[0].value > 0 ? "+" : ""}{payload[0].value}%
      </div>
      <div style={{ color: "var(--text-muted)", fontSize: 10 }}>{payload[0].payload.trade}</div>
    </div>
  );
}

export default function Performance({ isActive }) {
  const [stats, setStats] = useState(null);
  const [trades, setTrades] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [period, setPeriod] = useState("all");

  const fetchAll = useCallback(() => {
    if (document.hidden) return;
    Promise.all([
      fetch("/api/performance").then((r) => (r.ok ? r.json() : null)).catch(() => null),
      fetch("/api/trades").then((r) => (r.ok ? r.json() : [])).catch(() => []),
    ]).then(([s, t]) => {
      setStats(s);
      setTrades(t);
      setLastUpdate(new Date());
    }).finally(() => setIsLoading(false));
  }, []);

  // Fetch on mount + poll every 60s
  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, 60_000);
    return () => clearInterval(id);
  }, [fetchAll]);

  // Refetch when tab becomes active
  useEffect(() => {
    if (isActive) fetchAll();
  }, [isActive, fetchAll]);

  // Refetch when browser tab regains focus
  useEffect(() => {
    const onVisible = () => { if (!document.hidden) fetchAll(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [fetchAll]);

  // Period-filtered data
  const filteredTrades = useMemo(() => filterByPeriod(trades, period), [trades, period]);
  const equityData = useMemo(() => buildEquityCurve(filteredTrades), [filteredTrades]);
  const dayOfWeekData = useMemo(() => buildDayOfWeekStats(filteredTrades), [filteredTrades]);
  const enriched = useMemo(() => computeEnrichedStats(stats, filteredTrades), [stats, filteredTrades]);

  if (isLoading) {
    return (
      <div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12, marginBottom: 16 }}>
          <div className="skeleton skeleton-kpi" />
          <div className="skeleton skeleton-kpi" />
          <div className="skeleton skeleton-kpi" />
        </div>
        <div className="skeleton skeleton-card" style={{ height: 200 }} />
      </div>
    );
  }

  if (!stats || stats.total_trades === 0) {
    return (
      <div className="no-trade">
        <div className="no-trade-icon">—</div>
        <div className="no-trade-title">Pas encore de données</div>
        <div className="no-trade-reason">
          Les statistiques apparaîtront après la clôture des premiers trades.
        </div>
        <div className="no-trade-meta">
          <span className="no-trade-tag">Journal auto : 22h CET</span>
          <span className="no-trade-tag">Min. 1 trade clôturé</span>
        </div>
      </div>
    );
  }

  const totalPnlColor = stats.total_pnl_pct > 0 ? "var(--green)" : stats.total_pnl_pct < 0 ? "var(--red)" : "var(--text-primary)";

  return (
    <div>
      {/* (M8) Period toggle */}
      <div className="period-toggle">
        {PERIODS.map((p) => (
          <button
            key={p.key}
            className={`period-btn ${period === p.key ? "active" : ""}`}
            onClick={() => setPeriod(p.key)}
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* Primary KPIs */}
      <div className="perf-kpi-row">
        <div className="perf-card-primary">
          <div className="perf-card-value" style={{
            color: stats.win_rate >= 55 ? "var(--green)" : stats.win_rate < 45 ? "var(--red)" : "var(--yellow)",
          }}>
            {stats.win_rate}%
          </div>
          <div className="perf-card-label">Win rate</div>
        </div>
        <div className="perf-card-primary">
          <div className="perf-card-value" style={{ color: totalPnlColor }}>
            {stats.total_pnl_pct > 0 ? "+" : ""}{stats.total_pnl_pct}%
          </div>
          <div className="perf-card-label">P&L cumulé</div>
        </div>
        <div className="perf-card-primary">
          <div className="perf-card-value" style={{ color: "var(--cyan)" }}>
            {stats.total_trades}
          </div>
          <div className="perf-card-label">Trades total</div>
        </div>
      </div>

      {/* (C1) Equity curve chart */}
      {equityData.length > 1 && (
        <div className="chart-container">
          <div className="chart-title">Courbe de P&L cumulé</div>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={equityData}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="date" tick={{ fill: "var(--text-muted)", fontSize: 10 }} />
              <YAxis tick={{ fill: "var(--text-muted)", fontSize: 10 }} tickFormatter={(v) => `${v}%`} />
              <Tooltip content={<EquityTooltip />} />
              <Line
                type="monotone"
                dataKey="pnl"
                stroke="var(--cyan)"
                strokeWidth={2}
                dot={{ r: 3, fill: "var(--cyan)" }}
                activeDot={{ r: 5 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Secondary stats */}
      <div className="perf-grid">
        <div className="perf-card">
          <div className="perf-card-value" style={{ color: "var(--green)" }}>
            {stats.best_trade_pnl > 0 ? "+" : ""}{stats.best_trade_pnl}%
          </div>
          <div className="perf-card-label">Meilleur trade</div>
        </div>
        <div className="perf-card">
          <div className="perf-card-value" style={{ color: "var(--red)" }}>
            {stats.worst_trade_pnl}%
          </div>
          <div className="perf-card-label">Pire trade</div>
        </div>
        <div className="perf-card">
          <div className="perf-card-value">{stats.avg_confidence}%</div>
          <div className="perf-card-label">Confiance moy.</div>
        </div>
        <div className="perf-card">
          <div className="perf-card-value" style={{ color: "var(--green)" }}>{stats.wins}</div>
          <div className="perf-card-label">Gains</div>
        </div>
        <div className="perf-card">
          <div className="perf-card-value" style={{ color: "var(--red)" }}>{stats.losses}</div>
          <div className="perf-card-label">Pertes</div>
        </div>
        <div className="perf-card">
          <div className="perf-card-value" style={{ color: "var(--blue)" }}>{stats.pending}</div>
          <div className="perf-card-label">En cours</div>
        </div>
        {/* (M8) Enriched stats */}
        {enriched.avgRR && (
          <div className="perf-card">
            <div className="perf-card-value" style={{ color: "var(--cyan)" }}>{enriched.avgRR}</div>
            <div className="perf-card-label">R/R moyen</div>
          </div>
        )}
        {enriched.maxStreak > 0 && (
          <div className="perf-card">
            <div className="perf-card-value" style={{ color: enriched.streakType === "win" ? "var(--green)" : "var(--red)" }}>
              {enriched.maxStreak}
            </div>
            <div className="perf-card-label">Max streak</div>
          </div>
        )}
      </div>

      {/* (M8) P&L by day of week */}
      {dayOfWeekData.length > 0 && (
        <div className="chart-container">
          <div className="chart-title">P&L par jour de semaine</div>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={dayOfWeekData}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="name" tick={{ fill: "var(--text-muted)", fontSize: 11 }} />
              <YAxis tick={{ fill: "var(--text-muted)", fontSize: 10 }} tickFormatter={(v) => `${v}%`} />
              <Tooltip
                contentStyle={{
                  background: "var(--bg-card)", border: "1px solid var(--border)",
                  borderRadius: 6, fontSize: 12, fontFamily: "var(--font-mono)",
                }}
                formatter={(value) => [`${value}%`, "P&L"]}
              />
              <Bar dataKey="pnl" radius={[4, 4, 0, 0]}>
                {dayOfWeekData.map((entry, i) => (
                  <Cell key={i} fill={entry.pnl >= 0 ? "var(--green)" : "var(--red)"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Breakdown by category */}
      {Object.keys(stats.by_category).length > 0 && (
        <div style={{ marginTop: 24 }}>
          <h3 className="section-header">
            Par catégorie
            {enriched.bestCat && (
              <span style={{ fontSize: 11, color: "var(--green)", marginLeft: 12 }}>
                Meilleure : {enriched.bestCat}
              </span>
            )}
          </h3>
          <table className="history-table">
            <thead>
              <tr>
                <th>Catégorie</th>
                <th>Trades</th>
                <th>Win rate</th>
                <th>P&L cumulé</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(stats.by_category).map(([cat, data]) => {
                const catColor = CATEGORY_COLORS[cat] || CATEGORY_COLORS.other;
                return (
                  <tr key={cat}>
                    <td>
                      <span className="cat-badge" style={{ background: catColor + "22", color: catColor }}>{cat}</span>
                    </td>
                    <td>{data.total}</td>
                    <td style={{ color: data.win_rate >= 55 ? "var(--green)" : "var(--text-secondary)" }}>
                      {data.win_rate}%
                    </td>
                    <td style={{ color: pnlColor(data.pnl_sum) }}>
                      {data.pnl_sum > 0 ? "+" : ""}{data.pnl_sum?.toFixed(2)}%
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Breakdown by scan type */}
      {Object.keys(stats.by_scan_type).length > 0 && (
        <div style={{ marginTop: 24 }}>
          <h3 className="section-header">Par session</h3>
          <table className="history-table">
            <thead>
              <tr>
                <th>Session</th>
                <th>Trades</th>
                <th>Win rate</th>
                <th>P&L cumulé</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(stats.by_scan_type).map(([st, data]) => (
                <tr key={st}>
                  <td>{st === "europe" ? "Europe" : "US"}</td>
                  <td>{data.total}</td>
                  <td>{data.win_rate}%</td>
                  <td style={{ color: pnlColor(data.pnl_sum) }}>
                    {data.pnl_sum > 0 ? "+" : ""}{data.pnl_sum?.toFixed(2)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* (O1) Last update + refresh */}
      <div className="last-update-bar">
        {lastUpdate && <span className="last-update">MAJ {timeAgo(lastUpdate)}</span>}
        <button className="refresh-btn" onClick={fetchAll} title="Rafraîchir">{"\u21BB"}</button>
      </div>
    </div>
  );
}
