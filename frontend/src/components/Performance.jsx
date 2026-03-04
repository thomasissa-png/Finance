import React, { useCallback, useEffect, useState } from "react";

export default function Performance() {
  const [stats, setStats] = useState(null);
  const [isLoading, setIsLoading] = useState(true);

  const fetchStats = useCallback(() => {
    if (document.hidden) return;
    fetch("/api/performance")
      .then((r) => (r.ok ? r.json() : null))
      .then(setStats)
      .catch(() => setStats(null))
      .finally(() => setIsLoading(false));
  }, []);

  useEffect(() => {
    fetchStats();
    const id = setInterval(fetchStats, 60_000);
    return () => clearInterval(id);
  }, [fetchStats]);

  if (isLoading) {
    return (
      <div className="no-trade">
        <div className="no-trade-icon">...</div>
        <div className="no-trade-title">Chargement...</div>
      </div>
    );
  }

  if (!stats || stats.total_trades === 0) {
    return (
      <div className="no-trade">
        <div className="no-trade-icon">—</div>
        {/* (D13) Personalized empty state */}
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

  const pnlColor =
    stats.total_pnl_pct > 0
      ? "var(--green)"
      : stats.total_pnl_pct < 0
        ? "var(--red)"
        : "var(--text-primary)";

  return (
    <div>
      {/* (D9) Primary KPIs — 3 prominent cards */}
      <div className="perf-kpi-row">
        <div className="perf-card-primary">
          <div
            className="perf-card-value"
            style={{
              color:
                stats.win_rate >= 55
                  ? "var(--green)"
                  : stats.win_rate < 45
                    ? "var(--red)"
                    : "var(--yellow)",
            }}
          >
            {stats.win_rate}%
          </div>
          <div className="perf-card-label">Win rate</div>
        </div>
        <div className="perf-card-primary">
          <div className="perf-card-value" style={{ color: pnlColor }}>
            {stats.total_pnl_pct > 0 ? "+" : ""}
            {stats.total_pnl_pct}%
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

      {/* Secondary stats */}
      <div className="perf-grid">
        <div className="perf-card">
          <div className="perf-card-value" style={{ color: "var(--green)" }}>
            {stats.best_trade_pnl > 0 ? "+" : ""}
            {stats.best_trade_pnl}%
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
          <div className="perf-card-value" style={{ color: "var(--green)" }}>
            {stats.wins}
          </div>
          <div className="perf-card-label">Gains</div>
        </div>
        <div className="perf-card">
          <div className="perf-card-value" style={{ color: "var(--red)" }}>
            {stats.losses}
          </div>
          <div className="perf-card-label">Pertes</div>
        </div>
        <div className="perf-card">
          <div className="perf-card-value" style={{ color: "var(--blue)" }}>
            {stats.pending}
          </div>
          <div className="perf-card-label">En cours</div>
        </div>
      </div>

      {/* Breakdown by category */}
      {Object.keys(stats.by_category).length > 0 && (
        <div style={{ marginTop: 24 }}>
          <h3
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 14,
              color: "var(--text-muted)",
              marginBottom: 12,
            }}
          >
            Par catégorie
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
              {Object.entries(stats.by_category).map(([cat, data]) => (
                <tr key={cat}>
                  <td>{cat}</td>
                  <td>{data.total}</td>
                  <td
                    style={{
                      color:
                        data.win_rate >= 55
                          ? "var(--green)"
                          : "var(--text-secondary)",
                    }}
                  >
                    {data.win_rate}%
                  </td>
                  <td
                    style={{
                      color:
                        data.pnl_sum > 0
                          ? "var(--green)"
                          : data.pnl_sum < 0
                            ? "var(--red)"
                            : "var(--text-secondary)",
                    }}
                  >
                    {data.pnl_sum > 0 ? "+" : ""}
                    {data.pnl_sum?.toFixed(2)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Breakdown by scan type */}
      {Object.keys(stats.by_scan_type).length > 0 && (
        <div style={{ marginTop: 24 }}>
          <h3
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 14,
              color: "var(--text-muted)",
              marginBottom: 12,
            }}
          >
            Par session
          </h3>
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
                  <td
                    style={{
                      color:
                        data.pnl_sum > 0
                          ? "var(--green)"
                          : data.pnl_sum < 0
                            ? "var(--red)"
                            : "var(--text-secondary)",
                    }}
                  >
                    {data.pnl_sum > 0 ? "+" : ""}
                    {data.pnl_sum?.toFixed(2)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
