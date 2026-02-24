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

// (D3) Compute next scan time
function getNextScanInfo() {
  const now = new Date();
  const day = now.getDay();
  // Weekend
  if (day === 0 || day === 6) {
    return { text: "Prochain scan : lundi 07:50 CET", cls: "weekend" };
  }
  const h = now.getHours();
  const m = now.getMinutes();
  const totalMin = h * 60 + m;
  if (totalMin < 7 * 60 + 50) {
    return { text: "Prochain scan : aujourd'hui 07:50 CET (Europe)", cls: "online" };
  }
  if (totalMin < 14 * 60 + 30) {
    return { text: "Prochain scan : aujourd'hui 14:30 CET (US)", cls: "online" };
  }
  if (day === 5) {
    // Friday after 14:30
    return { text: "Prochain scan : lundi 07:50 CET", cls: "offline" };
  }
  return { text: "Prochain scan : demain 07:50 CET (Europe)", cls: "offline" };
}

export default function Dashboard() {
  const [scans, setScans] = useState({});
  const [loading, setLoading] = useState({});
  const [scanInfo, setScanInfo] = useState(getNextScanInfo);
  const { toasts, addToast } = useToasts();

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
          addToast(`Trade detecte : ${result.recommendation?.ticker}`, "success");
        } else {
          addToast(`Scan ${scanType} termine — pas de trade`, "warning");
        }
      } else {
        const err = await res.json().catch(() => ({}));
        addToast(err.detail || `Erreur scan ${scanType}`, "error");
      }
    } catch (err) {
      console.error("Trigger failed:", err);
      addToast("Erreur reseau", "error");
    } finally {
      setLoading((prev) => ({ ...prev, [scanType]: false }));
    }
  };

  return (
    <div>
      {/* (D3) Scan status bar */}
      <div className="scan-status-bar">
        <span className={`status-dot ${scanInfo.cls}`} />
        {scanInfo.text}
      </div>

      <div className="trigger-section">
        <button
          className="trigger-btn europe"
          onClick={() => triggerScan("europe")}
          disabled={loading.europe}
        >
          {loading.europe ? "Scan en cours..." : "Scan Europe (07:50)"}
        </button>
        <button
          className="trigger-btn us"
          onClick={() => triggerScan("us")}
          disabled={loading.us}
        >
          {loading.us ? "Scan en cours..." : "Scan US (14:30)"}
        </button>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
        <TradeCard scan={scans.europe} label="SCAN EUROPE — 07:50 CET" />
        <TradeCard scan={scans.us} label="SCAN US — 14:30 CET" />
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
