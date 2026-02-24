import React, { useEffect, useState, useCallback } from "react";
import TradeCard from "./TradeCard";

export default function Dashboard() {
  const [scans, setScans] = useState({});
  const [loading, setLoading] = useState({});

  const fetchScans = useCallback(async () => {
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

  const triggerScan = async (scanType) => {
    setLoading((prev) => ({ ...prev, [scanType]: true }));
    try {
      const res = await fetch(`/api/scan/trigger/${scanType}`, {
        method: "POST",
      });
      if (res.ok) {
        const result = await res.json();
        setScans((prev) => ({ ...prev, [scanType]: result }));
      }
    } catch (err) {
      console.error("Trigger failed:", err);
    } finally {
      setLoading((prev) => ({ ...prev, [scanType]: false }));
    }
  };

  return (
    <div>
      <div className="trigger-section">
        <button
          className="trigger-btn"
          onClick={() => triggerScan("europe")}
          disabled={loading.europe}
        >
          {loading.europe ? "Scan en cours..." : "Scan Europe (07:50)"}
        </button>
        <button
          className="trigger-btn"
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
    </div>
  );
}
