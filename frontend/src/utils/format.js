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
