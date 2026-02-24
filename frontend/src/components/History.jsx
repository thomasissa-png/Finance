import React, { useEffect, useState } from "react";

const RESULT_LABELS = {
  TP_HIT: { label: "TP", cls: "tp" },
  SL_HIT: { label: "SL", cls: "sl" },
  EXPIRED: { label: "EXP", cls: "expired" },
  PENDING: { label: "...", cls: "pending" },
};

function formatDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("fr-FR", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function History() {
  const [trades, setTrades] = useState([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    fetch("/api/trades")
      .then((r) => (r.ok ? r.json() : []))
      .then(setTrades)
      .catch(() => setTrades([]))
      .finally(() => setIsLoading(false));
  }, []);

  if (isLoading) {
    return (
      <div className="no-trade">
        <div className="no-trade-icon">...</div>
        <div className="no-trade-title">Chargement...</div>
      </div>
    );
  }

  if (trades.length === 0) {
    return (
      <div className="no-trade">
        <div className="no-trade-icon">—</div>
        <div className="no-trade-title">Aucun trade enregistre</div>
        <div className="no-trade-reason">
          Les trades apparaitront ici apres le premier scan.
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="trigger-section">
        <a href="/api/export/trades" className="trigger-btn" style={{ textDecoration: "none" }}>
          Export CSV
        </a>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="history-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Scan</th>
              <th>Actif</th>
              <th>Dir.</th>
              <th>Entree</th>
              <th>Objectif</th>
              <th>Stop</th>
              <th>R/R</th>
              <th>Conf.</th>
              <th>Resultat</th>
              <th>P&L</th>
            </tr>
          </thead>
          <tbody>
            {trades
              .slice()
              .reverse()
              .map((t) => {
                const r = RESULT_LABELS[t.result] || RESULT_LABELS.PENDING;
                return (
                  <tr key={`${t.timestamp}-${t.ticker}`}>
                    <td>{formatDate(t.timestamp)}</td>
                    <td>{t.scan_type === "europe" ? "EU" : "US"}</td>
                    <td>
                      <div>
                        {t.asset_name}{" "}
                        <span style={{ color: "var(--text-muted)" }}>
                          {t.ticker}
                        </span>
                      </div>
                      {t.binary_event_warning && (
                        <div className="trade-warning-inline" style={{ fontSize: 10 }}>
                          Evt. binaire
                        </div>
                      )}
                    </td>
                    <td>
                      <span
                        className={`direction-badge ${t.direction === "LONG" ? "long" : "short"}`}
                        style={{ fontSize: 11, padding: "2px 6px" }}
                      >
                        {t.direction}
                      </span>
                    </td>
                    <td>{t.entry_price}</td>
                    <td style={{ color: "var(--green)" }}>{t.target_price}</td>
                    <td style={{ color: "var(--red)" }}>{t.stop_price}</td>
                    <td style={{ color: "var(--cyan)" }}>{t.risk_reward}</td>
                    <td>{t.confidence}%</td>
                    <td>
                      <span className={`result-badge ${r.cls}`}>{r.label}</span>
                    </td>
                    <td
                      style={{
                        color:
                          t.pnl_pct > 0
                            ? "var(--green)"
                            : t.pnl_pct < 0
                              ? "var(--red)"
                              : "var(--text-muted)",
                        fontWeight: 600,
                      }}
                    >
                      {t.pnl_pct != null ? `${t.pnl_pct > 0 ? "+" : ""}${t.pnl_pct}%` : "—"}
                    </td>
                  </tr>
                );
              })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
