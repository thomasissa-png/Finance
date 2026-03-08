import React, { useEffect, useState, useCallback } from "react";

// ── Agent definitions ────────────────────────────────────────────────
// Shared source of truth for all agents, their teams, missions, and relationships

const TEAM_DEFS = [
  { id: "shared", label: "Agents Partagés", desc: "Alimentent toutes les équipes" },
  { id: "team1", label: "Équipe 1 Intraday", desc: "Day trading event-driven, 0-1 trade par scan" },
  { id: "team2", label: "Équipe 2 Tendance", desc: "Trend following commodities, positions longue durée" },
  { id: "team3", label: "Équipe 3 Technique", desc: "Trading sur indicateurs techniques, heures à 3 jours" },
  { id: "team4", label: "Équipe 4 Meta", desc: "Ensemble multi-signal, confluence teams 1-3" },
  { id: "infra", label: "Infrastructure & Suivi", desc: "Monitoring, audit, performance" },
];

const AGENT_DEFS = {
  news:           { team: "shared", icon: "\ud83d\udce1", label: "News",          role: "Collecte RSS, structured data, GNews, event detection", schedule: "4 scans/jour + event q10min", tokens: false },
  scoring:        { team: "shared", icon: "\ud83c\udfaf", label: "Scoring",       role: "Score Claude API, edge formula, chain reactions", schedule: "4 scans/jour", tokens: true },
  scoring_2:      { team: "team2",  icon: "\ud83d\udccf", label: "Scoring 2",     role: "Re-pondération trend, multiplicateurs structurels, accumulation", schedule: "4 scans/jour (après Scoring)", tokens: false },
  trader_1:       { team: "team1",  icon: "\ud83d\udcb9", label: "Trader 1",      role: "Décision trade intraday, TP/SL, risk management", schedule: "4 scans/jour", tokens: false },
  trader_2:       { team: "team2",  icon: "\ud83d\udcc8", label: "Trader 2",      role: "Trend following 4 commodities, positions longue durée", schedule: "4 scans/jour", tokens: false },
  journal:        { team: "team1",  icon: "\ud83d\udcd3", label: "Journal 1",     role: "Clôture trades 22h, P&L, MAE/MFE, slippage", schedule: "22h quotidien", tokens: false },
  journal_2:      { team: "team2",  icon: "\ud83d\udcd4", label: "Journal 2",     role: "Journal des flips Trader 2, MAE/MFE daily bars", schedule: "22h (après J1)", tokens: false },
  learning:       { team: "team1",  icon: "\ud83e\udde0", label: "Learning 1",    role: "6 dimensions ML, anomaly detection, feedback Claude", schedule: "Post-journal 22h", tokens: false },
  learning_2:     { team: "team2",  icon: "\ud83d\udca1", label: "Learning 2",    role: "4 dimensions trend, calibration seuil, churning detection", schedule: "Post-journal 2", tokens: false },
  scoring_3:      { team: "team3",  icon: "\ud83d\udcc9", label: "Scoring 3",     role: "Indicateurs techniques (RSI, MACD, Bollinger, ADX), 20 tickers", schedule: "4 scans/jour", tokens: false },
  scoring_4:      { team: "team4",  icon: "\ud83e\udde9", label: "Scoring 4",     role: "Meta-scoring, combinaison news+trend+tech, confluence", schedule: "4 scans/jour", tokens: false },
  trader_3:       { team: "team3",  icon: "\ud83d\udcc0", label: "Trader 3",      role: "Multi-position technique, A/B testing, heures à 3 jours", schedule: "4 scans/jour", tokens: false },
  trader_4:       { team: "team4",  icon: "\ud83c\udfb0", label: "Trader 4",      role: "Ensemble confluence-driven, positions quand 2+/3 teams s'accordent", schedule: "4 scans/jour", tokens: false },
  journal_3:      { team: "team3",  icon: "\ud83d\udcd2", label: "Journal 3",     role: "Journal positions techniques, MAE/MFE, A/B par stratégie", schedule: "22h (après J2)", tokens: false },
  journal_4:      { team: "team4",  icon: "\ud83d\udcd5", label: "Journal 4",     role: "Journal positions meta, analyse confluence accuracy", schedule: "22h (après J3)", tokens: false },
  learning_3:     { team: "team3",  icon: "\ud83e\uddea", label: "Learning 3",    role: "3 dimensions (strategy, ticker, timeframe), A/B ranking", schedule: "Post-journal 3", tokens: false },
  learning_4:     { team: "team4",  icon: "\ud83e\uddf2", label: "Learning 4",    role: "3 dimensions + weight optimization, confluence quality", schedule: "Post-journal 4", tokens: false },
  infrastructure: { team: "infra",  icon: "\ud83d\udee0\ufe0f", label: "Infrastructure", role: "Santé PG, VACUUM, pruning, divergence JSON/PG", schedule: "q15min + 23h + dim 21h", tokens: false },
  performance:    { team: "infra",  icon: "\ud83d\udcca", label: "Performance",   role: "KPIs tous agents, tendances, ranking, alertes", schedule: "Horaire + 22h30 + dim 21h30", tokens: false },
  auditor:        { team: "infra",  icon: "\ud83d\udd0d", label: "Auditeur",      role: "Audit profondeur, note /10, 14 profils", schedule: "Manuel", tokens: false },
  ux:             { team: "infra",  icon: "\ud83c\udfa8", label: "UX",            role: "Frontend React, navigation, notifications", schedule: "Continu", tokens: false },
};

