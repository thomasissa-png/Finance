import React, { useEffect, useState, useCallback, useRef } from "react";
import { timeAgo, pnlColor } from "../utils/format";
import TradeCard from "./TradeCard";
import AgentOverview from "./AgentOverview";

// Toast system
function useToasts() {
  const [toasts, setToasts] = useState([]);
  const idRef = useRef(0);
  const addToast = useCallback((message, type = "info") => {
    const id = ++idRef.current;
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.map((t) => (t.id === id ? { ...t, exiting: true } : t)));
      setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 250);
    }, 4000);
  }, []);
  const dismissToast = useCallback((id) => {
    setToasts((prev) => prev.map((t) => (t.id === id ? { ...t, exiting: true } : t)));
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 250);
  }, []);
  return { toasts, addToast, dismissToast };
}

const SCAN_SCHEDULE = [
  { h: 7, m: 50, label: "07:50 CET (Europe)" },
  { h: 11, m: 15, label: "11:15 CET (Mid-Session)" },
  { h: 14, m: 50, label: "14:50 CET (Pre-US)" },
  { h: 17, m: 0, label: "17:00 CET (US Session)" },
];

function getNextScanInfo() {
  const now = new Date();
  const paris = new Date(now.toLocaleString("en-US", { timeZone: "Europe/Paris" }));
  const day = paris.getDay();
  if (day === 0 || day === 6) return { text: "Prochain scan : lundi 07:50 CET", cls: "weekend" };
  const totalMin = paris.getHours() * 60 + paris.getMinutes();
  for (const s of SCAN_SCHEDULE) {
    if (totalMin < s.h * 60 + s.m) return { text: `Prochain scan : aujourd'hui ${s.label}`, cls: "online" };
  }
  if (day === 5) return { text: "Prochain scan : lundi 07:50 CET", cls: "offline" };
  return { text: "Prochain scan : demain 07:50 CET (Europe)", cls: "offline" };
}

const SCAN_DEFS = [
  { key: "europe", label: "SCAN EUROPE \u2014 07:50 CET", btn: "Europe (07:50)", cls: "europe" },
  { key: "mid_session", label: "SCAN MID-SESSION \u2014 11:15 CET", btn: "Mid-Session (11:15)", cls: "europe" },
  { key: "us", label: "SCAN PRE-US \u2014 14:50 CET", btn: "Pre-US (14:50)", cls: "us" },
  { key: "us_session", label: "SCAN US SESSION \u2014 17:00 CET", btn: "US Session (17:00)", cls: "us" },
];

const PROGRESS_STEPS = ["Collecte RSS", "Analyse Claude", "S\u00e9lection trade"];

function getApiError(scans) {
  for (const key of Object.keys(scans)) {
    const scan = scans[key];
    if (scan?.api_error) return scan;
    const reason = scan?.reason_no_trade || "";
    if (reason.includes("API Claude") || reason.includes("AuthenticationError") || reason.includes("RateLimitError")) return scan;
  }
  return null;
}

