import React, { useState, useEffect, useCallback } from "react";
import { POLL_NORMAL } from "../utils/constants";
import { apiFetch } from "../utils/api";
import { ErrorBanner, LastUpdated, LogSection } from "./shared";

function CollectedNewsList({ items }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="collected-news-list" style={{ marginTop: 8, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 6 }}>
        {items.length} news collectées :
      </div>
      {items.map((n, i) => (
        <div key={i} style={{ fontSize: 11, padding: "4px 0", color: "var(--text-secondary)", display: "flex", gap: 8, alignItems: "baseline", borderBottom: "1px solid var(--border-light)" }}>
          <span style={{ color: "var(--accent)", minWidth: 70, flexShrink: 0, fontFamily: "var(--font-mono)", fontSize: 10 }}>{n.source}</span>
          <span style={{ flex: 1 }}>{n.title}</span>
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
  const [error, setError] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [expandedLog, setExpandedLog] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const [shRes, wrRes, logRes] = await Promise.all([
        apiFetch("/api/source-health?days=7", {}, null),
        apiFetch("/api/source-health/weekly", {}, null),
        apiFetch(`/api/agents/news/logs?limit=50${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`, {}, []),
      ]);
      setSourceHealth(shRes);
      setWeeklyReview(wrRes);
      setLogs(Array.isArray(logRes) ? logRes : []);
      setError(null);
      setLastUpdate(new Date());
    } catch (err) {
      setError(err.message || "Erreur de chargement");
    } finally {
      setLoading(false);
    }
  }, [logFilter]);

  useEffect(() => {
    if (isActive) fetchData();
    const id = setInterval(fetchData, POLL_NORMAL);
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

  // NewsPage has custom log rendering (expandable news items), so we keep it inline
  const filteredLogs = logFilter === "ALL" ? logs : logs.filter((l) => l.level === logFilter);

  return (
    <div className="agent-page">
      <div className="page-header">
        <div className="page-title">Agent News</div>
        <div className="page-subtitle">Collecte, curation, santé des sources, détection d'événements</div>
        <LastUpdated date={lastUpdate} />
      </div>

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement des données...</div>}
      <ErrorBanner error={error} onRetry={fetchData} />

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
                  <th>&Eacute;checs</th>
                  <th>Latence moy.</th>
                  <th>Dernière erreur</th>
                </tr>
              </thead>
              <tbody>
                {sources.sort((a, b) => (a.success_rate ?? 1) - (b.success_rate ?? 1)).map((s) => (
                  <tr key={s.name}>
                    <td className="ticker-cell">{s.name}</td>
                    <td>{s.success_rate != null ? `${(s.success_rate * 100).toFixed(0)}%` : "\u2014"}</td>
                    <td style={{ width: 120 }}><HealthBar rate={s.success_rate} /></td>
                    <td style={{ color: s.failures > 0 ? "var(--red)" : "var(--text-muted)" }}>{s.failures}</td>
                    <td>{s.avg_latency != null ? `${Math.round(s.avg_latency)}ms` : "\u2014"}</td>
                    <td className="error-cell">{s.last_error ? String(s.last_error).slice(0, 60) : "\u2014"}</td>
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
                    {s.success_rate != null ? `${(s.success_rate * 100).toFixed(0)}%` : "\u2014"}
                  </span>
                </div>
                <HealthBar rate={s.success_rate} />
                <div className="mobile-card-body">
                  <span>&Eacute;checs : <strong style={{ color: s.failures > 0 ? "var(--red)" : "var(--text-muted)" }}>{s.failures}</strong></span>
                  <span>Latence : {s.avg_latency != null ? `${Math.round(s.avg_latency)}ms` : "\u2014"}</span>
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
                    {weeklyReview.summary.healthy ?? "\u2014"}
                  </span>
                  <span className="weekly-stat-label">Sources saines</span>
                </div>
                <div className="weekly-stat">
                  <span className="weekly-stat-value" style={{ color: "var(--red)" }}>
                    {weeklyReview.summary.degraded ?? "\u2014"}
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

      {/* Agent logs — custom rendering for expandable news items */}
      <LogSection
        logs={filteredLogs}
        logFilter={logFilter}
        setLogFilter={setLogFilter}
        title="Logs Agent News"
        maxLogs={30}
        renderExtra={(log, i) => {
          const hasNewsItems = log.details?.news_items && Array.isArray(log.details.news_items) && log.details.news_items.length > 0;
          return hasNewsItems && expandedLog === i ? <CollectedNewsList items={log.details.news_items} /> : null;
        }}
        onLogClick={(log, i) => {
          const hasNewsItems = log.details?.news_items && Array.isArray(log.details.news_items) && log.details.news_items.length > 0;
          if (hasNewsItems) setExpandedLog(expandedLog === i ? null : i);
        }}
      />
    </div>
  );
}
