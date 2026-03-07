import React, { useState, useEffect, useCallback } from "react";

const LEVEL_ICONS = { INFO: "\u2139\ufe0f", WARN: "\u26a0\ufe0f", ERROR: "\u274c", DECISION: "\u26a1" };
const LEVEL_COLORS = { INFO: "var(--text-secondary)", WARN: "var(--yellow)", ERROR: "var(--red)", DECISION: "var(--cyan)" };

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

  const fetchData = useCallback(async () => {
    const [shRes, wrRes, logRes] = await Promise.all([
      fetch("/api/source-health?days=7").then((r) => r.json()).catch(() => null),
      fetch("/api/source-health/weekly").then((r) => r.json()).catch(() => null),
      fetch(`/api/agents/news/logs?limit=50${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`)
        .then((r) => r.json()).catch(() => []),
    ]);
    setSourceHealth(shRes);
    setWeeklyReview(wrRes);
    setLogs(Array.isArray(logRes) ? logRes : []);
  }, [logFilter]);

  useEffect(() => {
    if (isActive) fetchData();
    const id = setInterval(fetchData, 60_000);
    return () => clearInterval(id);
  }, [isActive, fetchData]);

  // Extract source data from health report
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
      <div className="agent-page-header">
        <h2>{"\ud83d\udce1"} Agent News</h2>
        <span className="agent-page-desc">Collecte, curation, sant\u00e9 des sources, d\u00e9tection d'\u00e9v\u00e9nements</span>
      </div>

      {/* Source Health */}
      <div className="section-card">
        <h3>Sant\u00e9 des sources</h3>
        {sources.length === 0 ? (
          <div className="agent-logs-empty">Aucune donn\u00e9e de sant\u00e9 disponible. Les donn\u00e9es apparaissent apr\u00e8s le premier scan.</div>
        ) : (
          <div className="compact-table">
            <table>
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Taux de succ\u00e8s</th>
                  <th>Sant\u00e9</th>
                  <th>\u00c9checs</th>
                  <th>Latence moy.</th>
                  <th>Derni\u00e8re erreur</th>
                </tr>
              </thead>
              <tbody>
                {sources.sort((a, b) => (a.success_rate ?? 1) - (b.success_rate ?? 1)).map((s) => (
                  <tr key={s.name}>
                    <td className="ticker-cell">{s.name}</td>
                    <td>
                      {s.success_rate != null
                        ? `${(s.success_rate * 100).toFixed(0)}%`
                        : "—"}
                    </td>
                    <td style={{ width: 120 }}>
                      <HealthBar rate={s.success_rate} />
                    </td>
                    <td style={{ color: s.failures > 0 ? "var(--red)" : "var(--text-muted)" }}>
                      {s.failures}
                    </td>
                    <td>
                      {s.avg_latency != null ? `${Math.round(s.avg_latency)}ms` : "—"}
                    </td>
                    <td className="error-cell">
                      {s.last_error ? String(s.last_error).slice(0, 60) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
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
                  <span className="weekly-stat-label">Sources d\u00e9grad\u00e9es</span>
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
                onClick={() => setLogFilter(level)}>
                {level}
              </button>
            ))}
          </div>
        </div>
        <div className="agent-logs compact-logs">
          {logs.length === 0 ? (
            <div className="agent-logs-empty">Aucun log</div>
          ) : (
            logs.slice(0, 20).map((log, i) => (
              <div key={`${log.timestamp}-${i}`} className={`agent-log-entry ${(log.level || "info").toLowerCase()}`}>
                <div className="agent-log-header">
                  <span className="agent-log-icon">{LEVEL_ICONS[log.level] || "\u2139\ufe0f"}</span>
                  <span className="agent-log-action" style={{ color: LEVEL_COLORS[log.level] }}>{log.action}</span>
                  <span className="agent-log-time">
                    {log.timestamp ? new Date(log.timestamp).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : ""}
                  </span>
                  {log.duration_ms != null && <span className="agent-log-duration">{log.duration_ms}ms</span>}
                </div>
                {log.details && Object.keys(log.details).length > 0 && (
                  <div className="agent-log-details">
                    {Object.entries(log.details).slice(0, 4).map(([k, v]) => (
                      <span key={k} className="agent-log-detail">
                        <span className="agent-log-detail-key">{k}:</span>{" "}
                        {typeof v === "object" ? JSON.stringify(v).slice(0, 80) : String(v).slice(0, 80)}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
