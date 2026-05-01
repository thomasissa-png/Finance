# Path to 10/10 — Toutes les améliorations par agent

Date : 2026-05-01
Source : consolidation des 7 audits (`docs/audit/*.md`)
Effort total estimé pour 10/10 sur tous les axes : **~110-160 heures + $7/mois Replit**

---

## @qa : 6.5/10 → 10/10 (gap 3.5 pts)

| # | Action | Sévérité | Effort | Impact |
|---|--------|----------|--------|--------|
| 1 | Supprimer ou dériver les 8 `test_version_bumped` cassés (parser CLAUDE.md table → `agent.version`) | P0 | 1h | Élimine 8 failures cosmétiques permanentes |
| 2 | Fixer les 2 vraies régressions noyées : `test_sma_200_computed` (SMA200 disparu Scoring 3.0) + `test_pipeline_trader1_failure_doesnt_block_teams` (graceful degradation cassée) | P0 | 2h | Bugs prod actifs, 2 fix concrets |
| 3 | Réparer collection `test_event_scanner.py` + `test_weekend.py` (`feedparser>=6.0.10`) + ajouter CI GitHub Actions minimale (40L YAML, `pytest --maxfail=5` sur push/PR) | P1 | 1h | 2 tests fantômes activés + CI bloque régressions |
| 4 | 8 vrais tests comportementaux (avec freezegun) pour `_run_scan_watchdog`, `_recover_missed_scans_on_startup`, `_get_latest_scan_info`, `_recover_pending_trades_on_startup` (remplacer les `inspect.getsource()` anti-pattern) | P1 | 3h | Couvre commit 8c410a7 v8.7 + élimine 2 anti-patterns |
| 5 | Décomposer `test_agents.py` 2413L en 6 fichiers dédiés : `test_agent_news`, `test_agent_scoring`, `test_agent_trader_4`, `test_agent_auditor`, `test_agent_performance`, **`test_market_data` (CRITIQUE — mapping `type=commodities`)** | P1 | 4h | Couvre 5/15 agents non-testés + protège contre régression CC1 ETF/Cocoa |
| 6 | Bonus 10/10 : `pytest-cov` (mesurer couverture par module) + `mutmut` mutation testing sur modules critiques (`learning.py`, `trade_selector.py`) | P2 | 4h | Quantifie la qualité réelle des assertions |

**Total : ~11h core + 4h bonus = 15h**

---

## @fullstack : 5.5/10 → 10/10 (gap 4.5 pts)

| # | Action | Sévérité | Effort | Lignes économisées |
|---|--------|----------|--------|-------------------|
| 1 | `BasePersistedAgent` + `BaseTradingAgent` + `base_learning_math.py` → factorise Trader/Journal/Learning 2/3/4 | P0 | 12-16h | -700L sur 22 738L |
| 2 | Découper `main.py` 3022L → 6 modules (`lifecycle.py`, `scheduler_jobs.py`, `api/{scan,trades,teams,agents,infrastructure,admin}.py`) avec APIRouter | P0 | 8-12h | God file → 6×~500L |
| 3 | Splitter `agent_auditor.py` 4146L → `auditor/{shared,team_1,team_2,team_3,team_4}.py` | P1 | 6-8h | 4146 → 5×~700L |
| 4 | Centraliser couche I/O DB (`agents/_db.py`) → éliminer ~50 lazy imports `from ..database import ... ` à l'intérieur des fonctions | P1 | 4-6h | Cycle imports résolu, startup plus rapide |
| 5 | FastAPI APIRouter + versioning : 33 endpoints `/api/trader2|3|4` paramétrés en `/api/v1/teams/{team_id}/...` | P1 | 4-6h | 33 → 11 endpoints |
| 6 | Cleanup 24 `getattr(obj, "field", default)` sur Pydantic + 80 `hasattr(...)` → 0 (interface formelle via `BaseAgent.get_metrics()`) | P2 | 3-4h | Élimine anti-patterns, cohérence avec v6.4 |
| 7 | Bonus 10/10 : marquer la dette technique (zéro TODO/FIXME pour 22k lignes = suspect, soit ajouter, soit confirmer discipline) + profiling `python -X importtime` | P2 | 2h | Visibilité dette |

**Total : ~37-54h**

---

## @ia : 7/10 → 10/10 (gap 3 pts)

