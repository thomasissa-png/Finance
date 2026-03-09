/**
 * Shared formatting utilities (M4 — DRY, single source of truth).
 */

// ── Result labels ─────────────────────────────────────────────
export const RESULT_LABELS = {
  TP_HIT: { label: "TP", cls: "tp" },
  SL_HIT: { label: "SL", cls: "sl" },
  EXPIRED: { label: "EXP", cls: "expired" },
  PENDING: { label: "...", cls: "pending" },
};

// ── Ticker readable names ─────────────────────────────────────
export const TICKER_NAMES = {
  // Forex
  "EURUSD=X": "EUR/USD",
  "GBPUSD=X": "GBP/USD",
  "USDJPY=X": "USD/JPY",
  "AUDUSD=X": "AUD/USD",
  "USDCHF=X": "USD/CHF",
  "EURJPY=X": "EUR/JPY",
  "USDCNH=X": "USD/CNH",
  // Indices
  "^GSPC": "S&P 500",
  "^DJI": "Dow Jones",
  "^IXIC": "Nasdaq",
  "^RUT": "Russell 2000",
  "^FCHI": "CAC 40",
  "^GDAXI": "DAX",
  "^FTSE": "FTSE 100",
  "^N225": "Nikkei 225",
  "^VIX": "VIX",
  // Metaux
  "GC=F": "Or (Gold)",
  "SI=F": "Argent (Silver)",
  "HG=F": "Cuivre (Copper)",
  "PL=F": "Platine",
  "PA=F": "Palladium",
  // Energie
  "CL=F": "Pétrole WTI",
  "BZ=F": "Brent",
  "NG=F": "Gaz naturel",
  // Agriculture
  "ZC=F": "Maïs (Corn)",
  "ZW=F": "Blé (Wheat)",
  "ZS=F": "Soja (Soybeans)",
  // Soft commodities
  "KC=F": "Café (Coffee)",
  "CC=F": "Cacao (Cocoa)",
  "SB=F": "Sucre (Sugar)",
  "CT=F": "Coton (Cotton)",
  "OJ=F": "Jus d'orange (OJ)",
  // Livestock
  "LE=F": "Bétail (Cattle)",
  "HE=F": "Porc (Hogs)",
  // Actions Europe
  "TTE.PA": "TotalEnergies",
  "MC.PA": "LVMH",
  "BNP.PA": "BNP Paribas",
  "RMS.PA": "Hermès",
  "OR.PA": "L'Oréal",
  "SAN.PA": "Sanofi",
  "AI.PA": "Air Liquide",
  // Actions US
  "AAPL": "Apple",
  "MSFT": "Microsoft",
  "TSLA": "Tesla",
  "AMZN": "Amazon",
  // ETFs
  "URA": "Uranium ETF",
};

export function tickerName(ticker) {
  return TICKER_NAMES[ticker] || ticker;
}

// ── Category colors (O9 — centralized palette) ───────────────
export const CATEGORY_COLORS = {
  weather: "#4fc3f7",
  commodity: "#ffb74d",
  geopolitical: "#ef5350",
  supply_chain: "#ab47bc",
  sector: "#66bb6a",
  regulatory: "#78909c",
  macro: "#9e9e9e",
  earnings: "#757575",
  central_bank_subtle: "#8d6e63",
  m_a: "#5c6bc0",
  other: "#90a4ae",
};

// ── Scan type labels (O3 — full labels) ──────────────────────
export const SCAN_LABELS = {
  europe: "Europe 07:50",
  mid_session: "Mid-Session 11:15",
  us: "Pre-US 14:50",
  us_session: "US 17:00",
};

export function scanLabel(scanType) {
  return SCAN_LABELS[scanType] || scanType;
}

