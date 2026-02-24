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

// (D14) P&L color with flat for near-zero
function pnlClass(val) {
  if (val == null) return "";
  if (Math.abs(val) < 0.05) return "pnl-flat";
  return "";
}

function pnlColor(val) {
  if (val == null) return "var(--text-muted)";
  if (Math.abs(val) < 0.05) return undefined; // handled by pnl-flat class
  if (val > 0) return "var(--green)";
  if (val < 0) return "var(--red)";
  return "var(--text-muted)";
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
        {/* (D13) Personalized empty state */}
        <div className="no-trade-title">Aucun trade enregistre</div>
        <div className="no-trade-reason">
          Les trades apparaitront ici apres le premier scan.
        </div>
        <div className="no-trade-meta">
          <span className="no-trade-tag">Scans : 07:50 + 14:30 CET</span>
          <span className="no-trade-tag">Lun-Ven uniquement</span>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="trigger-section">
        {/* (D17) Export button — outline style */}
        <a href="/api/export/trades" className="trigger-btn export" style={{ textDecoration: "none" }}>
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
                      className={pnlClass(t.pnl_pct)}
                      style={{
                        color: pnlColor(t.pnl_pct),
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
