import React, { useState, useEffect, useCallback } from "react";
import { POLL_NORMAL, AGENT_LABELS } from "../utils/constants";
import { apiFetch } from "../utils/api";
import { ErrorBanner, LastUpdated, LogSection } from "./shared";

const AUDIT_TARGETS = [
  "news", "scoring", "scoring_2", "scoring_3", "scoring_4",
  "trader_1", "trader_2", "trader_3", "trader_4",
  "journal", "journal_2", "journal_3", "journal_4",
  "learning", "learning_2", "learning_3", "learning_4",
  "infrastructure", "performance", "auditor",
];

const TARGET_TEAMS = {
  news: "Partagé",
  scoring: "Équipe 1",
  scoring_2: "Équipe 2",
  scoring_3: "Équipe 3",
  scoring_4: "Équipe 4",
  trader_1: "Équipe 1",
  trader_2: "Équipe 2",
  trader_3: "Équipe 3",
  trader_4: "Équipe 4",
  journal: "Équipe 1",
  journal_2: "Équipe 2",
  journal_3: "Équipe 3",
  journal_4: "Équipe 4",
  learning: "Équipe 1",
  learning_2: "Équipe 2",
  learning_3: "Équipe 3",
  learning_4: "Équipe 4",
  infrastructure: "Partagé",
  performance: "Partagé",
  auditor: "Partagé",
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

export default function AuditorPage({ isActive, agents }) {
  const agentMap = React.useMemo(() => {
    const m = {};
    (agents || []).forEach((a) => { m[a.name] = a; });
    return m;
  }, [agents]);
  const [reports, setReports] = useState([]);
  const [logs, setLogs] = useState([]);
  const [logFilter, setLogFilter] = useState("ALL");
  const [auditLoading, setAuditLoading] = useState({});
  const [expandedReport, setExpandedReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [auditFeedback, setAuditFeedback] = useState(null);
  const [teamFilter, setTeamFilter] = useState("all");

  const fetchData = useCallback(async () => {
    try {
      const [rRes, logRes] = await Promise.all([
        apiFetch("/api/agents/auditor/reports?limit=20", {}, []),
        apiFetch(`/api/agents/auditor/logs?limit=50${logFilter !== "ALL" ? `&level=${logFilter}` : ""}`, {}, []),
      ]);
      setReports(Array.isArray(rRes) ? rRes : []);
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

  const triggerAudit = async (target) => {
    setAuditLoading((prev) => ({ ...prev, [target]: true }));
    setAuditFeedback(null);
    try {
      const res = await fetch(`/api/agents/auditor/audit/${target}`, { method: "POST" });
      if (res.ok) {
        setAuditFeedback({ type: "success", message: `Audit ${AGENT_LABELS[target] || target} lancé avec succès` });
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

  const filteredTargets = teamFilter === "all"
    ? AUDIT_TARGETS
    : AUDIT_TARGETS.filter((t) => TARGET_TEAMS[t] === teamFilter);

  return (
    <div className="agent-page">
      <div className="page-header">
        <div className="page-title">Audit</div>
        <div className="page-subtitle">Audit en profondeur de chaque agent, note /10, améliorations, tendances</div>
        <LastUpdated date={lastUpdate} />
      </div>

      {loading && <div className="agent-loading"><span className="spinner" /> Chargement des données...</div>}
      <ErrorBanner error={error} onRetry={fetchData} />
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
            {["all", "Équipe 1", "Équipe 2", "Équipe 3", "Équipe 4", "Partagé"].map((f) => (
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
                  {agentMap[target] && (
                    <span style={{ width: 6, height: 6, borderRadius: "50%", display: "inline-block", marginRight: 4,
                      background: agentMap[target].status === "working" ? "var(--accent)" : agentMap[target].status === "error" ? "var(--red)" : "var(--text-muted)" }} />
                  )}
                  {AGENT_LABELS[target] || target}
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
                        {AGENT_LABELS[report.agent] || report.agent}
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
                          <h4>Améliorations proposées</h4>
                          <ul>
                            {report.improvements.map((imp, i) => (
                              <li key={i}>{typeof imp === "string" ? imp : JSON.stringify(imp)}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {report.checks && Object.keys(report.checks).length > 0 && (
                        <div className="audit-section">
                          <h4>Détail des checks</h4>
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
      <LogSection logs={logs} logFilter={logFilter} setLogFilter={setLogFilter} title="Logs Auditeur" />
    </div>
  );
}
