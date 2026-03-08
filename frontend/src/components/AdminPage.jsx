import React, { useState, useCallback } from "react";

/**
 * P3.12: Admin page — exposes infrastructure endpoints:
 * - DB stats (table row counts, sizes)
 * - Infrastructure health check
 * - Manual maintenance trigger (VACUUM, pruning)
 * - Backtest replay trigger
 * - Price archive stats
 */

function AdminPage({ isActive }) {
  const [dbStats, setDbStats] = useState(null);
  const [healthData, setHealthData] = useState(null);
  const [maintenanceResult, setMaintenanceResult] = useState(null);
  const [priceArchiveStats, setPriceArchiveStats] = useState(null);
  const [loading, setLoading] = useState({});
  const [error, setError] = useState(null);

  const fetchWithState = useCallback(async (key, url, setter, method = "GET") => {
    setLoading((prev) => ({ ...prev, [key]: true }));
    setError(null);
    try {
      const opts = method === "POST" ? { method: "POST" } : {};
      const r = await fetch(url, opts);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setter(data);
    } catch (err) {
      setError(`${key}: ${err.message}`);
    } finally {
      setLoading((prev) => ({ ...prev, [key]: false }));
    }
  }, []);

  if (!isActive) return null;

  return (
    <div>
      <div className="page-header">
        <h2>Administration</h2>
        <p className="page-subtitle">Infrastructure, base de données, maintenance</p>
      </div>

      {error && <div className="disconnect-banner">{error}</div>}

      {/* Action buttons */}
      <div className="section-card">
        <h3>Actions</h3>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
          <button
            className="trigger-btn"
            disabled={loading.health}
            onClick={() => fetchWithState("health", "/api/infrastructure/health", setHealthData)}
          >
            {loading.health ? "..." : "Health Check"}
          </button>
          <button
            className="trigger-btn"
            disabled={loading.dbStats}
            onClick={() => fetchWithState("dbStats", "/api/db/stats", setDbStats)}
          >
            {loading.dbStats ? "..." : "DB Stats"}
          </button>
          <button
            className="trigger-btn"
            disabled={loading.archive}
            onClick={() => fetchWithState("archive", "/api/price-archive/stats", setPriceArchiveStats)}
          >
            {loading.archive ? "..." : "Price Archive"}
          </button>
          <button
            className="trigger-btn export"
            disabled={loading.maintenance}
            onClick={() => fetchWithState("maintenance", "/api/infrastructure/maintenance", setMaintenanceResult, "POST")}
          >
            {loading.maintenance ? "..." : "Lancer Maintenance"}
          </button>
        </div>
      </div>

      {/* Health check results */}
      {healthData && (
        <div className="section-card">
          <h3>Infrastructure Health</h3>
          <div className="compact-table-wrap">
            <table className="compact-table">
              <thead>
                <tr>
                  <th>Check</th>
                  <th>Status</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(healthData).map(([key, val]) => (
                  <tr key={key}>
                    <td><strong>{key}</strong></td>
                    <td>
                      <span className={`badge ${
                        val === true || val === "ok" || val === "healthy"
                          ? "badge-tp" : typeof val === "object" ? "badge-pending" : "badge-sl"
                      }`}>
                        {typeof val === "boolean" ? (val ? "OK" : "FAIL") : typeof val === "object" ? "..." : String(val)}
                      </span>
                    </td>
                    <td style={{ fontSize: 12, color: "var(--text-muted)" }}>
                      {typeof val === "object" ? JSON.stringify(val, null, 1) : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* DB Stats */}
      {dbStats && (
        <div className="section-card">
          <h3>Base de données</h3>
          <div className="compact-table-wrap">
            <table className="compact-table">
              <thead>
                <tr>
                  <th>Table</th>
                  <th>Lignes</th>
                  <th>Taille</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(dbStats)
                  .filter(([k]) => k !== "status")
                  .sort(([, a], [, b]) => (b?.row_count || 0) - (a?.row_count || 0))
                  .map(([table, info]) => (
                    <tr key={table}>
                      <td><strong>{table}</strong></td>
                      <td>{info?.row_count ?? info?.estimated_rows ?? "?"}</td>
                      <td style={{ fontSize: 12, color: "var(--text-muted)" }}>
                        {info?.total_size || info?.size || "—"}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Price Archive Stats */}
      {priceArchiveStats && (
        <div className="section-card">
          <h3>Price Archive</h3>
          <div className="compact-table-wrap">
            <table className="compact-table">
              <thead>
                <tr>
                  <th>Info</th>
                  <th>Valeur</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(priceArchiveStats).map(([key, val]) => (
                  <tr key={key}>
                    <td><strong>{key}</strong></td>
                    <td>{typeof val === "object" ? JSON.stringify(val) : String(val)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Maintenance results */}
      {maintenanceResult && (
        <div className="section-card">
          <h3>Résultat Maintenance</h3>
          <div className="compact-table-wrap">
            <table className="compact-table">
              <thead>
                <tr>
                  <th>Table</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(maintenanceResult).map(([table, status]) => (
                  <tr key={table}>
                    <td><strong>{table}</strong></td>
                    <td>
                      <span className={`badge ${status === "ok" ? "badge-tp" : "badge-sl"}`}>
                        {status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

export default AdminPage;
