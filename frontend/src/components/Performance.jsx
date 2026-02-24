import React, { useEffect, useState } from "react";

export default function Performance() {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    fetch("/api/performance")
      .then((r) => (r.ok ? r.json() : null))
      .then(setStats)
      .catch(() => setStats(null));
  }, []);

  if (!stats || stats.total_trades === 0) {
    return (
      <div className="no-trade">
        <div className="no-trade-icon">—</div>
        <div className="no-trade-title">Pas encore de donnees</div>
        <div className="no-trade-reason">
          Les statistiques apparaitront apres la cloture des premiers trades.
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
      <div className="perf-grid">
        <div className="perf-card">
          <div className="perf-card-value" style={{ color: "var(--cyan)" }}>
            {stats.total_trades}
          </div>
          <div className="perf-card-label">Trades total</div>
        </div>
        <div className="perf-card">
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
        <div className="perf-card">
          <div className="perf-card-value" style={{ color: pnlColor }}>
            {stats.total_pnl_pct > 0 ? "+" : ""}
            {stats.total_pnl_pct}%
          </div>
          <div className="perf-card-label">P&L cumule</div>
        </div>
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
            Par categorie
          </h3>
          <table className="history-table">
            <thead>
              <tr>
                <th>Categorie</th>
                <th>Trades</th>
                <th>Win rate</th>
                <th>P&L cumule</th>
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
                <th>P&L cumule</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(stats.by_scan_type).map(([st, data]) => (
                <tr key={st}>
                  <td>{st === "europe" ? "Europe (07:50)" : "US (14:30)"}</td>
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
