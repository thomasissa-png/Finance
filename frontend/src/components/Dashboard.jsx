import React, { useEffect, useState, useCallback, useRef } from "react";
import TradeCard from "./TradeCard";

// (D6) Toast notification system
function useToasts() {
  const [toasts, setToasts] = useState([]);
  const idRef = useRef(0);

  const addToast = useCallback((message, type = "info") => {
    const id = ++idRef.current;
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 3200);
  }, []);

  return { toasts, addToast };
}

// (D3) Compute next scan time — 4 scans/day
const SCAN_SCHEDULE = [
  { h: 7, m: 50, label: "07:50 CET (Europe)" },
  { h: 11, m: 15, label: "11:15 CET (Mid-Session)" },
  { h: 14, m: 50, label: "14:50 CET (Pre-US)" },
  { h: 17, m: 0, label: "17:00 CET (US Session)" },
];

function getNextScanInfo() {
  const now = new Date();
  const day = now.getDay();
  // Weekend
  if (day === 0 || day === 6) {
    return { text: "Prochain scan : lundi 07:50 CET", cls: "weekend" };
  }
  const totalMin = now.getHours() * 60 + now.getMinutes();
  for (const s of SCAN_SCHEDULE) {
    if (totalMin < s.h * 60 + s.m) {
      return { text: `Prochain scan : aujourd'hui ${s.label}`, cls: "online" };
    }
  }
  if (day === 5) {
    // Friday after last scan
    return { text: "Prochain scan : lundi 07:50 CET", cls: "offline" };
  }
  return { text: "Prochain scan : demain 07:50 CET (Europe)", cls: "offline" };
}

// Scan configuration: key, label, button label, css class
const SCAN_DEFS = [
  { key: "europe", label: "SCAN EUROPE — 07:50 CET", btn: "Europe (07:50)", cls: "europe" },
  { key: "mid_session", label: "SCAN MID-SESSION — 11:15 CET", btn: "Mid-Session (11:15)", cls: "europe" },
  { key: "us", label: "SCAN PRE-US — 14:50 CET", btn: "Pre-US (14:50)", cls: "us" },
  { key: "us_session", label: "SCAN US SESSION — 17:00 CET", btn: "US Session (17:00)", cls: "us" },
];

// Detect API errors from any scan result
function getApiError(scans) {
  for (const key of Object.keys(scans)) {
    const scan = scans[key];
    if (scan?.api_error) {
      return scan;
    }
    // Also detect credit issues from reason_no_trade text
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
  const { toasts, addToast } = useToasts();

  const apiErrorScan = getApiError(scans);

  const fetchScans = useCallback(async () => {
    // (F2) Skip polling when tab is not visible
    if (document.hidden) return;
    try {
      const res = await fetch("/api/scan/latest");
      if (res.ok) {
        setScans(await res.json());
      }
    } catch {
      /* backend not yet started — silent */
    }
  }, []);

  useEffect(() => {
    fetchScans();
    const interval = setInterval(fetchScans, 60_000); // poll every minute
    return () => clearInterval(interval);
  }, [fetchScans]);

  // Update scan info every minute
  useEffect(() => {
    const interval = setInterval(() => setScanInfo(getNextScanInfo()), 60_000);
    return () => clearInterval(interval);
  }, []);

  const triggerScan = async (scanType) => {
    setLoading((prev) => ({ ...prev, [scanType]: true }));
    try {
      const res = await fetch(`/api/scan/trigger/${scanType}`, {
        method: "POST",
      });
      if (res.ok) {
        const result = await res.json();
        setScans((prev) => ({ ...prev, [scanType]: result }));
        if (result.has_trade) {
          addToast(`Trade détecté : ${result.recommendation?.ticker}`, "success");
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
      setLoading((prev) => ({ ...prev, [scanType]: false }));
    }
  };

  return (
    <div>
      {/* API error alert banner */}
      {apiErrorScan && (
        <div className="api-error-banner">
          <strong>API Claude hors service</strong>
          <span>{apiErrorScan.api_error === "AuthenticationError"
            ? "Cle API invalide ou credits epuises. Verifiez ANTHROPIC_API_KEY."
            : apiErrorScan.api_error === "RateLimitError"
            ? "Limite de requetes atteinte. Verifiez vos credits Anthropic."
            : `Erreur API : ${apiErrorScan.reason_no_trade || apiErrorScan.api_error}`
          }</span>
          <span>Les scans ne peuvent pas scorer les news tant que l'API est indisponible.</span>
        </div>
      )}

      {/* (D3) Scan status bar */}
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
            {loading[s.key] ? "Scan en cours..." : `Scan ${s.btn}`}
          </button>
        ))}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
        {SCAN_DEFS.map((s) => (
          <TradeCard key={s.key} scan={scans[s.key]} label={s.label} />
        ))}
      </div>

      {/* (D6) Toast container */}
      {toasts.length > 0 && (
        <div className="toast-container">
          {toasts.map((t) => (
            <div key={t.id} className={`toast ${t.type}`}>
              {t.message}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
