import React, { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { timeAgo, pnlColor, tickerName, formatDate, formatTime } from "../utils/format";
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
    // Only show banner for real API failures, not timeouts or empty results
    if (scan?.api_error === "AuthenticationError" || scan?.api_error === "RateLimitError") return scan;
    const reason = scan?.reason_no_trade || "";
    if (reason.includes("clé API invalide") || reason.includes("AuthenticationError") || reason.includes("RateLimitError")) return scan;
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

/* Team position label mapping */
const TEAM_LABELS = {
  "1": "Eq. 1 — Day Trading",
  "2": "Eq. 2 — Tendance",
  "3": "Eq. 3 — Technique",
  "4": "Eq. 4 — Meta",
};

const TEAM_COLORS = {
  "1": "var(--accent)",
  "2": "#F59E0B",
  "3": "#8B5CF6",
  "4": "#EC4899",
};

/* Compute live P&L from current price vs entry */
function computeLivePnl(entry_price, current_price, direction) {
  if (entry_price == null || current_price == null || !entry_price) return null;
  const pct = ((current_price - entry_price) / entry_price) * 100;
  return direction === "SHORT" ? -pct : pct;
}

/* All-teams open positions section */
function AllTeamsPositions({ positions, onRefresh, loading }) {
  const [livePrices, setLivePrices] = useState({});
  const [pricesLoading, setPricesLoading] = useState(false);

  const allPositions = useMemo(() => {
    const result = [];
    (positions["1"] || []).forEach((t) => {
      result.push({ ...t, _team: "1", _key: `t1-${t.ticker}-${t.timestamp}` });
    });
    (positions["2"] || []).forEach((t) => {
      result.push({ ...t, _team: "2", _key: `t2-${t.ticker}` });
    });
    (positions["3"] || []).forEach((t) => {
      result.push({ ...t, _team: "3", _key: `t3-${t.ticker}-${t.strategy || ""}` });
    });
    (positions["4"] || []).forEach((t) => {
      result.push({ ...t, _team: "4", _key: `t4-${t.ticker}-${t.entry_time || ""}` });
    });
    return result;
  }, [positions]);

  // Fetch live prices for all open tickers
  const fetchLivePrices = useCallback(async () => {
    const tickers = [...new Set(allPositions.map((p) => p.ticker).filter(Boolean))];
    if (tickers.length === 0) return;
    setPricesLoading(true);
    try {
      const res = await fetch(`/api/prices/current?tickers=${encodeURIComponent(tickers.join(","))}`);
      if (res.ok) {
        const data = await res.json();
        setLivePrices(data);
      }
    } catch { /* silent */ }
    setPricesLoading(false);
  }, [allPositions]);

  // Auto-fetch prices on mount and every 60s
  useEffect(() => {
    if (allPositions.length === 0) return;
    fetchLivePrices();
    const id = setInterval(fetchLivePrices, 60_000);
    return () => clearInterval(id);
  }, [fetchLivePrices, allPositions.length]);

  const totalCount = allPositions.length;

  return (
    <div className="section-card">
      <div className="section-header">
        <h3>Positions ouvertes — toutes équipes ({totalCount})</h3>
        <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
          {pricesLoading && <span className="spinner spinner-inline" style={{ width: 12, height: 12 }} />}
          <button
            className="refresh-btn"
            onClick={() => { onRefresh(); fetchLivePrices(); }}
            disabled={loading}
            title="Rafraîchir les positions et les prix"
            aria-label="Rafraîchir les positions"
            style={{ fontSize: 16, padding: "2px 8px" }}
          >
            {loading ? <span className="spinner spinner-inline" /> : "↻"}
          </button>
        </div>
      </div>

      {totalCount === 0 ? (
        <div className="agent-logs-empty" style={{ padding: "16px 0" }}>
          Aucune position ouverte actuellement.
        </div>
      ) : (
        <>
          {/* Desktop table */}
          <div className="compact-table desktop-only">
            <table>
              <thead>
                <tr>
                  <th>Équipe</th>
                  <th>Actif</th>
                  <th>Direction</th>
                  <th>Entrée</th>
                  <th>Prix actuel</th>
                  <th>Target</th>
                  <th>Stop</th>
                  <th>P&L latent</th>
                  <th>Ouvert depuis</th>
                </tr>
              </thead>
              <tbody>
                {allPositions.map((t) => {
                  const currentPrice = livePrices[t.ticker] ?? null;
                  const livePnl = computeLivePnl(t.entry_price, currentPrice, t.direction);
                  const displayPnl = livePnl ?? t.pnl_pct ?? t.unrealized_pnl ?? t.latent_pnl ?? null;
                  const entryTime = t.timestamp || t.entry_time || t.last_change_time;
                  return (
                    <tr key={t._key}>
                      <td>
                        <span className="team-badge" style={{ borderColor: TEAM_COLORS[t._team], color: TEAM_COLORS[t._team] }}>
                          {TEAM_LABELS[t._team] || `Éq. ${t._team}`}
                        </span>
                      </td>
                      <td className="ticker-cell">{tickerName(t.ticker)}</td>
                      <td>
                        <span className={`direction-badge ${(t.direction || "").toLowerCase()}`}>
                          {t.direction}
                        </span>
                      </td>
                      <td>{t.entry_price != null ? t.entry_price.toFixed(2) : "--"}</td>
                      <td style={{ fontWeight: 500, color: currentPrice ? "var(--text-primary)" : "var(--text-muted)" }}>
                        {currentPrice != null ? currentPrice.toFixed(2) : "--"}
                      </td>
                      <td style={{ color: "var(--green)" }}>{t.target_price != null ? t.target_price.toFixed(2) : "--"}</td>
                      <td style={{ color: "var(--red)" }}>{t.stop_price != null ? t.stop_price.toFixed(2) : "--"}</td>
                      <td style={{ color: pnlColor(displayPnl), fontWeight: 600 }}>
                        {displayPnl != null ? `${displayPnl > 0 ? "+" : ""}${displayPnl.toFixed(2)}%` : "--"}
                      </td>
                      <td style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                        {entryTime ? formatDate(entryTime) + " " + formatTime(entryTime) : "--"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Mobile cards */}
          <div className="mobile-only">
            {allPositions.map((t) => {
              const currentPrice = livePrices[t.ticker] ?? null;
              const livePnl = computeLivePnl(t.entry_price, currentPrice, t.direction);
              const displayPnl = livePnl ?? t.pnl_pct ?? t.unrealized_pnl ?? t.latent_pnl ?? null;
              return (
                <div key={t._key} className="trade-mobile-card">
                  <div className="trade-mobile-header">
                    <span className="team-badge" style={{ borderColor: TEAM_COLORS[t._team], color: TEAM_COLORS[t._team], fontSize: 9 }}>
                      {TEAM_LABELS[t._team]}
                    </span>
                    <span className="ticker-cell">{tickerName(t.ticker)}</span>
                    <span className={`direction-badge ${(t.direction || "").toLowerCase()}`}>{t.direction}</span>
                  </div>
                  <div className="trade-mobile-body">
                    <span>Entrée: {t.entry_price != null ? t.entry_price.toFixed(2) : "--"}</span>
                    {currentPrice != null && (
                      <span style={{ color: "var(--text-secondary)", fontSize: 11 }}>
                        Actuel: {currentPrice.toFixed(2)}
                      </span>
                    )}
                    <span style={{ color: pnlColor(displayPnl), fontWeight: 600 }}>
                      {displayPnl != null ? `${displayPnl > 0 ? "+" : ""}${displayPnl.toFixed(2)}%` : "--"}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

/* Per-team summary cards */
function TeamSummaryCards({ report, positions }) {
  const teams = [
    { id: "1", name: "Éq. 1 — Day Trading", perfKey: "trader_1", wrKey: "win_rate", pnlKey: "pnl_total", tradesKey: "total_trades" },
    { id: "2", name: "Éq. 2 — Tendance", perfKey: "trader_2", wrKey: "flip_win_rate", pnlKey: "realized_pnl", tradesKey: "total_flips" },
    { id: "3", name: "Éq. 3 — Technique", perfKey: "trader_3", wrKey: "win_rate", pnlKey: "pnl_total", tradesKey: "total_trades" },
    { id: "4", name: "Éq. 4 — Meta", perfKey: "trader_4", wrKey: "win_rate", pnlKey: "pnl_total", tradesKey: "total_trades" },
  ];

  return (
    <div className="teams-summary-row">
      {teams.map((team) => {
        const perf = report?.[team.perfKey] || {};
        const wr = perf[team.wrKey];
        const pnl = perf[team.pnlKey];
        const trades = perf[team.tradesKey] || 0;
        const openCount = (positions[team.id] || []).length;

        return (
          <div key={team.id} className="team-summary-card" style={{ borderLeftColor: TEAM_COLORS[team.id] }}>
            <div className="team-summary-name">{team.name}</div>
            <div className="team-summary-kpis">
              <div className="team-summary-kpi">
                <span className="team-summary-kpi-value" style={{ color: wr != null && wr >= 50 ? "var(--green)" : wr != null ? "var(--red)" : "var(--text-muted)" }}>
                  {wr != null ? `${wr.toFixed(1)}%` : "N/A"}
                </span>
                <span className="team-summary-kpi-label">WR</span>
              </div>
              <div className="team-summary-kpi">
                <span className="team-summary-kpi-value" style={{ color: pnlColor(pnl) }}>
                  {pnl != null ? `${pnl > 0 ? "+" : ""}${pnl.toFixed(2)}%` : "N/A"}
                </span>
                <span className="team-summary-kpi-label">P&L</span>
              </div>
              <div className="team-summary-kpi">
                <span className="team-summary-kpi-value">{trades}</span>
                <span className="team-summary-kpi-label">Trades</span>
              </div>
              <div className="team-summary-kpi">
                <span className="team-summary-kpi-value" style={{ color: openCount > 0 ? "var(--accent)" : "var(--text-muted)" }}>
                  {openCount}
                </span>
                <span className="team-summary-kpi-label">Ouvertes</span>
              </div>
            </div>
          </div>
        );
      })}
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
  const [report, setReport] = useState(null);
  const [history, setHistory] = useState([]);
  const [positions, setPositions] = useState({});
  const [posLoading, setPosLoading] = useState(false);
  const { toasts, addToast, dismissToast } = useToasts();
  const fetchCountRef = useRef(0);

  const apiErrorScan = getApiError(scans);

  /* ── Fetch all data in a single consolidated call ── */
  const fetchAllData = useCallback(async () => {
    if (document.hidden) return;
    const fetchId = ++fetchCountRef.current;
    try {
      const [scanRes, perfRes, reportRes, histRes, t1Res, t2Res, t3Res, t4Res] = await Promise.all([
        fetch("/api/scan/latest").then((r) => r.ok ? r.json() : {}).catch(() => ({})),
        fetch("/api/performance").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch("/api/performance/report").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch("/api/performance/history?limit=30").then((r) => r.ok ? r.json() : []).catch(() => []),
        fetch("/api/trades").then((r) => r.ok ? r.json() : []).catch(() => []),
        fetch("/api/trader2/positions").then((r) => r.ok ? r.json() : {}).catch(() => ({})),
        fetch("/api/trader3/positions").then((r) => r.ok ? r.json() : {}).catch(() => ({})),
        fetch("/api/trader4/positions").then((r) => r.ok ? r.json() : {}).catch(() => ({})),
      ]);

      // Only apply if this is still the most recent fetch
      if (fetchId !== fetchCountRef.current) return;

      setScans(scanRes);
      if (perfRes) setPerf(perfRes);
      if (reportRes) setReport(reportRes);
      if (Array.isArray(histRes)) setHistory(histRes);

      // Normalize positions for all teams
      // Team 1: trades array, filter PENDING
      const pending1 = Array.isArray(t1Res) ? t1Res.filter((t) => t.result === "PENDING") : [];
      // Team 2: dict {ticker: posData} — convert to array, filter non-FLAT
      const t2Values = t2Res && typeof t2Res === "object" && !Array.isArray(t2Res) ? Object.values(t2Res) : [];
      const active2 = t2Values.filter((t) => t.direction && t.direction !== "FLAT");
      // Team 3: {active: [...], closed: [...]}
      const active3 = Array.isArray(t3Res?.active) ? t3Res.active : [];
      // Team 4: dict {ticker: posData} — convert to array, filter active
      const t4Values = t4Res && typeof t4Res === "object" && !Array.isArray(t4Res) ? Object.values(t4Res) : [];
      const active4 = t4Values.filter((t) => t.direction && t.direction !== "FLAT" && t.direction !== "NONE");
      setPositions({ "1": pending1, "2": active2, "3": active3, "4": active4 });

      setLastUpdate(new Date());
    } catch { /* network error — silent */ }
  }, []);

  const refreshPositions = useCallback(async () => {
    setPosLoading(true);
    try {
      const [t1Res, t2Res, t3Res, t4Res] = await Promise.all([
        fetch("/api/trades").then((r) => r.ok ? r.json() : []).catch(() => []),
        fetch("/api/trader2/positions").then((r) => r.ok ? r.json() : {}).catch(() => ({})),
        fetch("/api/trader3/positions").then((r) => r.ok ? r.json() : {}).catch(() => ({})),
        fetch("/api/trader4/positions").then((r) => r.ok ? r.json() : {}).catch(() => ({})),
      ]);
      const pending1 = Array.isArray(t1Res) ? t1Res.filter((t) => t.result === "PENDING") : [];
      const t2Values = t2Res && typeof t2Res === "object" && !Array.isArray(t2Res) ? Object.values(t2Res) : [];
      const active2 = t2Values.filter((t) => t.direction && t.direction !== "FLAT");
      const active3 = Array.isArray(t3Res?.active) ? t3Res.active : [];
      const t4Values = t4Res && typeof t4Res === "object" && !Array.isArray(t4Res) ? Object.values(t4Res) : [];
      const active4 = t4Values.filter((t) => t.direction && t.direction !== "FLAT" && t.direction !== "NONE");
      setPositions({ "1": pending1, "2": active2, "3": active3, "4": active4 });
      setLastUpdate(new Date());
    } catch { /* */ }
    setPosLoading(false);
  }, []);

  // Initial fetch + polling (30s for positions, 60s for full refresh)
  useEffect(() => {
    fetchAllData();
    const id = setInterval(fetchAllData, 30_000);
    return () => clearInterval(id);
  }, [fetchAllData]);

  // Re-fetch on tab becoming visible or page becoming active
  useEffect(() => {
    if (isActive) fetchAllData();
  }, [isActive, fetchAllData]);

  useEffect(() => {
    const onVisible = () => { if (!document.hidden) fetchAllData(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [fetchAllData]);

  useEffect(() => {
    const interval = setInterval(() => setScanInfo(getNextScanInfo()), 60_000);
    return () => clearInterval(interval);
  }, []);

  // Total open positions across all teams
  const totalOpen = useMemo(() =>
    (positions["1"] || []).length + (positions["2"] || []).length +
    (positions["3"] || []).length + (positions["4"] || []).length,
    [positions]
  );

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
        // Refresh positions after scan (new trade may have been created)
        setTimeout(refreshPositions, 2000);
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
        <div>
          <div className="page-title">Dashboard</div>
          <div className="page-subtitle">Vue consolidée, toutes équipes</div>
        </div>
        <div className="dashboard-update-status">
          {lastUpdate && (
            <span className="last-update-text">
              MAJ {timeAgo(lastUpdate)}
            </span>
          )}
          <button className="refresh-btn" onClick={fetchAllData} title="Rafraîchir tout" aria-label="Rafraîchir les données">↻</button>
        </div>
      </div>

      {/* Global KPI Cards */}
      {perf && (
        <div className="kpi-row">
          <KpiCard value={perf.total_trades || 0} label="Trades total" />
          <KpiCard
            value={`${(perf.win_rate || 0).toFixed(1)}%`}
            label="Win rate (Éq. 1)"
            color={(perf.win_rate || 0) >= 50 ? "var(--green)" : "var(--red)"}
            sparkData={wrSpark}
            sparkColor={(perf.win_rate || 0) >= 50 ? "#10B981" : "#EF4444"}
          />
          <KpiCard
            value={perf.total_pnl_pct != null ? `${perf.total_pnl_pct > 0 ? "+" : ""}${perf.total_pnl_pct.toFixed(2)}%` : "--"}
            label="P&L (Éq. 1)"
            color={pnlColor(perf.total_pnl_pct)}
            sparkData={pnlSpark}
            sparkColor={(perf.total_pnl_pct || 0) >= 0 ? "#10B981" : "#EF4444"}
          />
          <KpiCard
            value={totalOpen}
            label="Positions ouvertes"
            color={totalOpen > 0 ? "var(--accent)" : undefined}
          />
        </div>
      )}

      {/* Per-team summary */}
      <TeamSummaryCards report={report} positions={positions} />

      {/* All teams open positions */}
      <AllTeamsPositions positions={positions} onRefresh={refreshPositions} loading={posLoading} />

      {/* Equity curve */}
      <EquityCurve history={history} />

      {/* API error — only real auth/rate-limit failures */}
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
