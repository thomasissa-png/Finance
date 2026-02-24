import React, { useEffect, useState } from "react";

const RESULT_LABELS = {
  TP_HIT: { label: "TP", cls: "tp" },
  SL_HIT: { label: "SL", cls: "sl" },
  EXPIRED: { label: "EXP", cls: "expired" },
  PENDING: { label: "...", cls: "pending" },
};

function formatTime(iso) {
  if (!iso) return "--";
  return new Date(iso).toLocaleString("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDate(iso) {
  if (!iso) return "--";
  return new Date(iso).toLocaleDateString("fr-FR", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export default function Journal() {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetch("/api/journal")
      .then((r) => (r.ok ? r.json() : []))
      .then(setEntries)
      .catch(() => setEntries([]));
  }, []);

  const triggerJournal = async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/journal/trigger", { method: "POST" });
      if (res.ok) {
        // Reload entries
        const all = await fetch("/api/journal");
        if (all.ok) setEntries(await all.json());
      }
    } catch (err) {
      console.error("Journal trigger failed:", err);
    } finally {
      setLoading(false);
    }
  };

  // Group entries by date
  const byDate = {};
  entries.forEach((e) => {
    if (!byDate[e.date]) byDate[e.date] = [];
    byDate[e.date].push(e);
  });
  const sortedDates = Object.keys(byDate).sort().reverse();

  if (entries.length === 0) {
    return (
      <div>
        <div className="trigger-section">
          <button
            className="trigger-btn"
            onClick={triggerJournal}
            disabled={loading}
          >
            {loading ? "Generation..." : "Generer journal (22h)"}
          </button>
        </div>
        <div className="no-trade">
          <div className="no-trade-icon">--</div>
          <div className="no-trade-title">Aucune entree de journal</div>
          <div className="no-trade-reason">
            Le journal est genere automatiquement a 22h00 CET chaque jour.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="trigger-section">
        <button
          className="trigger-btn"
          onClick={triggerJournal}
          disabled={loading}
        >
          {loading ? "Generation..." : "Generer journal (22h)"}
        </button>
      </div>

      {sortedDates.map((date) => (
        <div key={date} className="journal-day">
          <div className="journal-date-header">{formatDate(date + "T00:00:00")}</div>

          <div style={{ overflowX: "auto" }}>
            <table className="journal-table">
              <thead>
                <tr>
                  <th>News</th>
                  <th>Analyse</th>
                  <th>Score</th>
                  <th>Actif</th>
                  <th>Dir.</th>
                  <th>Entree</th>
                  <th>Sortie</th>
                  <th>High du jour</th>
                  <th>Resultat</th>
                  <th>Bilan</th>
                </tr>
              </thead>
              <tbody>
                {byDate[date].map((e, i) => {
                  const r = RESULT_LABELS[e.result] || RESULT_LABELS.PENDING;
                  return (
                    <tr key={i}>
                      <td className="journal-news-cell">
                        <div className="journal-news-title">{e.news_title}</div>
                        <div className="journal-news-source">{e.news_source}</div>
                      </td>
                      <td className="journal-reasoning-cell">{e.reasoning}</td>
                      <td className="journal-score">
                        <span
                          style={{
                            color:
                              e.score >= 70
                                ? "var(--green)"
                                : e.score >= 50
                                  ? "var(--yellow)"
                                  : "var(--red)",
                          }}
                        >
                          {e.score}
                        </span>
                      </td>
                      <td>
                        <div>{e.asset_name}</div>
                        <div style={{ color: "var(--text-muted)", fontSize: 11 }}>
                          {e.ticker}
                        </div>
                      </td>
                      <td>
                        <span
                          className={`direction-badge ${e.direction === "LONG" ? "long" : "short"}`}
                          style={{ fontSize: 11, padding: "2px 6px" }}
                        >
                          {e.direction}
                        </span>
                      </td>
                      <td>
                        <div style={{ color: "var(--cyan)" }}>{e.entry_price}</div>
                        <div style={{ color: "var(--text-muted)", fontSize: 11 }}>
                          {formatTime(e.entry_time)}
                        </div>
                      </td>
                      <td>
                        <div>{e.exit_price ?? "--"}</div>
                        <div style={{ color: "var(--text-muted)", fontSize: 11 }}>
                          {formatTime(e.exit_time)}
                        </div>
                      </td>
                      <td>
                        <div style={{ color: "var(--green)" }}>
                          H: {e.day_high ?? "--"}
                        </div>
                        <div style={{ color: "var(--red)", fontSize: 11 }}>
                          L: {e.day_low ?? "--"}
                        </div>
                      </td>
                      <td>
                        <span className={`result-badge ${r.cls}`}>{r.label}</span>
                        {e.pnl_pct != null && (
                          <div
                            style={{
                              marginTop: 4,
                              fontWeight: 600,
                              fontSize: 12,
                              color:
                                e.pnl_pct > 0
                                  ? "var(--green)"
                                  : e.pnl_pct < 0
                                    ? "var(--red)"
                                    : "var(--text-muted)",
                            }}
                          >
                            {e.pnl_pct > 0 ? "+" : ""}
                            {e.pnl_pct}%
                          </div>
                        )}
                      </td>
                      <td className="journal-review-cell">{e.review}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}
