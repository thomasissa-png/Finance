import React, { useState, useCallback, useEffect } from "react";

/**
 * Réglages page — Theme toggle, API list, infrastructure tools, DB reset.
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

const INFRA_ACTIONS = [
  {
    key: "health",
    label: "Health Check",
    desc: "Vérifie la connexion PostgreSQL, le pool de connexions, les trades en attente et les fichiers de fallback JSON.",
    url: "/api/infrastructure/health",
    method: "GET",
    style: {},
  },
  {
    key: "dbStats",
    label: "Statistiques DB",
    desc: "Affiche le nombre de lignes et la taille de chaque table PostgreSQL (trades, journal, positions, logs, etc.).",
    url: "/api/db/stats",
    method: "GET",
    style: {},
  },
  {
    key: "archive",
    label: "Archive des prix",
    desc: "Consulte les statistiques de l'archive de prix OHLCV (nombre de tickers, couverture temporelle, entrées).",
    url: "/api/price-archive/stats",
    method: "GET",
    style: {},
  },
  {
    key: "maintenance",
    label: "Lancer la maintenance",
    desc: "Exécute VACUUM ANALYZE sur toutes les tables, purge les vieux logs/messages, et recalcule les statistiques. Peut prendre 30-60 secondes.",
    url: "/api/infrastructure/maintenance",
    method: "POST",
    style: { background: "var(--accent)", color: "#fff", border: "none" },
  },
];

function AdminPage({ isActive }) {
  const [dbStats, setDbStats] = useState(null);
  const [healthData, setHealthData] = useState(null);
  const [maintenanceResult, setMaintenanceResult] = useState(null);
  const [priceArchiveStats, setPriceArchiveStats] = useState(null);
  const [loading, setLoading] = useState({});
  const [error, setError] = useState(null);
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem("theme") || "light"; } catch { return "light"; }
  });

  // Reset state
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [resetPassword, setResetPassword] = useState("");
  const [resetting, setResetting] = useState(false);
  const [resetResult, setResetResult] = useState(null);
  const [resetError, setResetError] = useState(null);

  // API status from backend
  const [apiStatus, setApiStatus] = useState(null);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("theme", theme); } catch { /* */ }
  }, [theme]);

  // Fetch API key status from backend health endpoint
  useEffect(() => {
    if (!isActive) return;
    fetch("/api/infrastructure/health")
      .then((r) => r.ok ? r.json() : null)
      .then((data) => { if (data) setApiStatus(data); })
      .catch(() => {});
  }, [isActive]);

  const setterMap = {
    health: setHealthData,
    dbStats: setDbStats,
    archive: setPriceArchiveStats,
    maintenance: setMaintenanceResult,
  };

  const fetchWithState = useCallback(async (key, url, method = "GET") => {
    setLoading((prev) => ({ ...prev, [key]: true }));
    setError(null);
    try {
      const opts = method === "POST" ? { method: "POST" } : {};
      const r = await fetch(url, opts);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      const setter = setterMap[key];
      if (setter) setter(data);
    } catch (err) {
      setError(`${key}: ${err.message}`);
    } finally {
      setLoading((prev) => ({ ...prev, [key]: false }));
    }
  }, []);

  const doReset = async () => {
    setResetting(true);
    setResetError(null);
    setResetResult(null);
    try {
      const res = await fetch(`/api/infrastructure/reset?password=${encodeURIComponent(resetPassword)}`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        setResetError(data.detail || `Erreur HTTP ${res.status}`);
      } else {
        setResetResult(data);
        setShowResetConfirm(false);
        setResetPassword("");
      }
    } catch (err) {
      setResetError(`Erreur réseau : ${err.message}`);
    }
    setResetting(false);
  };

  if (!isActive) return null;

  const apiKeyed = API_LIST.filter((a) => a.type === "key");
  const apiFree = API_LIST.filter((a) => a.type === "free");

  return (
    <div className="admin-page">
      <div className="page-header">
        <h2>Réglages</h2>
        <p className="page-subtitle">Apparence, APIs, infrastructure, maintenance</p>
      </div>

      {error && <div className="disconnect-banner">{error}</div>}

      {/* Theme toggle */}
      <div className="section-card">
        <h3>Apparence</h3>
        <div className="admin-theme-section">
          <span className="admin-theme-label">Thème</span>
          <div className="theme-toggle">
            <button
              className={`theme-toggle-btn ${theme === "light" ? "active" : ""}`}
              onClick={() => setTheme("light")}
            >
              &#9788; Light
            </button>
            <button
              className={`theme-toggle-btn ${theme === "dark" ? "active" : ""}`}
              onClick={() => setTheme("dark")}
            >
              &#9790; Dark
            </button>
          </div>
        </div>
      </div>

      {/* API list */}
      <div className="section-card">
        <div className="admin-api-header">
          <h3>APIs utilisées</h3>
          <span className="admin-api-counts">
            <span className="admin-api-count key">{apiKeyed.length} avec clé</span>
            <span className="admin-api-count free">{apiFree.length} gratuites</span>
          </span>
        </div>

        <div className="admin-api-group-label">Clé requise</div>
        <div className="admin-api-grid">
          {apiKeyed.map((api) => (
            <div className="admin-api-card" key={api.name}>
              <div className="admin-api-card-top">
                <span className="admin-api-card-name">{api.name}</span>
                <span className="admin-api-badge key">{api.env}</span>
              </div>
              <div className="admin-api-card-desc">{api.desc}</div>
            </div>
          ))}
        </div>

        <div className="admin-api-group-label" style={{ marginTop: 16 }}>Gratuites</div>
        <div className="admin-api-grid">
          {apiFree.map((api) => (
            <div className="admin-api-card" key={api.name}>
              <div className="admin-api-card-top">
                <span className="admin-api-card-name">{api.name}</span>
                <span className="admin-api-badge free">GRATUIT</span>
              </div>
              <div className="admin-api-card-desc">{api.desc}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Infrastructure actions — with descriptions */}
      <div className="section-card">
        <h3>Infrastructure</h3>
        <p className="section-subtitle" style={{ margin: "0 0 16px", fontSize: 13, color: "var(--text-secondary)" }}>
          Outils de diagnostic et de maintenance de la base de données et du système.
        </p>
        <div className="admin-infra-grid">
          {INFRA_ACTIONS.map((action) => (
            <div className="admin-infra-card" key={action.key}>
              <div className="admin-infra-card-top">
                <button
                  className="trigger-btn"
                  style={action.style}
                  disabled={loading[action.key]}
                  onClick={() => fetchWithState(action.key, action.url, action.method)}
                >
                  {loading[action.key] ? "..." : action.label}
                </button>
              </div>
              <div className="admin-infra-card-desc">{action.desc}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Health check results */}
      {healthData && (
        <div className="section-card">
          <h3>Résultat Health Check</h3>
          <div className="compact-table-wrap">
            <table className="compact-table">
              <thead>
                <tr><th>Check</th><th>Status</th><th>Détail</th></tr>
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
                <tr><th>Table</th><th>Lignes</th><th>Taille</th></tr>
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
          <h3>Archive des prix</h3>
          <div className="compact-table-wrap">
            <table className="compact-table">
              <thead>
                <tr><th>Info</th><th>Valeur</th></tr>
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
                <tr><th>Table</th><th>Status</th></tr>
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

      {/* ── Zone dangereuse — tout en bas ── */}
      <div className="section-card" style={{ marginTop: 40, borderColor: "var(--red)", borderWidth: 1, borderStyle: "solid" }}>
        <div className="section-header">
          <span className="section-title" style={{ color: "var(--red)" }}>Zone dangereuse</span>
        </div>
        <div style={{ padding: "12px 16px" }}>
          <p style={{ margin: "0 0 12px", fontSize: 13, color: "var(--text-secondary)" }}>
            Réinitialise toutes les données : trades, journal, positions (Éq. 1-4), scan history, learning configs, logs agents.
            Cette action nécessite le mot de passe défini dans le secret Replit <code>RESET_PASSWORD</code>.
          </p>

          {!showResetConfirm ? (
            <button
              className="trigger-btn"
              style={{ background: "var(--red)", color: "#fff", border: "none" }}
              onClick={() => setShowResetConfirm(true)}
            >
              Réinitialiser toutes les données
            </button>
          ) : (
            <div style={{ background: "rgba(239,68,68,0.08)", borderRadius: 8, padding: 16 }}>
              <p style={{ margin: "0 0 12px", fontWeight: 600, color: "var(--red)" }}>
                Confirmer la réinitialisation complète ?
              </p>
              <p style={{ margin: "0 0 16px", fontSize: 13, color: "var(--text-secondary)" }}>
                Cette action va SUPPRIMER toutes les données de trading (PG + JSON) pour les 4 équipes.
                Les tables seront vidées, les fichiers JSON remis à zéro, les configs learning supprimées.
                <strong> Cette action est irréversible.</strong>
              </p>
              <div style={{ marginBottom: 12 }}>
                <label style={{ display: "block", fontSize: 12, color: "var(--text-secondary)", marginBottom: 4 }}>
                  Mot de passe de réinitialisation
                </label>
                <input
                  type="password"
                  value={resetPassword}
                  onChange={(e) => setResetPassword(e.target.value)}
                  placeholder="Entrer le mot de passe RESET_PASSWORD"
                  style={{
                    width: "100%", maxWidth: 320, padding: "8px 12px",
                    border: "1px solid var(--border)", borderRadius: 6,
                    background: "var(--bg-primary)", color: "var(--text-primary)",
                    fontSize: 14,
                  }}
                  autoComplete="off"
                  onKeyDown={(e) => { if (e.key === "Enter" && resetPassword) doReset(); }}
                />
              </div>
              {resetError && (
                <p style={{ margin: "0 0 12px", fontSize: 13, color: "var(--red)" }}>
                  {resetError}
                </p>
              )}
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  className="trigger-btn"
                  style={{ background: "var(--red)", color: "#fff", border: "none", opacity: resetting || !resetPassword ? 0.6 : 1 }}
                  onClick={doReset}
                  disabled={resetting || !resetPassword}
                >
                  {resetting ? "Réinitialisation en cours..." : "Oui, tout supprimer"}
                </button>
                <button
                  className="trigger-btn"
                  style={{ background: "var(--bg-secondary)", color: "var(--text-primary)" }}
                  onClick={() => { setShowResetConfirm(false); setResetPassword(""); setResetError(null); setResetResult(null); }}
                  disabled={resetting}
                >
                  Annuler
                </button>
              </div>
            </div>
          )}
          {resetResult && (
            <div style={{ marginTop: 12, padding: 12, borderRadius: 6, background: "rgba(16,185,129,0.1)", border: "1px solid var(--green)" }}>
              <strong style={{ color: "var(--green)" }}>Réinitialisation réussie</strong>
              <details style={{ marginTop: 8, fontSize: 12, color: "var(--text-secondary)" }}>
                <summary style={{ cursor: "pointer" }}>Détails du reset</summary>
                <pre style={{ whiteSpace: "pre-wrap", marginTop: 8, background: "var(--bg-secondary)", padding: 8, borderRadius: 6, maxHeight: 200, overflow: "auto" }}>
                  {JSON.stringify(resetResult.results, null, 2)}
                </pre>
              </details>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default AdminPage;
