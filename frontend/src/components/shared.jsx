/**
 * Shared UI components — eliminates duplication across 18+ pages.
 */
import React from "react";
import { LEVEL_ICONS, LEVEL_COLORS } from "../utils/constants";
import { formatLogDetails } from "../utils/format";

// ── Error banner (visible error state) ───────────────────────
export function ErrorBanner({ error, onRetry }) {
  if (!error) return null;
  return (
    <div className="error-banner" role="alert">
      <span className="error-banner-icon">!</span>
      <span className="error-banner-text">Erreur : {error}</span>
      {onRetry && (
        <button className="error-banner-retry" onClick={onRetry}>
          Réessayer
        </button>
      )}
    </div>
  );
}

// ── Empty state (consistent "no data" message) ──────────────
export function EmptyState({ message, detail }) {
  return (
    <div className="empty-state">
      <div className="empty-state-icon">—</div>
      <div className="empty-state-message">{message || "Aucune donnée"}</div>
      {detail && <div className="empty-state-detail">{detail}</div>}
    </div>
  );
}

// ── Last updated timestamp ──────────────────────────────────
export function LastUpdated({ date }) {
  if (!date) return null;
  const diff = Math.floor((Date.now() - date.getTime()) / 1000);
  let text;
  if (diff < 10) text = "à l'instant";
  else if (diff < 60) text = `il y a ${diff}s`;
  else if (diff < 3600) text = `il y a ${Math.floor(diff / 60)} min`;
  else text = `il y a ${Math.floor(diff / 3600)}h`;
  return (
    <span className="last-updated-text" title={date.toLocaleString("fr-FR")}>
      MAJ {text}
    </span>
  );
}

// ── Adjustment bar (Learning dimensions) ─────────────────────
export function AdjBar({ label, value, min = 0.5, max = 1.5 }) {
  const clamped = Math.max(min, Math.min(max, value ?? 1));
  const pct = ((clamped - min) / (max - min)) * 100;
  const color = clamped >= 1.05 ? "var(--green)" : clamped <= 0.95 ? "var(--red)" : "var(--text-muted)";
  return (
    <div className="adj-bar-row">
      <span className="adj-bar-label">{label}</span>
      <div className="adj-bar-track">
        <div className="adj-bar-center" />
        <div className="adj-bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="adj-bar-value" style={{ color }}>
        {value != null ? value.toFixed(2) : "—"}
      </span>
    </div>
  );
}

// ── Log entry (formatted, not raw JSON) ──────────────────────
export function LogEntry({ log, expandable, expanded, onToggle }) {
  const icon = LEVEL_ICONS[log.level] || "i";
  const color = LEVEL_COLORS[log.level] || "var(--text-secondary)";
  const details = formatLogDetails(log.details);
  const hasExpandable = expandable && log.details?.news_items?.length > 0;

  return (
    <div
      className={`agent-log-entry ${(log.level || "info").toLowerCase()}`}
      onClick={hasExpandable ? onToggle : undefined}
      style={hasExpandable ? { cursor: "pointer" } : undefined}
      role={hasExpandable ? "button" : undefined}
      tabIndex={hasExpandable ? 0 : undefined}
      onKeyDown={hasExpandable ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggle(); } } : undefined}
      aria-expanded={hasExpandable ? expanded : undefined}
    >
      <div className="agent-log-header">
        <span className="agent-log-icon">{icon}</span>
        <span className="agent-log-action" style={{ color }}>
          {log.action}
        </span>
        <span className="agent-log-time">
          {log.timestamp
            ? new Date(log.timestamp).toLocaleTimeString("fr-FR", {
                hour: "2-digit", minute: "2-digit", second: "2-digit",
              })
            : ""}
        </span>
        {log.duration_ms != null && (
          <span className="agent-log-duration">{log.duration_ms}ms</span>
        )}
        {hasExpandable && (
          <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 6 }}>
            {expanded ? "\u25B2" : "\u25BC"}
          </span>
        )}
      </div>
      {details && details.length > 0 && (
        <div className="agent-log-details">
          {details.slice(0, 4).map((d) => (
            <span key={d.key} className="agent-log-detail">
              <span className="agent-log-detail-key">{d.key}:</span> {d.value}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Log section with filter toolbar ──────────────────────────
export function LogSection({ logs, logFilter, setLogFilter, limit, title, maxLogs, renderExtra, onLogClick }) {
  const displayLimit = maxLogs || limit || 20;
  const [expandedLog, setExpandedLog] = React.useState(null);
  const filtered = logs || [];

  return (
    <div className="section-card">
      <div className="section-header">
        <h3>{title || "Logs"}</h3>
        <div className="log-filter-row">
          {["ALL", "DECISION", "INFO", "WARN", "ERROR"].map((level) => (
            <button
              key={level}
              className={`log-filter-btn ${logFilter === level ? "active" : ""}`}
              onClick={() => setLogFilter(level)}
            >
              {level}
            </button>
          ))}
        </div>
      </div>
      <div className="agent-logs compact-logs">
        {filtered.length === 0 ? (
          <EmptyState message="Aucun log" detail="Les logs apparaissent après la première exécution de l'agent." />
        ) : (
          [...filtered].reverse().slice(0, displayLimit).map((log, i) => (
            <React.Fragment key={`${log.timestamp}-${i}`}>
              <LogEntry
                log={log}
                expandable
                expanded={expandedLog === i}
                onToggle={() => {
                  setExpandedLog(expandedLog === i ? null : i);
                  if (onLogClick) onLogClick(log, i);
                }}
              />
              {renderExtra && renderExtra(log, i)}
            </React.Fragment>
          ))
        )}
      </div>
    </div>
  );
}

// ── Focus trap for modals/overlays ───────────────────────────
export function useFocusTrap(ref, active) {
  React.useEffect(() => {
    if (!active || !ref.current) return;
    const el = ref.current;
    const focusable = el.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    // Focus first element
    first.focus();

    const trap = (e) => {
      if (e.key !== "Tab") return;
      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    el.addEventListener("keydown", trap);
    return () => el.removeEventListener("keydown", trap);
  }, [ref, active]);
}