| # | Action | Sévérité | Effort | Gain |
|---|--------|----------|--------|------|
| 1 | Endpoint `/api/ai/token-usage` + table PG `claude_usage` (input/output/cache/scans/coût/jour, persiste après restart) | P0 | 1-2h | Visibilité coût J+1 vs J+30 facture |
| 2 | `SCORING_BATCH_SIZE` 15→30, monitoring `_last_api_status` ; rollback si timeout >5% | P0 | 1h test + 1 sem monitoring | -15-25% tokens, -$3-5/mois, /2 appels Claude |
| 3 | Mutualiser infra Scoring 1/2 dans `claude_runner.py` partagé (`_get_score_cache(namespace)`, `_track_tokens(agent_name, usage)`, `_call_claude_with_retry`) | P1 | 4-6h | Élimine désynchronisation 1/2, prépare Scoring 5/6 |
| 4 | Télémétrie sur `_validate_coherence` (compteurs par dimension) + injection dynamique de contre-exemples dans system prompt si fix > 10% | P1 | 3-4h | -5-10% trades faux positifs |
| 5 | Score cache fuzzy : embedding hash (sentence-transformers MiniLM 80MB) ou normalisation `md5(normalize(title))` + TTL 4h→6h | P2 | 4-6h | +10-20% hit rate, -$1-2/mois |
| 6 | Bonus 10/10 : valider tarifs Anthropic Haiku 4.5 / Sonnet 4.5 réels via WebSearch + comparer Sonnet pour scoring critique edge detection | P2 | 1-2h | Sécurise estimations ROI |
| 7 | Bonus 10/10 : élargir `_is_zero_edge_headline()` (substring → regex multilingue + scoring confiance) | P2 | 2h | Réduit faux négatifs/positifs pre-filter |

**Total : ~16-23h + 1 sem monitoring**

---

## @infrastructure : 5.5/10 → 10/10 (gap 4.5 pts)

| # | Action | Sévérité | Effort | Coût |
|---|--------|----------|--------|------|
| 1 | **Replit Hacker tier + Always-On** sur le repl Finance | P0 | 5 min Thomas | **$7/mois** |
| 2 | **UptimeRobot** ping `/api/health` 5min, alerte si `"status":"degraded"` ou `last_scan.status=="stale_critical"` | P0 | 15 min | $0 (free tier) |
| 3 | **Auth `/api/admin/*`** : middleware FastAPI vérifiant `X-Admin-Token` env var (`ADMIN_API_TOKEN` Replit Secret) sur 4 endpoints destructifs | P0 | 30 min | $0 |
| 4 | **Régénérer TWELVE_DATA_API_KEY** leakée en clair dans CLAUDE.md (ligne ~615 git committée) + nouvelle valeur Replit Secret uniquement | P0 | 5 min Thomas | $0 |
| 5 | **Sentry** error tracking (`sentry-sdk[fastapi]`, init lifespan main.py, alertes email error rate >5/h) | P1 | 45 min | $0 (free tier 5K events) |
| 6 | **Backup PG automatisé** : cron APScheduler dimanche 22h CET → `pg_backup_to_json()` → upload Cloudflare R2 (rétention 30j) + doc plan recovery | P1 | 2h | $0 (R2 free tier 10 Go) |
| 7 | **Healthchecks.io** pour cron monitoring (chaque scan ping un endpoint à la fin → alerte si silence) | P1 | 1h | $0 (free tier) |
| 8 | Bonus 10/10 : status page publique partageable (BetterStack free 10 monitors) + procédure rotation `ANTHROPIC_API_KEY` documentée trimestrielle | P2 | 1h | $0 |
| 9 | Bonus 10/10 : disaster recovery test réel (simuler PG down + restore depuis R2 backup) trimestriel | P2 | 2h | $0 |

**Total : ~7h + $7/mois**

---

## @data-analyst : 3.5/10 → 10/10 (gap 6.5 pts — le plus gros)