export default function DashboardPage({ isActive, agents }) {
  const [scans, setScans] = useState({});
  const [loading, setLoading] = useState({});
  const [scanInfo, setScanInfo] = useState(getNextScanInfo);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [progress, setProgress] = useState({});
  const [perf, setPerf] = useState(null);
  const { toasts, addToast, dismissToast } = useToasts();

  const apiErrorScan = getApiError(scans);

  const fetchScans = useCallback(async () => {
    if (document.hidden) return;
    try {
      const res = await fetch("/api/scan/latest");
      if (res.ok) { setScans(await res.json()); setLastUpdate(new Date()); }
    } catch { /* backend not started */ }
  }, []);

  const fetchPerf = useCallback(async () => {
    try {
      const res = await fetch("/api/performance");
      if (res.ok) setPerf(await res.json());
    } catch { /* */ }
  }, []);

  useEffect(() => {
    fetchScans();
    fetchPerf();
    const id = setInterval(fetchScans, 60_000);
    const id2 = setInterval(fetchPerf, 60_000);
    return () => { clearInterval(id); clearInterval(id2); };
  }, [fetchScans, fetchPerf]);

  useEffect(() => { if (isActive) { fetchScans(); fetchPerf(); } }, [isActive, fetchScans, fetchPerf]);

  useEffect(() => {
    const onVisible = () => { if (!document.hidden) fetchScans(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [fetchScans]);

  useEffect(() => {
    const interval = setInterval(() => setScanInfo(getNextScanInfo()), 60_000);
    return () => clearInterval(interval);
  }, []);

  const triggerScan = async (scanType) => {
    setLoading((prev) => ({ ...prev, [scanType]: true }));
    setProgress((prev) => ({ ...prev, [scanType]: 0 }));
    const t1 = setTimeout(() => setProgress((p) => ({ ...p, [scanType]: 1 })), 3000);
    const t2 = setTimeout(() => setProgress((p) => ({ ...p, [scanType]: 2 })), 8000);
    try {
      const res = await fetch(`/api/scan/trigger/${scanType}`, { method: "POST" });
      if (res.ok) {
        const result = await res.json();
        setScans((prev) => ({ ...prev, [scanType]: result }));
        setLastUpdate(new Date());
        if (result.has_trade) {
          const recs = result.recommendations || [];
          const count = recs.length || (result.recommendation ? 1 : 0);
          addToast(count > 1
            ? `${count} trades d\u00e9tect\u00e9s : ${recs.map((r) => r.ticker).join(", ")}`
            : `Trade d\u00e9tect\u00e9 : ${result.recommendation?.ticker}`, "success");
        } else {
          addToast(`Scan ${scanType} termin\u00e9 \u2014 pas de trade`, "warning");
        }
      } else {
        const err = await res.json().catch(() => ({}));
        addToast(err.detail || `Erreur scan ${scanType}`, "error");
      }
    } catch (err) {
      addToast("Erreur r\u00e9seau", "error");
    } finally {
      clearTimeout(t1);
      clearTimeout(t2);
      setLoading((prev) => ({ ...prev, [scanType]: false }));
      setProgress((prev) => { const n = { ...prev }; delete n[scanType]; return n; });
    }
  };

  return (
    <div className="dashboard-page">
      {/* KPI Cards */}
      {perf && (
        <div className="kpi-row">
          <div className="kpi-card">
            <div className="kpi-value">{perf.total_trades || 0}</div>
            <div className="kpi-label">Trades total</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-value" style={{ color: (perf.win_rate || 0) >= 50 ? "var(--green)" : "var(--red)" }}>
              {(perf.win_rate || 0).toFixed(1)}%
            </div>
            <div className="kpi-label">Win rate</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-value" style={{ color: pnlColor(perf.total_pnl_pct) }}>
              {perf.total_pnl_pct != null ? `${perf.total_pnl_pct > 0 ? "+" : ""}${perf.total_pnl_pct.toFixed(2)}%` : "--"}
            </div>
            <div className="kpi-label">P&L total</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-value">{perf.pending || 0}</div>
            <div className="kpi-label">En cours</div>
          </div>
        </div>
      )}

      {/* Agent Overview */}
      <AgentOverview agents={agents} onSelectAgent={() => {}} />

      {/* API error */}
      {apiErrorScan && (
        <div className="api-error-banner">
          <strong>API Claude hors service</strong>
          <span>
            {apiErrorScan.api_error === "AuthenticationError"
              ? "Cl\u00e9 API invalide ou cr\u00e9dits \u00e9puis\u00e9s."
              : apiErrorScan.api_error === "RateLimitError"
                ? "Limite de requ\u00eates atteinte."
                : `Erreur : ${apiErrorScan.reason_no_trade || apiErrorScan.api_error}`}
          </span>
        </div>
      )}

      {/* Scan triggers */}
      <div className="scan-status-bar">
        <span className={`status-dot ${scanInfo.cls}`} />
        {scanInfo.text}
      </div>

      <div className="trigger-section">
        {SCAN_DEFS.map((s) => (
          <button key={s.key} className={`trigger-btn ${s.cls}`} onClick={() => triggerScan(s.key)} disabled={loading[s.key]}>
            {loading[s.key] ? (<><span className="spinner spinner-inline" /> Scan en cours...</>) : `Scan ${s.btn}`}
          </button>
        ))}
      </div>

      {/* Progress */}
      {Object.keys(progress).length > 0 &&
        Object.entries(progress).map(([key, step]) => (
          <div key={key} className="scan-progress">
            {PROGRESS_STEPS.map((label, i) => (
              <React.Fragment key={i}>
                {i > 0 && <span className="scan-progress-separator" />}
                <span className={`scan-progress-step ${i < step ? "done" : i === step ? "active" : ""}`}>
                  <span className="scan-progress-dot" />
                  {label}
                </span>
              </React.Fragment>
            ))}
          </div>
        ))}

      {/* Scan results */}
      <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
        {SCAN_DEFS.map((s) => {
          const scan = scans[s.key];
          const recs = scan?.recommendations || [];
          if (scan?.has_trade && recs.length > 1) {
            return (
              <div key={s.key} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <div className="scan-multi-label">{s.label} \u2014 {recs.length} trades</div>
                {recs.map((rec, i) => (
                  <TradeCard key={`${s.key}-${rec.ticker}-${i}`} scan={{ ...scan, recommendation: rec }} label={`${s.label} #${i + 1}`} />
                ))}
              </div>
            );
          }
          return <TradeCard key={s.key} scan={scan} label={s.label} />;
        })}
      </div>

      {/* Last update */}
      <div className="last-update-bar">
        {lastUpdate && <span className="last-update">MAJ {timeAgo(lastUpdate)}</span>}
        <button className="refresh-btn" onClick={fetchScans} title="Rafra\u00eechir">{"\u21BB"}</button>
      </div>

      {/* Toasts */}
      {toasts.length > 0 && (
        <div className="toast-container">
          {toasts.map((t) => (
            <div key={t.id} className={`toast ${t.type} ${t.exiting ? "exiting" : ""}`}>
              <span>{t.message}</span>
              <button className="toast-dismiss" onClick={() => dismissToast(t.id)}>&times;</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
