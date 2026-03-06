import React, { useEffect, useState, useMemo, useCallback } from "react";
import {
  RESULT_LABELS, CATEGORY_COLORS, formatDate, formatTime, formatPnl,
  pnlColor, scoreColor, timeAgo, paginate, totalPages, PAGE_SIZE,
} from "../utils/format";

export default function Journal() {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [triggerMsg, setTriggerMsg] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);
  // (C4) Expanded journal entries
  const [expandedEntry, setExpandedEntry] = useState(null);
  // (C5) Filters
  const [filterTicker, setFilterTicker] = useState("");
  const [filterResult, setFilterResult] = useState("");
  const [filterCategory, setFilterCategory] = useState("");
  // (M2) Pagination
  const [page, setPage] = useState(1);
  // (M5) Confirmation modal
  const [showConfirm, setShowConfirm] = useState(false);
  // (O2) Export feedback
  const [exporting, setExporting] = useState(false);

  const fetchEntries = useCallback(async () => {
    if (document.hidden) return;
    try {
      const res = await fetch("/api/journal");
      if (res.ok) {
        const data = await res.json();
        setEntries(Array.isArray(data) ? data : []);
        setLastUpdate(new Date());
      }
    } catch (err) {
      console.error("Journal fetch failed:", err);
    }
  }, []);

  useEffect(() => {
    fetchEntries().finally(() => setIsLoading(false));
    const interval = setInterval(fetchEntries, 60_000);
    return () => clearInterval(interval);
  }, [fetchEntries]);

  // (M5) Trigger with confirmation
  const confirmTrigger = () => setShowConfirm(true);

  const triggerJournal = async () => {
    setShowConfirm(false);
    setLoading(true);
    setTriggerMsg(null);
    try {
      const res = await fetch("/api/journal/trigger", { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        const newEntries = Array.isArray(data) ? data : (data.entries || []);
        const diag = Array.isArray(data) ? null : data.diagnostic;
        if (newEntries.length > 0) {
          setTriggerMsg({ type: "success", text: `${newEntries.length} entrée(s) ajoutée(s) au journal.` });
        } else if (diag) {
          setTriggerMsg({ type: "warning", text: diag.message });
        } else {
          setTriggerMsg({ type: "warning", text: "Aucun trade PENDING à clôturer." });
        }
      } else {
        const err = await res.json().catch(() => ({}));
        setTriggerMsg({ type: "error", text: err.detail || `Erreur ${res.status} lors de la génération du journal.` });
      }
    } catch {
      setTriggerMsg({ type: "error", text: "Erreur réseau." });
    }
    await fetchEntries();
    setLoading(false);
  };

  // (O2) Export CSV with feedback
  const handleExport = async () => {
    setExporting(true);
    try {
      const res = await fetch("/api/export/journal");
      if (res.ok) {
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "journal.csv";
        a.click();
        URL.revokeObjectURL(url);
      }
    } catch (err) {
      console.error("Export failed:", err);
    }
    setExporting(false);
  };

  // Filter and group entries
  const validEntries = useMemo(
    () => entries.filter((e) => e && e.date),
    [entries]
  );

  // (C5) Apply filters
  const filteredEntries = useMemo(() => {
    let result = validEntries;
    if (filterTicker) {
      const q = filterTicker.toLowerCase();
      result = result.filter(
        (e) =>
          (e.ticker || "").toLowerCase().includes(q) ||
          (e.asset_name || "").toLowerCase().includes(q)
      );
    }
    if (filterResult) {
      result = result.filter((e) => e.result === filterResult);
    }
    if (filterCategory) {
      result = result.filter((e) => e.news_category === filterCategory);
    }
    return result;
  }, [validEntries, filterTicker, filterResult, filterCategory]);

  // Group by date
  const sortedDates = useMemo(() => {
    const byDate = {};
    filteredEntries.forEach((e) => {
      if (!byDate[e.date]) byDate[e.date] = [];
      byDate[e.date].push(e);
    });
    return Object.keys(byDate).sort().reverse();
  }, [filteredEntries]);

  const byDate = useMemo(() => {
    const grouped = {};
    filteredEntries.forEach((e) => {
      if (!grouped[e.date]) grouped[e.date] = [];
      grouped[e.date].push(e);
    });
    return grouped;
  }, [filteredEntries]);

  // (M2) Paginate dates
  const paginatedDates = useMemo(() => paginate(sortedDates, page), [sortedDates, page]);
  const pages = totalPages(sortedDates);

  // Reset page on filter change
  useEffect(() => setPage(1), [filterTicker, filterResult, filterCategory]);

  const hasFilters = filterTicker || filterResult || filterCategory;
  const clearFilters = () => {
    setFilterTicker("");
    setFilterResult("");
    setFilterCategory("");
  };

  const pendingCount = validEntries.filter((e) => e.result === "PENDING").length;

  // ── Skeleton loading ──
  if (isLoading) {
    return (
      <div>
        <div className="skeleton skeleton-row" />
        <div className="skeleton skeleton-card" />
        <div className="skeleton skeleton-card" style={{ height: 80 }} />
      </div>
    );
  }

  // ── Empty state ──
  if (validEntries.length === 0) {
    return (
      <div>
        <div className="trigger-section">
          <button className="trigger-btn" onClick={confirmTrigger} disabled={loading}>
            {loading ? <><span className="spinner spinner-inline" />Génération...</> : "Générer journal (22h)"}
          </button>
        </div>
        {triggerMsg && <div className={`journal-trigger-msg ${triggerMsg.type}`}>{triggerMsg.text}</div>}
        <div className="no-trade">
          <div className="no-trade-icon">--</div>
          <div className="no-trade-title">Aucune entrée de journal</div>
          <div className="no-trade-reason">
            Le journal est généré automatiquement à 22h00 CET chaque jour ouvré.
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
      {/* Actions bar */}
      <div className="trigger-section">
        <button className="trigger-btn" onClick={confirmTrigger} disabled={loading}>
          {loading ? <><span className="spinner spinner-inline" />Génération...</> : "Générer journal (22h)"}
        </button>
        <button className="trigger-btn export" onClick={handleExport} disabled={exporting}>
          {exporting ? <><span className="spinner spinner-inline" />Export...</> : "Export CSV"}
        </button>
        {lastUpdate && <span className="last-update" style={{ marginLeft: "auto" }}>MAJ {timeAgo(lastUpdate)}</span>}
      </div>

      {triggerMsg && <div className={`journal-trigger-msg ${triggerMsg.type}`}>{triggerMsg.text}</div>}

      {/* (C5) Filters */}
      <div className="filters-bar">
        <input
          className="filter-input"
          placeholder="Ticker / actif..."
          value={filterTicker}
          onChange={(e) => setFilterTicker(e.target.value)}
        />
        <select className="filter-select" value={filterResult} onChange={(e) => setFilterResult(e.target.value)}>
          <option value="">Tous résultats</option>
          <option value="TP_HIT">TP</option>
          <option value="SL_HIT">SL</option>
          <option value="EXPIRED">Expiré</option>
          <option value="PENDING">Pending</option>
        </select>
        <select className="filter-select" value={filterCategory} onChange={(e) => setFilterCategory(e.target.value)}>
          <option value="">Toutes catégories</option>
          {Object.keys(CATEGORY_COLORS).map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        {hasFilters && (
          <button className="filter-clear" onClick={clearFilters}>Effacer</button>
        )}
        <span className="last-update">{filteredEntries.length} entrée{filteredEntries.length !== 1 ? "s" : ""}</span>
      </div>

      {/* (C4) Compact/expandable journal entries */}
      <div className="journal-desktop-view">
        {paginatedDates.map((date) => (
          <div key={date} className="journal-day">
            <div className="journal-date-header">{formatDate(date + "T00:00:00")}</div>

            {(byDate[date] || []).map((e) => {
              const key = `${e.entry_time}-${e.ticker}`;
              const r = RESULT_LABELS[e.result] || RESULT_LABELS.PENDING;
              const isExpanded = expandedEntry === key;
              const catColor = CATEGORY_COLORS[e.news_category] || CATEGORY_COLORS.other;

              return (
                <React.Fragment key={key}>
                  {/* Compact row — always visible */}
                  <div
                    className={`journal-compact-row ${isExpanded ? "expanded" : ""}`}
                    onClick={() => setExpandedEntry(isExpanded ? null : key)}
                  >
                    <span className="journal-compact-time">{formatTime(e.entry_time)}</span>
                    <span className="journal-compact-ticker">{e.ticker || "--"}</span>
                    <span
                      className={`direction-badge sm ${e.direction === "LONG" ? "long" : "short"}`}
                    >
                      {e.direction || "--"}
                    </span>
                    <span
                      className="cat-badge"
                      style={{
                        background: catColor + "22",
                        color: catColor,
                      }}
                    >
                      {e.news_category || "other"}
                    </span>
                    <span style={{ color: scoreColor(e.score), fontWeight: 700, minWidth: 30, textAlign: "center" }}>
                      {e.score ?? "--"}
                    </span>
                    <span className={`result-badge sm ${r.cls}`}>{r.label}</span>
                    <span className="journal-compact-spacer" />
                    <span className="journal-compact-pnl" style={{ color: pnlColor(e.pnl_pct) }}>
                      {formatPnl(e.pnl_pct)}
                    </span>
                    <span className="journal-compact-chevron">{isExpanded ? "\u25B2" : "\u25BC"}</span>
                  </div>

                  {/* Expanded detail */}
                  {isExpanded && (
                    <div className="journal-detail">
                      {/* News */}
                      {e.news_title && (
                        <div className="journal-detail-news">
                          <div className="journal-detail-news-title">{e.news_title}</div>
                          {e.news_source && <div className="journal-detail-news-source">{e.news_source}</div>}
                        </div>
                      )}

                      {/* Metrics grid */}
                      <div className="journal-detail-grid">
                        <div className="journal-detail-item">
                          <div className="journal-detail-label">Actif</div>
                          <div className="journal-detail-value">{e.asset_name || "--"}</div>
                        </div>
                        <div className="journal-detail-item">
                          <div className="journal-detail-label">Entrée</div>
                          <div className="journal-detail-value" style={{ color: "var(--cyan)" }}>
                            {e.entry_price ?? "--"} <span style={{ color: "var(--text-muted)", fontSize: 10 }}>({formatTime(e.entry_time)})</span>
                          </div>
                        </div>
                        <div className="journal-detail-item">
                          <div className="journal-detail-label">Sortie</div>
                          <div className="journal-detail-value">
                            {e.exit_price ?? "--"} <span style={{ color: "var(--text-muted)", fontSize: 10 }}>({formatTime(e.exit_time)})</span>
                          </div>
                        </div>
                        <div className="journal-detail-item">
                          <div className="journal-detail-label">High</div>
                          <div className="journal-detail-value" style={{ color: "var(--green)" }}>
                            {e.day_high ?? "--"}
                          </div>
                        </div>
                        <div className="journal-detail-item">
                          <div className="journal-detail-label">Low</div>
                          <div className="journal-detail-value" style={{ color: "var(--red)" }}>
                            {e.day_low ?? "--"}
                          </div>
                        </div>
                        <div className="journal-detail-item">
                          <div className="journal-detail-label">Catégorie actif</div>
                          <div className="journal-detail-value">{e.asset_category || "--"}</div>
                        </div>
                      </div>

                      {/* Reasoning */}
                      {e.reasoning && (
                        <div className="journal-detail-item" style={{ marginBottom: 12 }}>
                          <div className="journal-detail-label">Analyse</div>
                          <div className="journal-detail-value" style={{ fontSize: 12, lineHeight: 1.5, color: "var(--text-secondary)" }}>
                            {e.reasoning}
                          </div>
                        </div>
                      )}

                      {/* Review */}
                      {e.review && (
                        <div className="journal-detail-review">{e.review}</div>
                      )}

                      {/* Binary event warning */}
                      {e.binary_event_warning && (
                        <div className="trade-warning" style={{ marginTop: 8 }}>
                          {e.binary_event_warning}
                        </div>
                      )}
                    </div>
                  )}
                </React.Fragment>
              );
            })}
          </div>
        ))}
      </div>

      {/* Mobile view — card layout */}
      <div className="journal-mobile-list">
        {paginatedDates.map((date) => (
          <div key={date} className="journal-day">
            <div className="journal-date-header">{formatDate(date + "T00:00:00")}</div>
            {(byDate[date] || []).map((e) => {
              const r = RESULT_LABELS[e.result] || RESULT_LABELS.PENDING;
              return (
                <div key={`m-${e.entry_time}-${e.ticker}`} className="trade-mobile-card">
                  <div className="trade-mobile-card-header">
                    <div>
                      <div style={{ fontWeight: 600, color: "var(--text-primary)" }}>{e.asset_name || "--"}</div>
                      <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{e.ticker || "--"}</div>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <span className={`direction-badge sm ${e.direction === "LONG" ? "long" : "short"}`}>
                        {e.direction || "--"}
                      </span>
                      <span className={`result-badge sm ${r.cls}`}>{r.label}</span>
                    </div>
                  </div>
                  <div className="trade-mobile-card-body">
                    <div>
                      <div className="trade-mobile-label">Entrée</div>
                      <div style={{ color: "var(--cyan)" }}>{e.entry_price ?? "--"}</div>
                    </div>
                    <div>
                      <div className="trade-mobile-label">Sortie</div>
                      <div>{e.exit_price ?? "--"}</div>
                    </div>
                    <div>
                      <div className="trade-mobile-label">P&L</div>
                      <div style={{ color: pnlColor(e.pnl_pct), fontWeight: 600 }}>{formatPnl(e.pnl_pct)}</div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        ))}
      </div>

      {/* (M2) Pagination */}
      {pages > 1 && (
        <div className="pagination">
          <button className="pagination-btn" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            &larr; Préc.
          </button>
          <span className="pagination-info">{page} / {pages}</span>
          <button className="pagination-btn" disabled={page >= pages} onClick={() => setPage(page + 1)}>
            Suiv. &rarr;
          </button>
        </div>
      )}

      {/* (M5) Confirmation modal */}
      {showConfirm && (
        <div className="modal-overlay" onClick={() => setShowConfirm(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-title">Générer le journal</div>
            <div className="modal-body">
              {pendingCount > 0
                ? `${pendingCount} trade${pendingCount > 1 ? "s" : ""} PENDING ser${pendingCount > 1 ? "ont" : "a"} clôturé${pendingCount > 1 ? "s" : ""}. Continuer ?`
                : "Aucun trade PENDING détecté. Lancer quand même ?"
              }
            </div>
            <div className="modal-actions">
              <button className="trigger-btn export" onClick={() => setShowConfirm(false)}>Annuler</button>
              <button className="trigger-btn" onClick={triggerJournal}>Confirmer</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
