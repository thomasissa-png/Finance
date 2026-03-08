import React, { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { timeAgo, pnlColor, tickerName } from "../utils/format";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Area, AreaChart } from "recharts";
import TradeCard from "./TradeCard";

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
  { key: "europe", label: "Scan Europe 07:50 CET", btn: "Europe (07:50)", cls: "europe" },
  { key: "mid_session", label: "Scan Mid-Session 11:15 CET", btn: "Mid-Session (11:15)", cls: "europe" },
  { key: "us", label: "Scan Pre-US 14:50 CET", btn: "Pre-US (14:50)", cls: "us" },
  { key: "us_session", label: "Scan US Session 17:00 CET", btn: "US Session (17:00)", cls: "us" },
];

const PROGRESS_STEPS = ["Collecte RSS", "Analyse Claude", "Sélection trade"];

function getApiError(scans) {
  for (const key of Object.keys(scans)) {
    const scan = scans[key];
    if (scan?.api_error) return scan;
    const reason = scan?.reason_no_trade || "";
    if (reason.includes("API Claude") || reason.includes("AuthenticationError") || reason.includes("RateLimitError")) return scan;
  }
  return null;
}

/* Mini sparkline for KPI cards */
function Sparkline({ data, color, height = 30 }) {
  if (!data || data.length < 2) return null;
  return (
    <div style={{ width: "100%", height, marginTop: 4 }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 2, right: 2, bottom: 0, left: 2 }}>
          <defs>
            <linearGradient id={`spark-${color}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.3} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <Area type="monotone" dataKey="v" stroke={color} strokeWidth={1.5} fill={`url(#spark-${color})`} dot={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/* P&L Equity Curve */
function EquityCurve({ history }) {
  const data = useMemo(() => {
    if (!Array.isArray(history) || history.length === 0) return [];
    return history.map((h) => ({
      date: h.date || h.timestamp ? new Date(h.date || h.timestamp).toLocaleDateString("fr-FR", { day: "2-digit", month: "short" }) : "",
      pnl: h.cumulative_pnl ?? h.total_pnl_pct ?? h.pnl ?? 0,
      wr: h.win_rate ?? 0,
    }));
  }, [history]);

  if (data.length < 2) return null;

  const lastPnl = data[data.length - 1]?.pnl ?? 0;
  const color = lastPnl >= 0 ? "#10B981" : "#EF4444";

  return (
    <div className="section-card" style={{ padding: 20 }}>
      <h3>Courbe d'équité (P&L cumulé)</h3>
      <div style={{ width: "100%", height: 220, marginTop: 12 }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
            <defs>
              <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color} stopOpacity={0.2} />
                <stop offset="100%" stopColor={color} stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#8B9DC3" }} tickLine={false} axisLine={{ stroke: "#1E2D4A" }} />
            <YAxis tick={{ fontSize: 10, fill: "#8B9DC3" }} tickLine={false} axisLine={false} tickFormatter={(v) => `${v > 0 ? "+" : ""}${v.toFixed(1)}%`} width={55} />
            <Tooltip
              contentStyle={{ background: "#131D33", border: "1px solid #1E2D4A", borderRadius: 6, fontSize: 12, color: "#E8ECF4" }}
              formatter={(v) => [`${v > 0 ? "+" : ""}${v.toFixed(2)}%`, "P&L"]}
            />
            <Area type="monotone" dataKey="pnl" stroke={color} strokeWidth={2} fill="url(#eqGrad)" dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/* Flash animation hook for KPI value changes */
function useFlash(value) {
  const prevRef = useRef(value);
  const [flash, setFlash] = useState(null);
  useEffect(() => {
    if (prevRef.current !== value && value != null && prevRef.current != null) {
      setFlash(value > prevRef.current ? "flash-green" : "flash-red");
      const t = setTimeout(() => setFlash(null), 600);
      prevRef.current = value;
      return () => clearTimeout(t);
    }
    prevRef.current = value;
  }, [value]);
  return flash;
}

function KpiCard({ value, label, color, sparkData, sparkColor }) {
  const flash = useFlash(value);
  return (
    <div className={`kpi-card ${flash || ""}`}>
      <div className="kpi-value" style={color ? { color } : undefined}>{value}</div>
      <div className="kpi-label">{label}</div>
      {sparkData && <Sparkline data={sparkData} color={sparkColor || "#3B82F6"} />}
    </div>
  );
}

export default function DashboardPage({ isActive, agents }) {
  const [scans, setScans] = useState({});
  const [loading, setLoading] = useState({});
  const [scanInfo, setScanInfo] = useState(getNextScanInfo);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [progress, setProgress] = useState({});
  const [perf, setPerf] = useState(null);
  const [history, setHistory] = useState([]);
  const { toasts, addToast, dismissToast } = useToasts();

  const apiErrorScan = getApiError(scans);

  const fetchScans = useCallback(async () => {
    if (document.hidden) return;
    try {
      const res = await fetch("/api/scan/latest");
      if (res.ok) { setScans(await res.json()); setLastUpdate(new Date()); }
    } catch { /* network error — silent */ }
  }, []);

  const fetchPerf = useCallback(async () => {
    try {
      const [pRes, hRes] = await Promise.all([
        fetch("/api/performance").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch("/api/performance/history?limit=30").then((r) => r.ok ? r.json() : []).catch(() => []),
      ]);
      if (pRes) setPerf(pRes);
      if (Array.isArray(hRes)) setHistory(hRes);
    } catch { /* */ }
  }, []);

  useEffect(() => {
    fetchScans(); fetchPerf();
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

  // Build sparkline data from history
  const wrSpark = useMemo(() => history.map((h) => ({ v: h.win_rate ?? 0 })).slice(-7), [history]);
  const pnlSpark = useMemo(() => history.map((h) => ({ v: h.total_pnl_pct ?? h.pnl ?? 0 })).slice(-7), [history]);

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
            ? `${count} trades détectés : ${recs.map((r) => tickerName(r.ticker)).join(", ")}`
            : `Trade détecté : ${tickerName(result.recommendation?.ticker)}`, "success");
        } else {
          addToast(`Scan ${scanType} terminé, pas de trade`, "warning");
        }
      } else {
        const err = await res.json().catch(() => ({}));
        addToast(err.detail || `Erreur scan ${scanType}`, "error");
      }
    } catch (err) {
      addToast("Erreur réseau", "error");
    } finally {
      clearTimeout(t1); clearTimeout(t2);
      setLoading((prev) => ({ ...prev, [scanType]: false }));
      setProgress((prev) => { const n = { ...prev }; delete n[scanType]; return n; });
    }
  };

  return (
    <div className="page-fade-in">
      <div className="page-header">
        <div className="page-title">Dashboard</div>
        <div className="page-subtitle">Vue consolidée des trades, toutes équipes</div>
      </div>

      {/* KPI Cards with sparklines */}
      {perf && (
        <div className="kpi-row">
          <KpiCard value={perf.total_trades || 0} label="Trades total" />
          <KpiCard
            value={`${(perf.win_rate || 0).toFixed(1)}%`}
            label="Win rate"
            color={(perf.win_rate || 0) >= 50 ? "var(--green)" : "var(--red)"}
            sparkData={wrSpark}
            sparkColor={(perf.win_rate || 0) >= 50 ? "#10B981" : "#EF4444"}
          />
          <KpiCard
            value={perf.total_pnl_pct != null ? `${perf.total_pnl_pct > 0 ? "+" : ""}${perf.total_pnl_pct.toFixed(2)}%` : "--"}
            label="P&L total"
            color={pnlColor(perf.total_pnl_pct)}
            sparkData={pnlSpark}
            sparkColor={(perf.total_pnl_pct || 0) >= 0 ? "#10B981" : "#EF4444"}
          />
          <KpiCard value={perf.pending || 0} label="En cours" />
        </div>
      )}

      {/* Equity curve */}
      <EquityCurve history={history} />

      {/* API error */}
      {apiErrorScan && (
        <div className="api-error-banner">
          <strong>API Claude hors service</strong>
          <span>
            {apiErrorScan.api_error === "AuthenticationError"
              ? "Clé API invalide ou crédits épuisés."
              : apiErrorScan.api_error === "RateLimitError"
                ? "Limite de requêtes atteinte."
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
          <div key={key} className="scan-progress" role="progressbar" aria-valuenow={step} aria-valuemin={0} aria-valuemax={2} aria-label={`Progression scan : étape ${step + 1} sur 3`}>
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
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {SCAN_DEFS.map((s) => {
          const scan = scans[s.key];
          const recs = scan?.recommendations || [];
          if (scan?.has_trade && recs.length > 1) {
            return (
              <div key={s.key} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div className="scan-multi-label">{s.label} ({recs.length} trades)</div>
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
        <button className="refresh-btn" onClick={fetchScans} title="Rafraîchir" aria-label="Rafraîchir les données">↻</button>
      </div>

      {/* Toasts */}
      {toasts.length > 0 && (
        <div className="toast-container" role="status" aria-live="polite">
          {toasts.map((t) => (
            <div key={t.id} className={`toast ${t.type} ${t.exiting ? "exiting" : ""}`}>
              <span>{t.message}</span>
              <button className="toast-dismiss" onClick={() => dismissToast(t.id)} aria-label="Fermer">&times;</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
