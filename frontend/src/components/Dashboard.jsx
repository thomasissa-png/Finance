import React, { useEffect, useState, useCallback, useRef } from "react";
import { timeAgo } from "../utils/format";
import TradeCard from "./TradeCard";

// (O5) Dismissable toast system
function useToasts() {
  const [toasts, setToasts] = useState([]);
  const idRef = useRef(0);

  const addToast = useCallback((message, type = "info") => {
    const id = ++idRef.current;
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.map((t) => (t.id === id ? { ...t, exiting: true } : t)));
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, 250);
    }, 4000);
  }, []);

  const dismissToast = useCallback((id) => {
    setToasts((prev) => prev.map((t) => (t.id === id ? { ...t, exiting: true } : t)));
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 250);
  }, []);

  return { toasts, addToast, dismissToast };
}

// Next scan time
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
  if (day === 0 || day === 6) {
    return { text: "Prochain scan : lundi 07:50 CET", cls: "weekend" };
  }
  const totalMin = paris.getHours() * 60 + paris.getMinutes();
  for (const s of SCAN_SCHEDULE) {
    if (totalMin < s.h * 60 + s.m) {
      return { text: `Prochain scan : aujourd'hui ${s.label}`, cls: "online" };
    }
  }
  if (day === 5) {
    return { text: "Prochain scan : lundi 07:50 CET", cls: "offline" };
  }
  return { text: "Prochain scan : demain 07:50 CET (Europe)", cls: "offline" };
}

// Scan configuration
const SCAN_DEFS = [
  { key: "europe", label: "SCAN EUROPE — 07:50 CET", btn: "Europe (07:50)", cls: "europe" },
  { key: "mid_session", label: "SCAN MID-SESSION — 11:15 CET", btn: "Mid-Session (11:15)", cls: "europe" },
  { key: "us", label: "SCAN PRE-US — 14:50 CET", btn: "Pre-US (14:50)", cls: "us" },
  { key: "us_session", label: "SCAN US SESSION — 17:00 CET", btn: "US Session (17:00)", cls: "us" },
];

// (C2) Scan progress steps
const PROGRESS_STEPS = ["Collecte RSS", "Analyse Claude", "Sélection trade"];

// Detect API errors
function getApiError(scans) {
  for (const key of Object.keys(scans)) {
    const scan = scans[key];
    if (scan?.api_error) return scan;
    const reason = scan?.reason_no_trade || "";
    if (reason.includes("API Claude") || reason.includes("AuthenticationError") || reason.includes("RateLimitError")) {
      return scan;
    }
  }
  return null;
}

