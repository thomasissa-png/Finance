import React, { useState, useEffect, useCallback } from "react";
import { formatDate, formatTime } from "../utils/format";

const LEVEL_ICONS = { INFO: "\u2139\ufe0f", WARN: "\u26a0\ufe0f", ERROR: "\u274c", DECISION: "\u26a1" };
const LEVEL_COLORS = { INFO: "var(--text-secondary)", WARN: "var(--yellow)", ERROR: "var(--red)", DECISION: "var(--cyan)" };

const AUDIT_TARGETS = [
  "news", "scoring", "scoring_2", "trader_1", "trader_2",
  "journal", "journal_2", "learning", "learning_2",
  "infrastructure", "performance", "auditor",
];
const TARGET_LABELS = {
  news: "\ud83d\udce1 News",
  scoring: "\ud83c\udfaf Scoring",
  scoring_2: "\ud83d\udccf Scoring 2",
  trader_1: "\ud83d\udcb9 Trader 1",
  trader_2: "\ud83d\udcc8 Trader 2",
  journal: "\ud83d\udcd3 Journal 1",
  journal_2: "\ud83d\udcd4 Journal 2",
  learning: "\ud83e\udde0 Learning 1",
  learning_2: "\ud83d\udca1 Learning 2",
  infrastructure: "\ud83d\udee0\ufe0f Infra",
  performance: "\ud83d\udcca Performance",
  auditor: "\ud83d\udd0d Auditeur",
};

function ScoreCircle({ score }) {
  const pct = Math.max(0, Math.min(100, (score / 10) * 100));
  const color = score >= 7 ? "var(--green)" : score >= 5 ? "var(--yellow)" : "var(--red)";
  return (
    <div className="score-circle" style={{ borderColor: color }}>
      <span style={{ color }}>{score?.toFixed(1)}</span>
      <span className="score-circle-label">/10</span>
    </div>
  );
}

export default function AuditorPage({ isActive }) {
  const [reports, setReports] = useState([]);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState("ALL");
  const [auditLoading, setAuditLoading] = useState({});
  const [expandedReport, setExpandedReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState(null);
  const [auditFeedback, setAuditFeedback] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const [rRes, logRes] = await Promise.all([
        fetch("/api/agents/auditor/reports?limit=20").then((r) => r.ok ? r.json() : []).catch(() => []),
        fetch(`/api/agents/auditor/logs?limit=50${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`)
          .then((r) => r.ok ? r.json() : []).catch(() => []),
      ]);
      setReports(Array.isArray(rRes) ? rRes : []);
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

  const triggerAudit = async (target) => {
    setAuditLoading((prev) => ({ ...prev, [target]: true }));
    setAuditFeedback(null);
    try {
      const res = await fetch(`/api/agents/auditor/audit/${target}`, { method: "POST" });
      if (res.ok) {
        setAuditFeedback({ type: "success", message: `Audit ${TARGET_LABELS[target] || target} lancé avec succès` });
        setTimeout(fetchData, 1500);
      } else {
        const err = await res.json().catch(() => ({}));
        setAuditFeedback({ type: "error", message: err.detail || `Erreur lors de l'audit ${target}` });
      }
    } catch (err) {
      setAuditFeedback({ type: "error", message: `Erreur réseau : ${err.message}` });
    } finally {
      setAuditLoading((prev) => ({ ...prev, [target]: false }));
      setTimeout(() => setAuditFeedback(null), 5000);
    }
  };

  return (
    <div className="agent-page">
      <div className="agent-page-header">
        <h2>{"\ud83d\udd0d"} Agent Auditeur</h2>
        <span className="agent-page-desc">Audit en profondeur de chaque agent, note /10, am\u00e9liorations, tendances</span>
      </div>

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement des données...</div>}
      {fetchError && <div className="agent-error-banner">Erreur : {fetchError}</div>}
      {auditFeedback && (
        <div className={`agent-${auditFeedback.type === "success" ? "success" : "error"}-banner`}>
          {auditFeedback.message}
        </div>
      )}

      {/* Audit triggers */}
      <div className="section-card">
        <h3>Lancer un audit</h3>
        <div className="audit-trigger-row">
          {AUDIT_TARGETS.map((target) => (
            <button
              key={target}
              className="audit-trigger-btn"
              onClick={() => triggerAudit(target)}
              disabled={auditLoading[target]}
            >
              {auditLoading[target] ? (
                <><span className="spinner spinner-inline" /> Audit...</>
              ) : (
                TARGET_LABELS[target]
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Reports */}
      <div className="section-card">
        <h3>Rapports d'audit ({reports.length})</h3>
        {reports.length === 0 ? (
          <div className="agent-logs-empty">Aucun rapport. Lancez un audit ci-dessus.</div>
        ) : (
          <div className="audit-reports">
            {reports.map((report, idx) => {
              const isExpanded = expandedReport === idx;
              const trend = report.trend || {};

              return (
                <div key={`${report.timestamp || report.agent}-${idx}`} className="audit-report-card">
                  <div className="audit-report-header" onClick={() => setExpandedReport(isExpanded ? null : idx)}>
                    <ScoreCircle score={report.score || 0} />
                    <div className="audit-report-meta">
                      <span className="audit-report-agent">
                        {TARGET_LABELS[report.agent] || report.agent}
                      </span>
                      <span className="audit-report-date">
                        {formatDate(report.timestamp)} {formatTime(report.timestamp)}
                      </span>
                    </div>
                    {trend.direction && (
                      <span className={`audit-trend ${trend.direction}`}>
                        {trend.direction === "improving" ? "\u2191" : trend.direction === "declining" ? "\u2193" : "\u2192"}
                        {trend.delta != null && ` ${trend.delta > 0 ? "+" : ""}${trend.delta.toFixed(1)}`}
                      </span>
                    )}
                    <span className="expand-icon">{isExpanded ? "\u25b2" : "\u25bc"}</span>
                  </div>

                  {isExpanded && (
                    <div className="audit-report-detail">
                      {/* Findings */}
                      {report.findings && report.findings.length > 0 && (
                        <div className="audit-section">
                          <h4>Constats</h4>
                          <ul>
                            {report.findings.map((f, i) => (
                              <li key={i}>{typeof f === "string" ? f : JSON.stringify(f)}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {/* Improvements */}
                      {report.improvements && report.improvements.length > 0 && (
                        <div className="audit-section">
                          <h4>Am\u00e9liorations propos\u00e9es</h4>
                          <ul>
                            {report.improvements.map((imp, i) => (
                              <li key={i}>{typeof imp === "string" ? imp : JSON.stringify(imp)}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {/* Checks detail */}
                      {report.checks && Object.keys(report.checks).length > 0 && (
                        <div className="audit-section">
                          <h4>D\u00e9tail des checks</h4>
                          <div className="audit-checks-grid">
                            {Object.entries(report.checks).map(([check, result]) => (
                              <div key={check} className="audit-check">
                                <span className="audit-check-name">{check.replace(/_/g, " ")}</span>
                                <span className={`audit-check-status ${result.status || (result.passed ? "pass" : "fail")}`}>
                                  {result.status || (result.passed ? "\u2705" : "\u274c")}
                                </span>
                                {result.message && (
                                  <span className="audit-check-msg">{String(result.message).slice(0, 100)}</span>
                                )}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Agent logs */}
      <div className="section-card">
        <div className="section-header">
          <h3>Logs Agent Auditeur</h3>
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
                </div>
                {log.details && Object.keys(log.details).length > 0 && (
                  <div className="agent-log-details">
                    {Object.entries(log.details).slice(0, 3).map(([k, v]) => (
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
