import React, { useState, useEffect, useCallback } from "react";

const LEVEL_ICONS = { INFO: "i", WARN: "!", ERROR: "x", DECISION: ">" };

const AUDIT_TARGETS = [
  "news", "scoring", "scoring_2", "scoring_3", "scoring_4",
  "trader_1", "trader_2", "trader_3", "trader_4",
  "journal", "journal_2", "journal_3", "journal_4",
  "learning", "learning_2", "learning_3", "learning_4",
  "infrastructure", "performance", "auditor",
];

const TARGET_LABELS = {
  news: "News",
  scoring: "Scoring 1",
  scoring_2: "Scoring 2",
  scoring_3: "Scoring 3",
  scoring_4: "Scoring 4",
  trader_1: "Trader 1",
  trader_2: "Trader 2",
  trader_3: "Trader 3",
  trader_4: "Trader 4",
  journal: "Journal 1",
  journal_2: "Journal 2",
  journal_3: "Journal 3",
  journal_4: "Journal 4",
  learning: "Learning 1",
  learning_2: "Learning 2",
  learning_3: "Learning 3",
  learning_4: "Learning 4",
  infrastructure: "Infrastructure",
  performance: "Performance",
  auditor: "Auditeur",
};

const TARGET_TEAMS = {
  news: "Partage",
  scoring: "Equipe 1",
  scoring_2: "Equipe 2",
  scoring_3: "Equipe 3",
  scoring_4: "Equipe 4",
  trader_1: "Equipe 1",
  trader_2: "Equipe 2",
  trader_3: "Equipe 3",
  trader_4: "Equipe 4",
  journal: "Equipe 1",
  journal_2: "Equipe 2",
  journal_3: "Equipe 3",
  journal_4: "Equipe 4",
  learning: "Equipe 1",
  learning_2: "Equipe 2",
  learning_3: "Equipe 3",
  learning_4: "Equipe 4",
  infrastructure: "Partage",
  performance: "Partage",
  auditor: "Partage",
};

function ScoreCircle({ score }) {
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
  const [teamFilter, setTeamFilter] = useState("all");

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
        setAuditFeedback({ type: "success", message: `Audit ${TARGET_LABELS[target] || target} lance avec succes` });
        setTimeout(fetchData, 1500);
      } else {
        const err = await res.json().catch(() => ({}));
        setAuditFeedback({ type: "error", message: err.detail || `Erreur lors de l'audit ${target}` });
      }
    } catch (err) {
      setAuditFeedback({ type: "error", message: `Erreur reseau : ${err.message}` });
    } finally {
      setAuditLoading((prev) => ({ ...prev, [target]: false }));
      setTimeout(() => setAuditFeedback(null), 5000);
    }
  };

  const filteredTargets = teamFilter === "all"
    ? AUDIT_TARGETS
    : AUDIT_TARGETS.filter((t) => TARGET_TEAMS[t] === teamFilter);

  return (
    <div className="agent-page">
      <div className="page-header">
        <div className="page-title">Audit</div>
        <div className="page-subtitle">Audit en profondeur de chaque agent, note /10, ameliorations, tendances</div>
      </div>

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement des donnees...</div>}
      {fetchError && <div className="agent-error-banner">Erreur : {fetchError}</div>}
      {auditFeedback && (
        <div className={`agent-${auditFeedback.type === "success" ? "success" : "error"}-banner`}>
          {auditFeedback.message}
        </div>
      )}

      {/* Audit triggers */}
      <div className="section-card">
        <div className="section-header">
          <h3>Lancer un audit</h3>
          <div className="log-filter-row">
            {["all", "Equipe 1", "Equipe 2", "Equipe 3", "Equipe 4", "Partage"].map((f) => (
              <button key={f} className={`log-filter-btn ${teamFilter === f ? "active" : ""}`}
                onClick={() => setTeamFilter(f)}>
                {f === "all" ? "Tous" : f}
              </button>
            ))}
          </div>
        </div>
        <div className="audit-trigger-row">
          {filteredTargets.map((target) => (
            <button
              key={target}
              className="audit-trigger-btn"
              onClick={() => triggerAudit(target)}
              disabled={auditLoading[target]}
            >
              {auditLoading[target] ? (
                <><span className="spinner spinner-inline" /> Audit...</>
              ) : (
                <>
                  {TARGET_LABELS[target]}
                  <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 4 }}>
                    {TARGET_TEAMS[target]}
                  </span>
                </>
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
                        {report.timestamp ? new Date(report.timestamp).toLocaleDateString("fr-FR") : ""}{" "}
                        {report.timestamp ? new Date(report.timestamp).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" }) : ""}
                      </span>
                    </div>
                    {trend.direction && (
                      <span className={`audit-trend ${trend.direction}`}>
                        {trend.direction === "improving" ? "+" : trend.direction === "declining" ? "-" : "="}
                        {trend.delta != null && ` ${trend.delta > 0 ? "+" : ""}${trend.delta.toFixed(1)}`}
                      </span>
                    )}
                    <span className="expand-icon">{isExpanded ? "^" : "v"}</span>
                  </div>

                  {isExpanded && (
                    <div className="audit-report-detail">
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

                      {report.improvements && report.improvements.length > 0 && (
                        <div className="audit-section">
                          <h4>Ameliorations proposees</h4>
                          <ul>
                            {report.improvements.map((imp, i) => (
                              <li key={i}>{typeof imp === "string" ? imp : JSON.stringify(imp)}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {report.checks && Object.keys(report.checks).length > 0 && (
                        <div className="audit-section">
                          <h4>Detail des checks</h4>
                          <div className="audit-checks-grid">
                            {Object.entries(report.checks).map(([check, result]) => (
                              <div key={check} className="audit-check">
                                <span className="audit-check-name">{check.replace(/_/g, " ")}</span>
                                <span className={`audit-check-status ${result.status || (result.passed ? "pass" : "fail")}`}>
                                  {result.status || (result.passed ? "OK" : "FAIL")}
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
          <h3>Logs Auditeur</h3>
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
                  <span className="agent-log-icon">{LEVEL_ICONS[log.level] || "i"}</span>
                  <span className="agent-log-action" style={{ color: log.level === "ERROR" ? "var(--red)" : log.level === "WARN" ? "var(--yellow)" : log.level === "DECISION" ? "var(--accent)" : "var(--text-secondary)" }}>
                    {log.action}
                  </span>
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