| # | Action | Sévérité | Effort | Bloquant ? |
|---|--------|----------|--------|------------|
| 1 | **Dashboard "EXPIRED Analysis"** : segmenter MAE/MFE des 30+ EXPIRED par 4 axes (heure entrée, distance TP, distance SL, ticker). Calculer `TP_touch_rate`. Si <5% → confirme H1 calibration trop ambitieuse | P0 | 4-8h | OUI — bloque la décision pivot |
| 2 | **North Star Metric** : `Weekly TP_HIT ratio` (TP_HIT / (TP_HIT + SL_HIT) sur 7j glissants), cible >45%, alerte CRITICAL <30%. Implémenter dans `_detect_alerts()` + DashboardPage.jsx KPI principal | P0 | 2-3h | OUI |
| 3 | **Alerte "silence"** : 0 trade ouvert/fermé depuis ≥3 jours ouvrables → CRITICAL externe (manquant : silent failure 21j n'aurait déclenché aucune alerte) | P0 | 1h | OUI |
| 4 | Sharpe ratio + max drawdown dans `_compute_trader_1_kpis()` (copier le pattern `weekly_sharpe` déjà présent dans `_compute_trader_4_kpis()`) | P1 | 2h | NON |
| 5 | Tableau **ATR intraday vs ATR daily** par ticker actif (NG=F, ZW=F, KC=F...) — si ratio < 0.4 → ajouter `intraday_correction_factor` dans trade_selector.py | P1 | 2-3h | NON (mais clé pour recalibration) |
| 6 | Webhook Slack/Discord vers l'extérieur sur `alert_level == "CRITICAL"` dans daily_report (anomalies, streaks, churning) | P2 | 2-3h | NON |
| 7 | **Lancer le backtest v5.1** (`POST /api/price-archive/fill` puis `POST /api/backtest/replay` sur 100 derniers scan_history) — JAMAIS LANCÉ depuis création v5.1 | P0 | 1h trigger + lecture | OUI — validation edge historique |
| 8 | Geler temporairement le learning (multiplicateurs figés à 1.0) jusqu'à ≥30 trades TP/SL — actuellement il apprend sur du bruit pur | P1 | 30 min | OUI (évite contamination données) |
| 9 | Investiguer le **biais sectoriel** : pourquoi 100% trades = 5 commodities, 0 forex/equity/indices ? Bug filtrage calendrier ou délibéré ? | P1 | 2-3h | NON |
| 10 | Bonus 10/10 : analyse colinéarité des 6 dimensions learning (matrice de corrélation par paires de dimensions) → identifier les vraies dimensions indépendantes | P2 | 4h | NON |

**Total : ~21-29h dont 8-12h en P0 bloquant**

---

## @elon : Verdict PIVOT → GO_FULL (12 semaines à valider)

Pas un score mais un verdict. Le chemin vers GO_FULL :

### Phase 1 — Stop the bleeding (Semaine 1-2)
| # | Action | Effort | Owner |
|---|--------|--------|-------|
| 1 | Lancer backtest v5.1 sur 90j historiques | 1j | @fullstack + @data-analyst |
| 2 | Analyser MAE/MFE des 30+ EXPIRED | 1j | @data-analyst |
| 3 | Définir North Star Metric chiffré (Weekly TP_HIT >40%) | 0.5j | Thomas |
| 4 | **Geler dev Teams 2/3/4** (mort cérébrale assumée) | 0j décision | Thomas |
| 5 | Webhook Slack/Discord sur silent failure >24h | 0.5j | @infrastructure |
| 6 | Critères de kill chiffrés implémentés en code | 1j | @fullstack |

### Phase 2 — Pivot MVP (Semaine 3-4)
| # | Action | Effort | Owner |
|---|--------|--------|-------|
| 7 | Branche `mvp-ng-only` : supprimer ~80% du code (Teams 2/3/4, learning multi-dim, scoring 2/3/4, frontend pages) | 1 sem | @fullstack |
| 8 | Garder uniquement : `market_data.py`, `agent_news.py` (filtré EIA), `trade_selector.py` simplifié, `journal.py` | inclus #7 | @fullstack |
| 9 | Backtester sur 24 mois NG=F + EIA reports | 2j | @data-analyst |
| 10 | Refonte LLM use-case en "prévisionniste de magnitude" (predicted_pct vs actual_pct) | 2 sem | @ia |

### Phase 3 — Validation live (Semaine 5-12)
| # | Action | Effort | Owner |
|---|--------|--------|-------|
| 11 | Live trading petit notional ($1k-2k) sur NG=F uniquement | 0 | Thomas |
| 12 | Tracking strict : TP_HIT rate, Sharpe, max drawdown, predicted_magnitude vs actual | continu | @data-analyst |
| 13 | Review mensuelle stricte avec critères de kill | 4h × 3 | Thomas |

### Critères GO_FULL à S+12
- TP_HIT rate > 40% sur 4 semaines glissantes
- Sharpe > 0.8
- Max drawdown < 5%
- 0 silent failure depuis Phase 1
- LLM MAPE prévisionniste < 30%

### Critères KILL à S+12 (un seul suffit)
- TP_HIT rate < 25% sur 4 semaines
- Sharpe < 0.5 sur backtest 90j
- Coût Anthropic > 3× P&L
- Aucun TP_HIT pendant 60 jours actifs
- Drift de l'edge > 50% entre backtest et live
- Max drawdown > 10%

**Si GO_FULL à S+12** : ré-introduire complexité incrémentalement (ticker #2, équipe #2) **uniquement après validation par 30 trades minimum**.

---

## @product-manager : 4/10 → 10/10 (gap 6 pts)

### MUST (Semaine 1-2) — 8 items P0
1. Alerte UptimeRobot sur `/api/health` (15 min Thomas)
2. Auth endpoints `/api/admin/*` (1-2h @fullstack)
3. Régénérer TWELVE_DATA_API_KEY leakée (5 min Thomas)
4. Diagnostic data-driven 100% EXPIRED — MAE/MFE (2-4h @fullstack + @data-analyst)
5. Replit Hacker tier $7/mois (5 min Thomas)
6. Alerte CRITICAL si `expired_rate > 90%` 7j (1h @fullstack)
7. Lancer backtest v5.1 — premier run jamais effectué (1h @fullstack)
8. Fix les 2 régressions QA noyées (SMA200, graceful degradation) (2-3h @qa+@fullstack)

### SHOULD (Semaine 3-4) — 8 items P1
1. Recalibration TP/SL data-driven (`intraday_correction_factor`) (4-6h)
2. Sharpe + max drawdown dans `_compute_trader_1_kpis()` (2h)
3. North Star Metric Weekly TP_HIT visible dashboard (2-3h)
4. Désactiver temporairement le learning (multiplicateurs 1.0) (30 min)
5. Supprimer les 8 `test_version_bumped` cassés (1h)
6. Réparer collection `test_event_scanner.py` + `test_weekend.py` (30 min)
7. Audit Team 4 opérationnelle (2h diagnostic)
8. Backup PostgreSQL automatisé hebdo dimanche 22h (1h)

### COULD (Mois 2) — 5 items P2 (uniquement si edge validé Phase 2)
1. BasePersistedAgent + BaseTradingAgent (12-16h)
2. Découper main.py 3022L → 6 modules (8-12h)
3. Endpoint `/api/ai/token-usage` (2-3h)
4. CI GitHub Actions minimale (2h)
5. 5 fichiers tests dédiés manquants (4h)

### WON'T (this iteration) — 6 items reportés
1. Team 5 (aucune avant edge validé)
2. Nouvelles sources data (20 sources existent déjà)
3. Frontend redesign (fonctionne, faible priorité)
4. Nouveaux indicateurs Team 3 (11 stratégies actives, statistique insuffisante)
5. Split agent_auditor.py 4146L (refacto sans impact fonctionnel)
6. Optimisation coût Claude (-$3-5/mois négligeable vs enjeux)

### Definition of Done proposée pour 10/10 PM
- WR ≥ 40% sur 50 trades minimum
- Sharpe ≥ 0.8 sur 30 jours consécutifs
- Max drawdown < 5%
- Zero silent failure 60 jours
- EXPIRED rate < 40%
- North Star Metric tracké quotidiennement
- Critères GO/NO-GO chiffrés respectés
- Coût d'opportunité Thomas mesuré (heures/semaine)

**Total Sem 1-2 (MUST) : ~13-20h pour Thomas + agents**

---

## Vue agrégée : path to 10/10 par priorité globale

### P0 — Cette semaine (incident-driven, tout Thomas + 1 dev)
- @infrastructure : Replit Hacker + UptimeRobot + Auth admin + régénérer clé Twelve Data (~1h)
- @data-analyst : Backtest v5.1 + MAE/MFE diagnostic + North Star + alerte silence (~12h)
- @fullstack : Auth admin + alerte expired_rate >90% (~3h)
- @qa : 2 régressions réelles fix (~2h)
- @elon : Geler dev Teams 2/3/4 (décision)

**Sous-total P0 : ~18-20h sur la semaine**

### P1 — Mois 1 (validation edge)
- @ia : Token usage endpoint + batch_size 30 + claude_runner mutualisé (~6-9h)
- @fullstack : Sharpe Trader 1 + recalibration TP/SL (~6-8h)
- @qa : Réparer tests fantômes + CI + tests watchdog (~4-5h)
- @infrastructure : Sentry + Backup R2 + Healthchecks (~4h)
- @data-analyst : ATR intraday vs daily + biais sectoriel (~5-6h)
- @product-manager : exécuter SHOULD (8 items) coordination

**Sous-total P1 : ~25-32h sur le mois**

### P2 — Mois 2+ (qualité, conditionnel à edge validé)
- @fullstack : refacto BasePersistedAgent + main.py split + cleanup getattr/hasattr (~25-35h)
- @ia : Score cache fuzzy + télémétrie coherence (~7-10h)
- @qa : 5 fichiers tests dédiés + mutation testing (~8h)
- @data-analyst : webhook alertes externes + analyse colinéarité dimensions (~6-7h)
- @infrastructure : status page + procédure rotation clés + DR test (~3h)

**Sous-total P2 : ~50-65h sur 2 mois**

---

## Total global : ~110-160 heures pour 10/10 sur tous les axes + $7/mois Replit

**Mais @elon dit** : si le backtest v5.1 et le diagnostic MAE/MFE révèlent **edge = 0**, ne pas dépenser ces 110-160h. Pivot vers MVP 1 ticker (12 semaines de validation) supprime ~80% du code donc invalide ~60% des refactos P2.

**Décision Thomas requise avant d'investir** : exécuter d'abord les **2-3 actions zéro-effort** (backtest v5.1 trigger 5 min, MAE/MFE analysis ~4h, régénérer clé 5 min) — selon résultats, GO refacto complet OU PIVOT MVP.
