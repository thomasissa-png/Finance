import React, { useState, useEffect, useCallback } from "react";

const LEVEL_ICONS = { INFO: "i", WARN: "!", ERROR: "x", DECISION: ">" };

function CollectedNewsList({ items }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="collected-news-list" style={{ marginTop: 8, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 6 }}>
        {items.length} news collectées :
      </div>
      {items.map((n, i) => (
        <div key={i} style={{ fontSize: 11, padding: "3px 0", color: "var(--text-secondary)", display: "flex", gap: 8 }}>
          <span style={{ color: "var(--text-muted)", minWidth: 80, flexShrink: 0 }}>{n.source}</span>
          <span>{n.title}</span>
        </div>
      ))}
    </div>
  );
}

function HealthBar({ rate }) {
  const pct = Math.max(0, Math.min(100, (rate || 0) * 100));
  const color = pct >= 80 ? "var(--green)" : pct >= 50 ? "var(--yellow)" : "var(--red)";
  return (
    <div className="health-bar">
      <div className="health-bar-fill" style={{ width: `${pct}%`, backgroundColor: color }} />
    </div>
  );
}

export default function NewsPage({ isActive }) {
  const [sourceHealth, setSourceHealth] = useState(null);
  const [weeklyReview, setWeeklyReview] = useState(null);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState(null);
  const [expandedLog, setExpandedLog] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const [shRes, wrRes, logRes] = await Promise.all([
        fetch("/api/source-health?days=7").then((r) => { if (!r.ok) throw new Error(`Sources: ${r.status}`); return r.json(); }).catch(() => null),
        fetch("/api/source-health/weekly").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch(`/api/agents/news/logs?limit=50${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`)
          .then((r) => r.ok ? r.json() : []).catch(() => []),
      ]);
      setSourceHealth(shRes);
      setWeeklyReview(wrRes);
      setLogs(Array.isArray(logRes) ? logRes : []);
      setFetchError(null);
    } catch (err) {
      setFetchError(err.message || "Erreur de chargement");
    } finally {
      setLoading(false);
    }
  }, [logFilter]);

  useEffect(() => {
    if (isActive) fetchData();
    const id = setInterval(fetchData, 60_000);
    return () => clearInterval(id);
  }, [isActive, fetchData]);

  const sources = [];
  if (sourceHealth) {
    const dayData = sourceHealth.current_day || sourceHealth;
    if (typeof dayData === "object") {
      Object.entries(dayData).forEach(([name, data]) => {
        if (typeof data === "object" && name !== "current_day" && name !== "historical") {
          sources.push({
            name,
            success_rate: data.success_rate ?? data.rate ?? null,
            failures: data.failures ?? data.failure_count ?? 0,
            total: data.total ?? data.success_count ?? 0,
            last_error: data.last_error || null,
            avg_latency: data.avg_latency_ms ?? data.avg_latency ?? null,
          });
        }
      });
    }
  }

  return (
    <div className="agent-page">
      <div className="page-header">
        <div className="page-title">Agent News</div>
        <div className="page-subtitle">Collecte, curation, santé des sources, détection d'événements</div>
      </div>

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement des données...</div>}
      {fetchError && <div className="agent-error-banner">Erreur : {fetchError}</div>}

      {/* Source Health */}
      <div className="section-card">
        <h3>Santé des sources</h3>
        {sources.length === 0 ? (
          <div className="agent-logs-empty">Aucune donnée de santé disponible. Les données apparaissent après le premier scan.</div>
        ) : (
          <>
          <div className="compact-table desktop-only">
            <table>
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Taux de succès</th>
                  <th>Sante</th>
                  <th>Échecs</th>
                  <th>Latence moy.</th>
                  <th>Dernière erreur</th>
                </tr>
              </thead>
              <tbody>
                {sources.sort((a, b) => (a.success_rate ?? 1) - (b.success_rate ?? 1)).map((s) => (
                  <tr key={s.name}>
                    <td className="ticker-cell">{s.name}</td>
                    <td>{s.success_rate != null ? `${(s.success_rate * 100).toFixed(0)}%` : "—"}</td>
                    <td style={{ width: 120 }}><HealthBar rate={s.success_rate} /></td>
                    <td style={{ color: s.failures > 0 ? "var(--red)" : "var(--text-muted)" }}>{s.failures}</td>
                    <td>{s.avg_latency != null ? `${Math.round(s.avg_latency)}ms` : "—"}</td>
                    <td className="error-cell">{s.last_error ? String(s.last_error).slice(0, 60) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mobile-only">
            {sources.sort((a, b) => (a.success_rate ?? 1) - (b.success_rate ?? 1)).map((s) => (
              <div key={s.name} className="mobile-card">
                <div className="mobile-card-header">
                  <span className="ticker-cell">{s.name}</span>
                  <span style={{ color: (s.success_rate ?? 1) >= 0.8 ? "var(--green)" : (s.success_rate ?? 1) >= 0.5 ? "var(--yellow)" : "var(--red)" }}>
                    {s.success_rate != null ? `${(s.success_rate * 100).toFixed(0)}%` : "—"}
                  </span>
                </div>
                <HealthBar rate={s.success_rate} />
                <div className="mobile-card-body">
                  <span>Échecs : <strong style={{ color: s.failures > 0 ? "var(--red)" : "var(--text-muted)" }}>{s.failures}</strong></span>
                  <span>Latence : {s.avg_latency != null ? `${Math.round(s.avg_latency)}ms` : "—"}</span>
                </div>
                {s.last_error && <div className="mobile-card-error">{String(s.last_error).slice(0, 80)}</div>}
              </div>
            ))}
          </div>
          </>
        )}
      </div>

      {/* Weekly review */}
      {weeklyReview && (
        <div className="section-card">
          <h3>Revue hebdomadaire</h3>
          <div className="weekly-review">
            {weeklyReview.summary && (
              <div className="weekly-summary">
                <div className="weekly-stat">
                  <span className="weekly-stat-value" style={{ color: "var(--green)" }}>
                    {weeklyReview.summary.healthy ?? "—"}
                  </span>
                  <span className="weekly-stat-label">Sources saines</span>
                </div>
                <div className="weekly-stat">
                  <span className="weekly-stat-value" style={{ color: "var(--red)" }}>
                    {weeklyReview.summary.degraded ?? "—"}
                  </span>
                  <span className="weekly-stat-label">Sources dégradées</span>
                </div>
              </div>
            )}
            {weeklyReview.suggestions && weeklyReview.suggestions.length > 0 && (
              <div className="weekly-suggestions">
                <h4>Suggestions</h4>
                <ul>
                  {weeklyReview.suggestions.map((s, i) => (
                    <li key={i}>{typeof s === "string" ? s : JSON.stringify(s)}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Agent logs */}
      <div className="section-card">
        <div className="section-header">
          <h3>Logs Agent News</h3>
          <div className="log-filter-row">
            {["ALL", "DECISION", "INFO", "WARN", "ERROR"].map((level) => (
              <button key={level} className={`log-filter-btn ${logFilter === level ? "active" : ""}`}
                onClick={() => setLogFilter(level)}>{level}</button>
            ))}
          </div>
        </div>
        <div className="agent-logs compact-logs">
          {logs.length === 0 ? (
            <div className="agent-logs-empty">Aucun log</div>
          ) : logs.slice(0, 20).map((log, i) => {
            const hasNewsItems = log.details?.news_items && Array.isArray(log.details.news_items) && log.details.news_items.length > 0;
            const isExpanded = expandedLog === i;
            const isClickable = hasNewsItems;
            return (
              <div
                key={`${log.timestamp}-${i}`}
                className={`agent-log-entry ${(log.level || "info").toLowerCase()}`}
                onClick={isClickable ? () => setExpandedLog(isExpanded ? null : i) : undefined}
                style={isClickable ? { cursor: "pointer" } : undefined}
              >
                <div className="agent-log-header">
                  <span className="agent-log-icon">{LEVEL_ICONS[log.level] || "i"}</span>
                  <span className="agent-log-action" style={{ color: log.level === "ERROR" ? "var(--red)" : log.level === "WARN" ? "var(--yellow)" : log.level === "DECISION" ? "var(--accent)" : "var(--text-secondary)" }}>
                    {log.action}
                  </span>
                  <span className="agent-log-time">
                    {log.timestamp ? new Date(log.timestamp).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : ""}
                  </span>
                  {log.duration_ms != null && <span className="agent-log-duration">{log.duration_ms}ms</span>}
                  {hasNewsItems && <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 6 }}>{isExpanded ? "▲" : "▼"}</span>}
                </div>
                {log.details && Object.keys(log.details).length > 0 && (
                  <div className="agent-log-details">
                    {Object.entries(log.details).filter(([k]) => k !== "news_items").slice(0, 4).map(([k, v]) => (
                      <span key={k} className="agent-log-detail">
                        <span className="agent-log-detail-key">{k}:</span>{" "}
                        {typeof v === "object" ? JSON.stringify(v).slice(0, 80) : String(v).slice(0, 80)}
                      </span>
                    ))}
                  </div>
                )}
                {isExpanded && hasNewsItems && <CollectedNewsList items={log.details.news_items} />}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
