import React, { useEffect, useState, useMemo, useCallback } from "react";

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

// (D14) P&L color with flat for near-zero
function pnlColor(val) {
  if (val == null) return "var(--text-muted)";
  if (Math.abs(val) < 0.05) return "var(--text-muted)";
  if (val > 0) return "var(--green)";
  if (val < 0) return "var(--red)";
  return "var(--text-muted)";
}

export default function Journal() {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [triggerMsg, setTriggerMsg] = useState(null);

  const fetchEntries = useCallback(async () => {
    // Skip polling when tab is not visible
    if (document.hidden) return;
    try {
      const res = await fetch("/api/journal");
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data)) {
          setEntries(data);
        }
      }
    } catch {
      /* backend not yet started — silent */
    }
  }, []);

  // Initial load + periodic polling (entries may change after 22h journal)
  useEffect(() => {
    fetchEntries().finally(() => setIsLoading(false));
    const interval = setInterval(fetchEntries, 60_000);
    return () => clearInterval(interval);
  }, [fetchEntries]);

  const triggerJournal = async () => {
    setLoading(true);
    setTriggerMsg(null);
    try {
      const res = await fetch("/api/journal/trigger", { method: "POST" });
      if (res.ok) {
        const newEntries = await res.json();
        if (Array.isArray(newEntries) && newEntries.length > 0) {
          setTriggerMsg({ type: "success", text: `${newEntries.length} entree(s) ajoutee(s) au journal.` });
        } else {
          setTriggerMsg({ type: "warning", text: "Aucun trade PENDING a cloturer." });
        }
      } else {
        const err = await res.json().catch(() => ({}));
        setTriggerMsg({ type: "error", text: err.detail || "Erreur lors de la generation du journal." });
      }
    } catch (err) {
      console.error("Journal trigger failed:", err);
      setTriggerMsg({ type: "error", text: "Erreur reseau." });
    }
    // Always refetch entries after trigger, regardless of outcome
    await fetchEntries();
    setLoading(false);
  };

  // (F6) Memoize grouping and sorting to avoid re-computing on every render
  // Filter out entries with missing date field (defensive)
  const validEntries = useMemo(
    () => entries.filter((e) => e && e.date),
    [entries]
  );

  const sortedDates = useMemo(() => {
    const byDate = {};
    validEntries.forEach((e) => {
      if (!byDate[e.date]) byDate[e.date] = [];
      byDate[e.date].push(e);
    });
    return Object.keys(byDate).sort().reverse();
  }, [validEntries]);

  const byDate = useMemo(() => {
    const grouped = {};
    validEntries.forEach((e) => {
      if (!grouped[e.date]) grouped[e.date] = [];
      grouped[e.date].push(e);
    });
    return grouped;
  }, [validEntries]);

  if (isLoading) {
    return (
      <div className="no-trade">
        <div className="no-trade-icon">...</div>
        <div className="no-trade-title">Chargement...</div>
      </div>
    );
  }

  if (validEntries.length === 0) {
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
        {triggerMsg && (
          <div className={`journal-trigger-msg ${triggerMsg.type}`}>
            {triggerMsg.text}
          </div>
        )}
        <div className="no-trade">
          <div className="no-trade-icon">--</div>
          {/* (D13) Personalized empty state */}
          <div className="no-trade-title">Aucune entree de journal</div>
          <div className="no-trade-reason">
            Le journal est genere automatiquement a 22h00 CET chaque jour ouvre.
          </div>
          <div className="no-trade-meta">
            <span className="no-trade-tag">Auto : 22h00 CET</span>
            <span className="no-trade-tag">Lun-Ven</span>
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
        {/* (D17) Export button — outline style */}
        <a href="/api/export/journal" className="trigger-btn export" style={{ textDecoration: "none" }}>
          Export CSV
        </a>
      </div>

      {triggerMsg && (
        <div className={`journal-trigger-msg ${triggerMsg.type}`}>
          {triggerMsg.text}
        </div>
      )}

      {sortedDates.map((date) => (
        <div key={date} className="journal-day">
          <div className="journal-date-header">{formatDate(date + "T00:00:00")}</div>

          {/* Desktop table (D8) */}
          <div className="journal-desktop-table" style={{ overflowX: "auto" }}>
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
                  <th>High/Low</th>
                  <th>Resultat</th>
                  <th>Bilan</th>
                </tr>
              </thead>
              <tbody>
                {(byDate[date] || []).map((e) => {
                  const r = RESULT_LABELS[e.result] || RESULT_LABELS.PENDING;
                  return (
                    <tr key={`${e.entry_time}-${e.ticker}`}>
                      <td className="journal-news-cell">
                        <div className="journal-news-title">{e.news_title || "--"}</div>
                        <div className="journal-news-source">{e.news_source || "--"}</div>
                        {e.news_category && e.news_category !== "other" && (
                          <div className="journal-cat-badge">{e.news_category}</div>
                        )}
                      </td>
                      <td className="journal-reasoning-cell">{e.reasoning || "--"}</td>
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
                          {e.score ?? "--"}
                        </span>
                      </td>
                      <td>
                        <div>{e.asset_name || "--"}</div>
                        <div style={{ color: "var(--text-muted)", fontSize: 11 }}>
                          {e.ticker || "--"}
                        </div>
                        {e.asset_category && (
                          <div className="journal-cat-badge">{e.asset_category}</div>
                        )}
                      </td>
                      <td>
                        <span
                          className={`direction-badge ${e.direction === "LONG" ? "long" : "short"}`}
                          style={{ fontSize: 11, padding: "2px 6px" }}
                        >
                          {e.direction || "--"}
                        </span>
                      </td>
                      <td>
                        <div style={{ color: "var(--cyan)" }}>{e.entry_price ?? "--"}</div>
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
                            className={Math.abs(e.pnl_pct) < 0.05 ? "pnl-flat" : ""}
                            style={{
                              marginTop: 4,
                              fontWeight: 600,
                              fontSize: 12,
                              color: pnlColor(e.pnl_pct),
                            }}
                          >
                            {e.pnl_pct > 0 ? "+" : ""}
                            {e.pnl_pct}%
                          </div>
                        )}
                      </td>
                      <td className="journal-review-cell">
                        {e.review || "--"}
                        {e.binary_event_warning && (
                          <div className="trade-warning-inline">
                            {e.binary_event_warning}
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* (D8) Mobile card layout — shown only on small screens */}
          <div className="journal-mobile-list">
            {(byDate[date] || []).map((e) => {
              const r = RESULT_LABELS[e.result] || RESULT_LABELS.PENDING;
              return (
                <div key={`m-${e.entry_time}-${e.ticker}`} className="journal-mobile-card">
                  <div className="journal-mobile-card-header">
                    <div>
                      <div style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                        {e.asset_name || "--"}
                      </div>
                      <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                        {e.ticker || "--"}
                        {e.news_category && e.news_category !== "other" && (
                          <span className="journal-cat-badge" style={{ marginLeft: 6 }}>
                            {e.news_category}
                          </span>
                        )}
                      </div>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span
                        className={`direction-badge ${e.direction === "LONG" ? "long" : "short"}`}
                        style={{ fontSize: 10, padding: "2px 6px" }}
                      >
                        {e.direction || "--"}
                      </span>
                      <span className={`result-badge ${r.cls}`}>{r.label}</span>
                    </div>
                  </div>
                  <div className="journal-mobile-card-body">
                    <div>
                      <div className="journal-mobile-label">Entree</div>
                      <div style={{ color: "var(--cyan)" }}>{e.entry_price ?? "--"}</div>
                    </div>
                    <div>
                      <div className="journal-mobile-label">Sortie</div>
                      <div>{e.exit_price ?? "--"}</div>
                    </div>
                    <div>
                      <div className="journal-mobile-label">Score</div>
                      <div style={{
                        color: e.score >= 70 ? "var(--green)" : e.score >= 50 ? "var(--yellow)" : "var(--red)",
                        fontWeight: 700,
                      }}>
                        {e.score ?? "--"}
                      </div>
                    </div>
                    <div>
                      <div className="journal-mobile-label">P&L</div>
                      <div style={{
                        color: pnlColor(e.pnl_pct),
                        fontWeight: 600,
                      }}>
                        {e.pnl_pct != null ? `${e.pnl_pct > 0 ? "+" : ""}${e.pnl_pct}%` : "--"}
                      </div>
                    </div>
                    {(e.review || e.news_title) && (
                      <div className="journal-mobile-review">
                        {e.news_title && (
                          <div style={{ color: "var(--text-secondary)", marginBottom: 4 }}>
                            {e.news_title}
                          </div>
                        )}
                        {e.review || "--"}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