// Data flow connections between agents
const PIPELINE_FLOWS = [
  { from: "news", to: "scoring", label: "news_items" },
  { from: "scoring", to: "trader_1", label: "scored_news" },
  { from: "scoring", to: "scoring_2", label: "scored_news" },
  { from: "scoring_2", to: "trader_2", label: "trend_scored" },
  { from: "learning", to: "trader_1", label: "adjustments" },
  { from: "learning_2", to: "trader_2", label: "adjustments" },
  { from: "journal", to: "learning", label: "trade_results" },
  { from: "journal_2", to: "learning_2", label: "flip_results" },
  { from: "scoring_3", to: "trader_3", label: "tech_setups" },
  { from: "scoring", to: "scoring_4", label: "scored_news" },
  { from: "scoring_2", to: "scoring_4", label: "trend_scored" },
  { from: "scoring_3", to: "scoring_4", label: "tech_scored" },
  { from: "scoring_4", to: "trader_4", label: "meta_scored" },
  { from: "learning_3", to: "trader_3", label: "adjustments" },
  { from: "learning_4", to: "trader_4", label: "adjustments" },
  { from: "journal_3", to: "learning_3", label: "trade_results" },
  { from: "journal_4", to: "learning_4", label: "meta_results" },
  { from: "performance", to: "ux", label: "kpis" },
  { from: "auditor", to: "ux", label: "reports" },
];

// ── Status helpers ────────────────────────────────────────────────────

const STATUS_MAP = {
  idle: { cls: "idle", label: "En attente" },
  working: { cls: "working", label: "Actif" },
  error: { cls: "error", label: "Erreur" },
};

function AgentNode({ agent, def, onClick }) {
  const st = STATUS_MAP[agent?.status] || STATUS_MAP.idle;
  const version = agent?.version;
  const metrics = agent?.metrics || {};
  const lastAction = agent?.last_action;

  return (
    <div className={`team-agent-node ${st.cls}`} onClick={() => onClick && onClick(agent?.name)} role="button" tabIndex={0}>
      <div className="team-agent-header">
        <span className="team-agent-icon">{def.icon}</span>
        <div className="team-agent-info">
          <span className="team-agent-name">
            {def.label}
            {version && <span className="team-agent-version">v{version}</span>}
          </span>
          <span className="team-agent-role">{def.role}</span>
        </div>
        <span className={`team-agent-status-dot ${st.cls}`} title={st.label} />
      </div>
      {def.schedule && (
        <div className="team-agent-schedule">
          <span className="team-agent-schedule-icon">{"\u23f0"}</span>
          {def.schedule}
        </div>
      )}
      {def.tokens && metrics.total_tokens_used != null && (
        <div className="team-agent-tokens">
          <span className="team-agent-tokens-icon">{"\ud83e\udee7"}</span>
          {(metrics.total_tokens_used / 1000).toFixed(1)}k tokens
        </div>
      )}
      {lastAction && agent?.status !== "working" && (
        <div className="team-agent-last-action">{lastAction.slice(0, 60)}</div>
      )}
      {agent?.status === "working" && lastAction && (
        <div className="team-agent-working-action">
          <span className="spinner spinner-xs" /> {lastAction.slice(0, 50)}
        </div>
      )}
    </div>
  );
}