// ── Date/Time formatting ─────────────────────────────────────
export function formatDate(iso) {
  if (!iso) return "--";
  return new Date(iso).toLocaleDateString("fr-FR", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export function formatDateTime(iso) {
  if (!iso) return "--";
  return new Date(iso).toLocaleDateString("fr-FR", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatTime(iso) {
  if (!iso) return "--";
  return new Date(iso).toLocaleTimeString("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ── Relative time (O1 — "il y a X min") ──────────────────────
export function timeAgo(date) {
  if (!date) return "";
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return "à l'instant";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `il y a ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return `il y a ${hours}h${minutes % 60 > 0 ? String(minutes % 60).padStart(2, "0") : ""}`;
}

// ── P&L coloring (D14) ───────────────────────────────────────
export function pnlColor(val) {
  if (val == null) return "var(--text-muted)";
  if (Math.abs(val) < 0.05) return "var(--text-muted)";
  if (val > 0) return "var(--green)";
  if (val < 0) return "var(--red)";
  return "var(--text-muted)";
}

export function pnlClass(val) {
  if (val == null) return "";
  if (Math.abs(val) < 0.05) return "pnl-flat";
  return "";
}

// ── Score coloring ───────────────────────────────────────────
export function scoreColor(score) {
  if (score >= 70) return "var(--green)";
  if (score >= 50) return "var(--yellow)";
  return "var(--red)";
}

// ── Confidence coloring + context (N5) ───────────────────────
export function confidenceColor(val) {
  if (val >= 70) return "var(--green)";
  if (val >= 50) return "var(--yellow)";
  return "var(--red)";
}

export function confidenceLabel(val) {
  if (val >= 70) return "Fort";
  if (val >= 50) return "Moyen";
  return "Faible";
}

// ── Format P&L value ─────────────────────────────────────────
export function formatPnl(val) {
  if (val == null) return "--";
  return `${val > 0 ? "+" : ""}${val}%`;
}

// ── Pagination helper ────────────────────────────────────────
export const PAGE_SIZE = 20;

export function paginate(items, page) {
  const start = (page - 1) * PAGE_SIZE;
  return items.slice(start, start + PAGE_SIZE);
}

export function totalPages(items) {
  return Math.max(1, Math.ceil(items.length / PAGE_SIZE));
}

// ── Paris timezone helper (M7) ───────────────────────────────
export function getParisHour() {
  const paris = new Date().toLocaleString("en-US", { timeZone: "Europe/Paris" });
  return new Date(paris).getHours();
}

export function getParisDay() {
  const paris = new Date().toLocaleString("en-US", { timeZone: "Europe/Paris" });
  return new Date(paris).getDay();
}

// ── Time ago formatting ──────────────────────────────────────
export function formatTimeAgo(isoString) {
  if (!isoString) return "";
  const now = Date.now();
  const then = new Date(isoString).getTime();
  const diff = Math.max(0, now - then);
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}min`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}j`;
}

// ── Intelligent price formatting ─────────────────────────────
// Forex: 4-5 decimals, Indices: 0-1 decimals, Commodities: 2
const FOREX_TICKERS = new Set([
  "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X",
  "USDCHF=X", "EURJPY=X", "USDCNH=X",
]);
const INDEX_TICKERS = new Set([
  "^GSPC", "^DJI", "^IXIC", "^RUT", "^FCHI",
  "^GDAXI", "^FTSE", "^N225", "^VIX",
]);

export function formatPrice(price, ticker) {
  if (price == null) return "--";
  const p = Number(price);
  if (isNaN(p)) return "--";
  if (FOREX_TICKERS.has(ticker)) {
    // JPY pairs: 3 decimals, others: 5
    return ticker.includes("JPY") || ticker.includes("CNH")
      ? p.toFixed(3) : p.toFixed(5);
  }
  if (INDEX_TICKERS.has(ticker)) {
    return p >= 1000 ? p.toFixed(0) : p.toFixed(1);
  }
  // Commodities, stocks: 2 decimals
  return p.toFixed(2);
}

// ── Log detail formatter (human-readable instead of raw JSON) ─
export function formatLogDetails(details) {
  if (!details || typeof details !== "object") return null;
  const parts = [];
  for (const [k, v] of Object.entries(details)) {
    if (k === "news_items") continue; // handled separately
    if (v == null) continue;
    let display;
    if (typeof v === "number") {
      display = Number.isInteger(v) ? String(v) : v.toFixed(2);
    } else if (typeof v === "boolean") {
      display = v ? "oui" : "non";
    } else if (Array.isArray(v)) {
      display = v.length > 3 ? `${v.slice(0, 3).join(", ")}... (${v.length})` : v.join(", ");
    } else if (typeof v === "object") {
      const keys = Object.keys(v);
      display = keys.length > 3 ? `{${keys.slice(0, 3).join(", ")}...}` : JSON.stringify(v);
    } else {
      display = String(v).slice(0, 100);
    }
    parts.push({ key: k.replace(/_/g, " "), value: display });
  }
  return parts;
}
