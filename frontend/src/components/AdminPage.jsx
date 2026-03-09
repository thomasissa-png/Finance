import React, { useState, useCallback, useEffect } from "react";

/**
 * Réglages page — Theme toggle, API list, and infrastructure endpoints.
 */

const API_LIST = [
  { name: "Anthropic (Claude)", type: "key", env: "ANTHROPIC_API_KEY", desc: "Scoring des news via Claude Haiku" },
  { name: "Twelve Data", type: "key", env: "TWELVE_DATA_API_KEY", desc: "Market data temps réel (800 crédits/jour)" },
  { name: "EIA", type: "key", env: "EIA_API_KEY", desc: "Stocks pétrole/gaz/distillats US" },
  { name: "GNews", type: "key", env: "GNEWS_API_KEY", desc: "Recherche news ciblée (100 req/jour)" },
  { name: "USDA NASS", type: "key", env: "USDA_API_KEY", desc: "Crop progress, conditions, récoltes" },
  { name: "GIE AGSI", type: "key", env: "GIE_AGSI_API_KEY", desc: "Stockage gaz européen" },
  { name: "Open-Meteo", type: "free", desc: "Météo 16 zones agricoles critiques" },
  { name: "yfinance", type: "free", desc: "Prix temps réel, options flow, fallback market data" },
  { name: "CFTC COT", type: "free", desc: "Positionnement commerciaux vs spéculateurs" },
  { name: "NASA EONET", type: "free", desc: "Événements naturels (tempêtes, feux, volcans)" },
  { name: "NASA POWER", type: "free", desc: "Données satellite stress végétatif" },
  { name: "WOAH/OIE", type: "free", desc: "Surveillance maladies animales mondiales" },
  { name: "CME FedWatch", type: "free", desc: "Taux implicites Fed Funds futures (via yfinance)" },
  { name: "SHFE/LME Proxy", type: "free", desc: "Proxy inventaires métaux (via yfinance)" },
  { name: "Freight Index", type: "free", desc: "Baltic Dry Index via ETF BDRY" },
  { name: "Chokepoint Monitor", type: "free", desc: "Proxy tankers pour disruptions maritimes" },
  { name: "Dark Pool Signals", type: "free", desc: "Divergence volume/prix sur ETFs majeurs" },
  { name: "Options Flow", type: "free", desc: "Put/call ratio, IV skew (via yfinance)" },
  { name: "RSS Feeds (25)", type: "free", desc: "USDA, NOAA, NHC, FAO, gCaptain, ECB, Fed, BoE, Caixin..." },
];

function AdminPage({ isActive }) {
  const [dbStats, setDbStats] = useState(null);
  const [healthData, setHealthData] = useState(null);
  const [maintenanceResult, setMaintenanceResult] = useState(null);
  const [priceArchiveStats, setPriceArchiveStats] = useState(null);
  const [loading, setLoading] = useState({});
  const [error, setError] = useState(null);
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem("theme") || "dark"; } catch { return "dark"; }
  });

  // Apply theme on mount and change
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("theme", theme); } catch { /* */ }
  }, [theme]);

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
        <h2>Réglages</h2>
        <p className="page-subtitle">Apparence, APIs, infrastructure, maintenance</p>
      </div>

      {error && <div className="disconnect-banner">{error}</div>}

      {/* Theme toggle */}
      <div className="section-card">
        <h3>Apparence</h3>
        <div style={{ marginTop: 12 }}>
          <div className="theme-toggle">
            <button
              className={`theme-toggle-btn ${theme === "light" ? "active" : ""}`}
              onClick={() => setTheme("light")}
            >
              Light
            </button>
            <button
              className={`theme-toggle-btn ${theme === "dark" ? "active" : ""}`}
              onClick={() => setTheme("dark")}
            >
              Dark
            </button>
          </div>
        </div>
      </div>

      {/* API list */}
      <div className="section-card">
        <h3>APIs utilisées</h3>
        <p style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 12 }}>
          {API_LIST.filter((a) => a.type === "key").length} APIs avec clé &middot; {API_LIST.filter((a) => a.type === "free").length} APIs gratuites
        </p>
        <div className="api-list">
          {API_LIST.map((api) => (
            <div className="api-item" key={api.name}>
              <span className="api-item-name">{api.name}</span>
              <span className={`api-item-type ${api.type}`}>
                {api.type === "free" ? "GRATUIT" : api.env}
              </span>
              <span className="api-item-desc">{api.desc}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Infrastructure actions */}
      <div className="section-card">
        <h3>Infrastructure</h3>
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