function TeamSection({ team, agents, agentMap, onNavigate }) {
  const teamAgents = Object.entries(AGENT_DEFS).filter(([, d]) => d.team === team.id);
  if (teamAgents.length === 0) return null;

  return (
    <div className="team-section">
      <div className="team-section-header">
        <h3 className="team-section-title">{team.label}</h3>
        <span className="team-section-desc">{team.desc}</span>
      </div>
      <div className="team-agents-grid">
        {teamAgents.map(([name, def]) => (
          <AgentNode
            key={name}
            agent={agentMap[name]}
            def={def}
            onClick={() => {
              // Navigate to agent page
              const pageMap = {
                news: "news", scoring: "scoring", scoring_2: "scoring2",
                scoring_3: "scoring3", scoring_4: "scoring4",
                trader_1: "trader", trader_2: "trader2",
                trader_3: "trader3", trader_4: "trader4",
                journal: "journal", journal_2: "journal2",
                journal_3: "journal3", journal_4: "journal4",
                learning: "learning", learning_2: "learning2",
                learning_3: "learning3", learning_4: "learning4",
                auditor: "auditor",
              };
              const page = pageMap[name];
              if (page && onNavigate) onNavigate(page);
            }}
          />
        ))}
      </div>
    </div>
  );
}

function PipelineDiagram() {
  return (
    <div className="team-pipeline">
      <h3 className="team-pipeline-title">Pipeline de scan (4x/jour)</h3>
      <div className="team-pipeline-flow">
        <div className="pipeline-step shared">
          <span className="pipeline-icon">{"\ud83d\udce1"}</span>
          <span>News</span>
        </div>
        <span className="pipeline-arrow">{"\u2192"}</span>
        <div className="pipeline-step shared">
          <span className="pipeline-icon">{"\ud83c\udfaf"}</span>
          <span>Scoring</span>
        </div>
        <span className="pipeline-arrow">{"\u2192"}</span>
        <div className="pipeline-branch">
          <div className="pipeline-branch-arm team1">
            <span className="pipeline-arrow">{"\u2192"}</span>
            <div className="pipeline-step team1">
              <span className="pipeline-icon">{"\ud83e\udde0"}</span>
              <span>Learning 1</span>
            </div>
            <span className="pipeline-arrow">{"\u2192"}</span>
            <div className="pipeline-step team1">
              <span className="pipeline-icon">{"\ud83d\udcb9"}</span>
              <span>Trader 1</span>
            </div>
          </div>
          <div className="pipeline-branch-arm team2">
            <span className="pipeline-arrow">{"\u2192"}</span>
            <div className="pipeline-step team2">
              <span className="pipeline-icon">{"\ud83d\udccf"}</span>
              <span>Scoring 2</span>
            </div>
            <span className="pipeline-arrow">{"\u2192"}</span>
            <div className="pipeline-step team2">
              <span className="pipeline-icon">{"\ud83d\udca1"}</span>
              <span>Learning 2</span>
            </div>
            <span className="pipeline-arrow">{"\u2192"}</span>
            <div className="pipeline-step team2">
              <span className="pipeline-icon">{"\ud83d\udcc8"}</span>
              <span>Trader 2</span>
            </div>
          </div>
          <div className="pipeline-branch-arm team3">
            <span className="pipeline-arrow">{"\u2192"}</span>
            <div className="pipeline-step team3">
              <span className="pipeline-icon">{"\ud83d\udcc9"}</span>
              <span>Scoring 3</span>
            </div>
            <span className="pipeline-arrow">{"\u2192"}</span>
            <div className="pipeline-step team3">
              <span className="pipeline-icon">{"\ud83e\uddea"}</span>
              <span>Learning 3</span>
            </div>
            <span className="pipeline-arrow">{"\u2192"}</span>
            <div className="pipeline-step team3">
              <span className="pipeline-icon">{"\ud83d\udcc0"}</span>
              <span>Trader 3</span>
            </div>
          </div>
          <div className="pipeline-branch-arm team4">
            <span className="pipeline-arrow">{"\u2192"}</span>
            <div className="pipeline-step team4">
              <span className="pipeline-icon">{"\ud83e\udde9"}</span>
              <span>Scoring 4</span>
            </div>
            <span className="pipeline-arrow">{"\u2192"}</span>
            <div className="pipeline-step team4">
              <span className="pipeline-icon">{"\ud83e\uddf2"}</span>
              <span>Learning 4</span>
            </div>
            <span className="pipeline-arrow">{"\u2192"}</span>
            <div className="pipeline-step team4">
              <span className="pipeline-icon">{"\ud83c\udfb0"}</span>
              <span>Trader 4</span>
            </div>
          </div>
        </div>
      </div>

      <h3 className="team-pipeline-title" style={{ marginTop: 20 }}>Pipeline quotidien (22h CET)</h3>
      <div className="team-pipeline-flow">
        <div className="pipeline-step team1">
          <span className="pipeline-icon">{"\ud83d\udcd3"}</span>
          <span>Journal 1</span>
        </div>
        <span className="pipeline-arrow">{"\u2192"}</span>
        <div className="pipeline-step team1">
          <span className="pipeline-icon">{"\ud83e\udde0"}</span>
          <span>Learning 1</span>
        </div>
        <span className="pipeline-arrow">{"\u2192"}</span>
        <div className="pipeline-step team2">
          <span className="pipeline-icon">{"\ud83d\udcd4"}</span>
          <span>Journal 2</span>
        </div>
        <span className="pipeline-arrow">{"\u2192"}</span>
        <div className="pipeline-step team2">
          <span className="pipeline-icon">{"\ud83d\udca1"}</span>
          <span>Learning 2</span>
        </div>
        <span className="pipeline-arrow">{"\u2192"}</span>
        <div className="pipeline-step team3">
          <span className="pipeline-icon">{"\ud83d\udcd2"}</span>
          <span>Journal 3</span>
        </div>
        <span className="pipeline-arrow">{"\u2192"}</span>
        <div className="pipeline-step team3">
          <span className="pipeline-icon">{"\ud83e\uddea"}</span>
          <span>Learning 3</span>
        </div>
        <span className="pipeline-arrow">{"\u2192"}</span>
        <div className="pipeline-step team4">
          <span className="pipeline-icon">{"\ud83d\udcd5"}</span>
          <span>Journal 4</span>
        </div>
        <span className="pipeline-arrow">{"\u2192"}</span>
        <div className="pipeline-step team4">
          <span className="pipeline-icon">{"\ud83e\uddf2"}</span>
          <span>Learning 4</span>
        </div>
        <span className="pipeline-arrow">{"\u2192"}</span>
        <div className="pipeline-step infra">
          <span className="pipeline-icon">{"\ud83d\udcca"}</span>
          <span>Performance</span>
          <span className="pipeline-time">22h30</span>
        </div>
        <span className="pipeline-arrow">{"\u2192"}</span>
        <div className="pipeline-step infra">
          <span className="pipeline-icon">{"\ud83d\udee0\ufe0f"}</span>
          <span>Maintenance</span>
          <span className="pipeline-time">23h</span>
        </div>
      </div>
    </div>
  );
}

