/**
 * Shared constants — single source of truth (eliminates 600+ lines of duplication).
 */

// ── Polling intervals ────────────────────────────────────────
export const POLL_FAST = 30_000;      // Positions, agents status
export const POLL_NORMAL = 60_000;    // Scoring, journal, learning pages
export const POLL_SLOW = 120_000;     // Performance, reports

// ── Display limits ───────────────────────────────────────────
export const LOG_DISPLAY_LIMIT = 20;
export const NEWS_DISPLAY_LIMIT = 30;
export const DEFAULT_PAGE_SIZE = 15;

// ── Team config ──────────────────────────────────────────────
export const TEAM_COLORS = {
  "1": "var(--accent)",
  "2": "#F59E0B",
  "3": "#8B5CF6",
  "4": "#EC4899",
};

export const TEAM_LABELS = {
  "1": "Eq. 1 — Day Trading",
  "2": "Eq. 2 — Tendance",
  "3": "Eq. 3 — Technique",
  "4": "Eq. 4 — Meta",
};

// ── Log level styling ────────────────────────────────────────
export const LEVEL_ICONS = {
  INFO: "i",
  WARN: "!",
  ERROR: "x",
  DECISION: ">",
};

export const LEVEL_COLORS = {
  INFO: "var(--text-secondary)",
  WARN: "var(--yellow)",
  ERROR: "var(--red)",
  DECISION: "var(--accent)",
};

// ── Direction styling ────────────────────────────────────────
export const DIR_COLORS = {
  LONG: "var(--green)",
  SHORT: "var(--red)",
  NEUTRAL: "var(--text-muted)",
  FLAT: "var(--text-muted)",
};

export const DIR_ARROWS = {
  LONG: "\u2191",
  SHORT: "\u2193",
  NEUTRAL: "\u2022",
  FLAT: "\u2014",
};

// ── Recharts tooltip style (theme-aware) ─────────────────────
export const CHART_TOOLTIP_STYLE = {
  background: "var(--bg-card)",
  border: "1px solid var(--border)",
  borderRadius: 6,
  fontSize: 12,
  color: "var(--text-primary)",
};

// ── Agent labels (used in notifications, audit, etc.) ────────
export const AGENT_LABELS = {
  news: "News",
  scoring: "Scoring 1",
  scoring_2: "Scoring 2",
  scoring_3: "Scoring 3",
  scoring_4: "Scoring 4",
  trader_1: "Trader 1",
  trader_2: "Trader 2",
  trader_3: "Trader 3",
  trader_4: "Trader 4",
  journal: "Journal 1",
  journal_2: "Journal 2",
  journal_3: "Journal 3",
  journal_4: "Journal 4",
  learning: "Learning 1",
  learning_2: "Learning 2",
  learning_3: "Learning 3",
  learning_4: "Learning 4",
  auditor: "Auditeur",
  infrastructure: "Infrastructure",
  performance: "Performance",
};

// ── Team 4 inner team styling (contributing teams) ───────────
export const T4_TEAM_COLORS = {
  team_1: "var(--accent)",
  team_2: "var(--green)",
  team_3: "var(--yellow)",
};

export const T4_TEAM_LABELS = {
  team_1: "News (1)",
  team_2: "Trend (2)",
  team_3: "Tech (3)",
};

// ── Agent to page mapping ────────────────────────────────────
export const AGENT_TO_PAGE = {
  news: "news",
  scoring: "team1",
  scoring_2: "team2",
  scoring_3: "team3",
  scoring_4: "team4",
  trader_1: "team1",
  trader_2: "team2",
  trader_3: "team3",
  trader_4: "team4",
  journal: "team1",
  journal_2: "team2",
  journal_3: "team3",
  journal_4: "team4",
  learning: "team1",
  learning_2: "team2",
  learning_3: "team3",
  learning_4: "team4",
  auditor: "auditor",
  infrastructure: "performance",
  performance: "performance",
};