export default function Dashboard() {
  const [scans, setScans] = useState({});
  const [loading, setLoading] = useState({});
  const [scanInfo, setScanInfo] = useState(getNextScanInfo);
  const [lastUpdate, setLastUpdate] = useState(null);
  // (C2) Track scan progress per scan key
  const [progress, setProgress] = useState({});
  const { toasts, addToast, dismissToast } = useToasts();

  const apiErrorScan = getApiError(scans);

  const fetchScans = useCallback(async () => {
    if (document.hidden) return;
    try {
      const res = await fetch("/api/scan/latest");
      if (res.ok) {
        setScans(await res.json());
        setLastUpdate(new Date());
      }
    } catch {
      /* backend not yet started */
    }
  }, []);

  useEffect(() => {
    fetchScans();
    const interval = setInterval(fetchScans, 60_000);
    return () => clearInterval(interval);
  }, [fetchScans]);

  useEffect(() => {
    const interval = setInterval(() => setScanInfo(getNextScanInfo()), 60_000);
    return () => clearInterval(interval);
  }, []);

  const triggerScan = async (scanType) => {
    setLoading((prev) => ({ ...prev, [scanType]: true }));
    // (C2) Simulate progress steps
    setProgress((prev) => ({ ...prev, [scanType]: 0 }));
    const stepTimer1 = setTimeout(() => setProgress((prev) => ({ ...prev, [scanType]: 1 })), 3000);
    const stepTimer2 = setTimeout(() => setProgress((prev) => ({ ...prev, [scanType]: 2 })), 8000);

    try {
      const res = await fetch(`/api/scan/trigger/${scanType}`, { method: "POST" });
      if (res.ok) {
        const result = await res.json();
        setScans((prev) => ({ ...prev, [scanType]: result }));
        setLastUpdate(new Date());
        if (result.has_trade) {
          const recs = result.recommendations || [];
          const count = recs.length || (result.recommendation ? 1 : 0);
          if (count > 1) {
            const tickers = recs.map((r) => r.ticker).join(", ");
            addToast(`${count} trades détectés : ${tickers}`, "success");
          } else {
            addToast(`Trade détecté : ${result.recommendation?.ticker}`, "success");
          }
        } else {
          addToast(`Scan ${scanType} terminé — pas de trade`, "warning");
        }
      } else {
        const err = await res.json().catch(() => ({}));
        addToast(err.detail || `Erreur scan ${scanType}`, "error");
      }
    } catch (err) {
      console.error("Trigger failed:", err);
      addToast("Erreur réseau", "error");
    } finally {
      clearTimeout(stepTimer1);
      clearTimeout(stepTimer2);
      setLoading((prev) => ({ ...prev, [scanType]: false }));
      setProgress((prev) => {
        const next = { ...prev };
        delete next[scanType];
        return next;
      });
    }
  };

  return (
    <div>
      {/* API error alert banner */}
      {apiErrorScan && (
        <div className="api-error-banner">
          <strong>API Claude hors service</strong>
          <span>
            {apiErrorScan.api_error === "AuthenticationError"
              ? "Clé API invalide ou crédits épuisés. Vérifiez ANTHROPIC_API_KEY."
              : apiErrorScan.api_error === "RateLimitError"
                ? "Limite de requêtes atteinte. Vérifiez vos crédits Anthropic."
                : `Erreur API : ${apiErrorScan.reason_no_trade || apiErrorScan.api_error}`}
          </span>
          <span>Les scans ne peuvent pas scorer les news tant que l'API est indisponible.</span>
        </div>
      )}

      {/* Scan status bar */}
      <div className="scan-status-bar">
        <span className={`status-dot ${scanInfo.cls}`} />
        {scanInfo.text}
      </div>

      <div className="trigger-section">
        {SCAN_DEFS.map((s) => (
          <button
            key={s.key}
            className={`trigger-btn ${s.cls}`}
            onClick={() => triggerScan(s.key)}
            disabled={loading[s.key]}
          >
            {loading[s.key] ? (
              <>
                <span className="spinner spinner-inline" />
                Scan en cours...
              </>
            ) : (
              `Scan ${s.btn}`
            )}
          </button>
        ))}
      </div>

      {/* (C2) Active scan progress */}
      {Object.keys(progress).length > 0 &&
        Object.entries(progress).map(([key, step]) => (
          <div key={key} className="scan-progress">
            {PROGRESS_STEPS.map((label, i) => (
              <React.Fragment key={i}>
                {i > 0 && <span className="scan-progress-separator" />}
                <span
                  className={`scan-progress-step ${
                    i < step ? "done" : i === step ? "active" : ""
                  }`}
                >
                  <span className="scan-progress-dot" />
                  {label}
                </span>
              </React.Fragment>
            ))}
          </div>
        ))}

      <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
        {SCAN_DEFS.map((s) => {
          const scan = scans[s.key];
          const recs = scan?.recommendations || [];
          if (scan?.has_trade && recs.length > 1) {
            return (
              <div key={s.key} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <div className="scan-multi-label">
                  {s.label} — {recs.length} trades
                </div>
                {recs.map((rec, i) => (
                  <TradeCard
                    key={`${s.key}-${rec.ticker}-${i}`}
                    scan={{ ...scan, recommendation: rec }}
                    label={`${s.label} #${i + 1}`}
                  />
                ))}
              </div>
            );
          }
          return <TradeCard key={s.key} scan={scan} label={s.label} />;
        })}
      </div>

      {/* (O1) Last update timestamp */}
      {lastUpdate && (
        <div className="last-update">MAJ {timeAgo(lastUpdate)}</div>
      )}

      {/* Toast container — (O5) dismissable */}
      {toasts.length > 0 && (
        <div className="toast-container">
          {toasts.map((t) => (
            <div key={t.id} className={`toast ${t.type} ${t.exiting ? "exiting" : ""}`}>
              <span>{t.message}</span>
              <button className="toast-dismiss" onClick={() => dismissToast(t.id)}>
                &times;
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