function GlobalKPIs({ agents, perfData }) {
  const agentMap = {};
  (agents || []).forEach((a) => { agentMap[a.name] = a; });

  const workingCount = agents.filter((a) => a.status === "working").length;
  const errorCount = agents.filter((a) => a.status === "error").length;
  const totalAgents = agents.length;

  // Token usage from scoring agent
  const scoringMetrics = agentMap.scoring?.metrics || {};
  const tokensK = scoringMetrics.total_tokens_used
    ? (scoringMetrics.total_tokens_used / 1000).toFixed(1)
    : "0";

  // Performance from daily report
  const t1 = perfData?.trader_1 || {};
  const t2 = perfData?.trader_2 || {};

  return (
    <div className="team-global-kpis">
      <div className="team-kpi">
        <div className="team-kpi-value">{totalAgents}</div>
        <div className="team-kpi-label">Agents actifs</div>
      </div>
      <div className="team-kpi">
        <div className="team-kpi-value" style={{ color: workingCount > 0 ? "var(--cyan)" : undefined }}>
          {workingCount}
        </div>
        <div className="team-kpi-label">En cours</div>
      </div>
      <div className="team-kpi">
        <div className="team-kpi-value" style={{ color: errorCount > 0 ? "var(--red)" : "var(--green)" }}>
          {errorCount}
        </div>
        <div className="team-kpi-label">Erreurs</div>
      </div>
      <div className="team-kpi">
        <div className="team-kpi-value">{tokensK}k</div>
        <div className="team-kpi-label">Tokens (session)</div>
      </div>
      {t1.win_rate != null && (
        <div className="team-kpi">
          <div className="team-kpi-value" style={{ color: t1.win_rate >= 50 ? "var(--green)" : "var(--red)" }}>
            {t1.win_rate.toFixed(1)}%
          </div>
          <div className="team-kpi-label">WR Trader 1</div>
        </div>
      )}
      {t2.flip_win_rate != null && (
        <div className="team-kpi">
          <div className="team-kpi-value" style={{ color: t2.flip_win_rate >= 50 ? "var(--green)" : "var(--red)" }}>
            {t2.flip_win_rate.toFixed(1)}%
          </div>
          <div className="team-kpi-label">WR Trader 2</div>
        </div>
      )}
    </div>
  );
}

function ScheduleTimeline() {
  const events = [
    { time: "07:50", label: "Scan Europe", team: "shared" },
    { time: "q10min", label: "Event Check", team: "shared", subtle: true },
    { time: "q15min", label: "Position Monitor", team: "team1", subtle: true },
    { time: "q15min", label: "Health Check", team: "infra", subtle: true },
    { time: "11:15", label: "Scan Mid-Session", team: "shared" },
    { time: "14:50", label: "Scan Pre-US", team: "shared" },
    { time: "16:45 mer", label: "Post-EIA", team: "shared" },
    { time: "17:00", label: "Scan US Session", team: "shared" },
    { time: "Horaire", label: "KPI Snapshot", team: "infra", subtle: true },
    { time: "22:00", label: "Journal 1 + Learning 1", team: "team1" },
    { time: "22:00", label: "Journal 2 + Learning 2", team: "team2" },
    { time: "22:00", label: "Journal 3 + Learning 3", team: "team3" },
    { time: "22:00", label: "Journal 4 + Learning 4", team: "team4" },
    { time: "22:30", label: "Performance Daily", team: "infra" },
    { time: "23:00", label: "Maintenance", team: "infra" },
    { time: "dim 20:00", label: "Source Review", team: "shared" },
    { time: "dim 21:00", label: "Infra Report", team: "infra" },
    { time: "dim 21:30", label: "Weekly Trends", team: "infra" },
  ];

  return (
    <div className="team-schedule">
      <h3 className="team-schedule-title">Planning journalier (lun-ven, CET)</h3>
      <div className="team-schedule-grid">
        {events.map((ev, i) => (
          <div key={i} className={`team-schedule-item ${ev.team} ${ev.subtle ? "subtle" : ""}`}>
            <span className="team-schedule-time">{ev.time}</span>
            <span className="team-schedule-label">{ev.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────

export default function TeamMapPage({ isActive, agents, onNavigate }) {
  const [perfData, setPerfData] = useState(null);

  const fetchPerf = useCallback(async () => {
    try {
      const res = await fetch("/api/performance/report");
      if (res.ok) {
        const data = await res.json();
        setPerfData(data);
      }
    } catch { /* */ }
  }, []);

  useEffect(() => {
    fetchPerf();
    const id = setInterval(fetchPerf, 120_000);
    return () => clearInterval(id);
  }, [fetchPerf]);

  useEffect(() => { if (isActive) fetchPerf(); }, [isActive, fetchPerf]);

  const agentMap = {};
  (agents || []).forEach((a) => { agentMap[a.name] = a; });

  return (
    <div className="agent-page team-map-page">
      <div className="agent-page-header">
        <h2>{"\ud83d\udcda"} Vue d'ensemble de l'équipe</h2>
        <span>{agents.length} agents, {TEAM_DEFS.length} groupes</span>
      </div>

      {/* Global KPIs */}
      <GlobalKPIs agents={agents} perfData={perfData} />

      {/* Pipeline diagrams */}
      <PipelineDiagram />

      {/* Teams */}
      {TEAM_DEFS.map((team) => (
        <TeamSection key={team.id} team={team} agents={agents} agentMap={agentMap} onNavigate={onNavigate} />
      ))}

      {/* Schedule */}
      <ScheduleTimeline />
    </div>
  );
}
