# SYNTHÈSE AUDIT ULTRA-COMPLET — OneShot Finance
Date : 2026-05-01
Verdict global : **PIVOT (Scénario A — MVP 1-3 tickers, 12 semaines, kill criteria chiffrés)**
Note pondérée : **5.0/10**

> Synthèse de 7 audits indépendants : @qa (6.5), @fullstack (5.5), @ia (7.0), @infrastructure (5.5), @data-analyst (3.5), @product-manager (4.0), @elon (verdict PIVOT). Pondération criticité : @elon×2 (verdict structurel), @data-analyst×1.5 (perf signal), @infrastructure×1.5 (incident-driven), @qa×1, @fullstack×1, @ia×1, @product-manager×1.

## Diagnostic en 5 phrases brutales

1. **30+ trades, 100% EXPIRED, 0 TP_HIT, 0 backtest jamais lancé** — le système n'a jamais prouvé d'edge en 14 jours actifs et personne n'a appuyé sur le bouton "backtest" qui existe déjà depuis v5.1.
2. **21 agents, 22 738 lignes, 4 équipes empilées avant validation de l'edge sur la première** — c'est la cathédrale sans fondations : Team 4 combine 3 signaux dont aucun n'est validé individuellement.
3. **Silent failure 21j non détecté** — aucun monitoring externe, aucun webhook, aucun heartbeat ; un système de trading qui peut s'arrêter 3 semaines sans alerte n'est pas un système de trading, c'est un script.
4. **L'IA Claude est techniquement bien exploitée mais conceptuellement mal utilisée** — Haiku 4.5 + prompt caching + score cache = excellent ($4-6/mois), mais Claude est utilisé comme "scoreur abstrait sur 100" au lieu de "prévisionniste de magnitude" (predicted_pct vs actual mesurable).
5. **La complexité a dépassé la capacité de Thomas à la maîtriser seul** — 1331 lignes de CLAUDE.md, 11 dimensions de scoring, 6 dimensions de learning, 11 stratégies techniques : aucun humain ne peut valider que tout est correctement calibré, et le projet ne survit donc qu'au sunk cost fallacy.

## Convergences (signaux forts inter-audits)

| Convergence | Audits qui la mentionnent | Sévérité |
|---|---|---|
| **Backtest v5.1 jamais lancé** alors qu'il existe et est gratuit | @elon, @data-analyst, @qa, @product-manager | P0 |
| **Monitoring externe / alerting urgent** (silent failure 21j) | @infrastructure, @elon, @product-manager, @data-analyst | P0 |
| **North Star Metric absent** (TP_HIT cible chiffrée, kill criteria) | @elon, @product-manager, @data-analyst | P0 |
| **Calibration TP/SL non data-driven** (formule convexe magique sans justification empirique) | @elon, @data-analyst, @ia | P0 |
| **Trop de complexité non validée — geler features** | @elon, @fullstack, @product-manager, @qa | P1 |
| **Sécuriser /api/admin + régénérer secret leaké** (TWELVE_DATA_API_KEY committé) | @infrastructure, @fullstack | P0 |
| **Tests / CI / régressions** (8 tests version drift, 2 régressions réelles noyées, pas de CI) | @qa, @fullstack | P1 |
| **Duplication code Trader/Journal/Learning 2-3-4 ~700 lignes** | @fullstack, @qa | P1 |
| **Observabilité coût Claude manquante** (endpoint, alerting) | @ia, @infrastructure | P1 |
| **Couplage temporel cassé** (Team 4 démarre 2026-03-16 mais Teams 1-3 non validées) | @elon, @product-manager | P1 |

## Contradictions arbitrées

| # | Contradiction | Audit A | Audit B | Décision finale | Raison |
|---|---|---|---|---|---|
| 1 | LLM utile ou bataille perdue ? | @ia (7/10, "mature techniquement") | @elon ("bataille perdue d'avance vs Citadel") | **LLM utile MAIS mal exploité — pivoter vers magnitude prediction (predicted_pct_4h vs actual)** | @ia parle de la qualité d'implémentation (excellente), @elon parle de la stratégie d'usage (mauvaise). Les 2 audits sont compatibles : garder Haiku, mais demander à Claude de prédire la magnitude au lieu d'un score abstrait. |
| 2 | Refacto BasePersistedAgent (12-16h) vs "geler tout dev Teams 2/3/4" | @fullstack (refacto urgent) | @elon (mort cérébrale Teams 2-4) | **GELER refacto Teams 2/3/4 — refacto SEULEMENT après pivot MVP NG=F validé OU sur Team 1 si MVP utilise Team 1** | Refacto = dev. Si Teams 2/3/4 sont supprimées dans le pivot Scénario A, refactoriser = dette gaspillée. Si Thomas garde Teams 2/3/4 en lecture seule, le refacto attend la validation MVP. |
| 3 | Ajouter Sharpe à Trader 1 vs "stop the bleeding" | @data-analyst (Sharpe + MAE/MFE) | @elon ("priorité absolue : valider l'edge") | **MAE/MFE oui (P0), Sharpe non (P1)** | MAE/MFE est diagnostic critique pour comprendre les 30 EXPIRED (le marché bouge-t-il ? dans quelle direction ?). Sharpe = métrique de pilotage qui suppose qu'on a déjà un signal. Sharpe attend la validation. |
| 4 | Coût Claude $4-6/mois (peanuts) vs "coût Anthropic > 3x P&L = NO-GO" | @ia (coût négligeable) | @elon (critère kill #3) | **Pas de contradiction réelle — le critère @elon est P&L-relatif** | $4-6/mois est négligeable en absolu mais devient critique si P&L = $0. Le critère kill @elon est correct : "Anthropic > 3x P&L" = à $0 P&L sur 12 mois, n'importe quel coût Claude dépasse le ratio. Garder le critère. |
| 5 | Refacto main.py 3022L vs gel features | @fullstack (P0 8-12h) | @elon (gel) | **Reporter à Phase 2 (mois 2+) sauf bloqueur** | main.py fonctionne. Le découper est qualité de vie, pas survie. Phase 1 = valider edge, Phase 2 = nettoyer. Exception : si le watchdog scan a un bug (cf @qa), traiter cette portion isolément. |
| 6 | "Suite tests honorable 6.5/10" vs "9 failures permanentes" | @qa (6.5/10) | @qa interne (8 version drift + 2 régressions) | **Suite a perdu sa valeur signal — fix tests version drift en 1h, fix 2 régressions réelles en 2h, ajouter CI minimale** | Les 9 failures masquent les 2 vraies régressions (SMA 200 disparu, pipeline trader1 graceful degrad cassée). C'est exactement le scénario que la suite est censée empêcher. P0 actionnable rapidement. |
| 7 | "Frontend déjà refactoré sur le bon pattern" vs "32 composants 10651 lignes" | @fullstack (forces) | @fullstack (faiblesses) | **Frontend OK, backend = problème** | Frontend a déjà fait son refacto (TeamPage.jsx unifié). Backend reste le god-file. Concentrer les refactos sur backend. |

## TOP 10 PRIORITÉS CONSOLIDÉES

### P0 — Cette semaine (incident-driven, must-do)

1. **[5 min, Thomas direct]** Régénérer `TWELVE_DATA_API_KEY` (clé leakée en clair dans CLAUDE.md committé ligne 1119) et stocker uniquement dans Replit Secrets. Effacer la valeur du fichier source.
   - Owner : Thomas
   - Dépendances : aucune
   - Critère succès : ancien hash de clé invalidé côté Twelve Data + `git grep "57627ad733b24fa78ac40652078c18fc"` retourne 0 résultats
   - Sources : @infrastructure, @fullstack

2. **[1h, Thomas + @fullstack]** Lancer le backtest v5.1 historique sur 90 jours (`run_news_replay_backtest()` existe déjà, jamais appelé). Output = première vérité empirique sur l'edge du système.
   - Owner : Thomas (lancement) + @fullstack (vérif endpoint `/api/backtest/replay` fonctionne)
   - Dépendances : `price_archive` table populée (vérifier via `/api/price-archive/stats`)
   - Critère succès : un rapport JSON avec TP_HIT rate, Sharpe, max DD sur 90j d'historique
   - Sources : @elon (priorité #1), @data-analyst, @qa, @product-manager

3. **[2h, @data-analyst]** Analyser MAE/MFE des 30+ trades EXPIRED — diagnostic critique des 4 hypothèses chiffrées (cf @data-analyst) :
   - **H1 (55% probabilité)** : TP cible trop large pour la fenêtre de temps. Test : `avg(MFE) << target_pct` ? Exemple : pour ZW=F target ~0.87%, si MFE moyen < 0.3% → calibration trop ambitieuse. Fix : réduire `score_factor` de 50% dans trade_selector.py.
   - **H2 (30% probabilité)** : fenêtre fermeture 20:00 CET trop courte pour scan 17:00. Test : si > 80% des EXPIRED viennent du scan 17:00 → bloquer nouveaux trades après 17:30.
   - **H3 (10% probabilité)** : stop trop large, ni TP ni SL touché. Test : `avg(MAE) << stop_pct` ?
   - **H4 (5% probabilité)** : silent failure 21j masque vraie distribution (TP_HIT possibles avant 2026-03-27). Test : `SELECT * FROM trades WHERE result IN ('TP_HIT','SL_HIT')` complet.
   - Owner : @data-analyst (lancement notebook/script ad-hoc), Thomas (review conclusions)
   - Dépendances : Q2 (le backtest peut révéler un pattern)
   - Critère succès : 1-page diagnostic identifiant H1/H2/H3/H4 par chiffres avec recommandation de fix précis
   - Sources : @elon (recommandation #2), @data-analyst (audit principal — hypothèses chiffrées)

4. **[30 min, Thomas direct]** Définir North Star Metric + Definition of Done + critères kill chiffrés :
   - **North Star Metric** (cf @data-analyst + @PM convergent) : `Weekly TP_HIT ratio = TP_HIT / (TP_HIT + SL_HIT) sur 7 jours glissants` (EXPIRED exclus du calcul d'edge). Cible > 45%. Alerte CRITICAL < 30% sur 10 derniers actionnés.
   - **Definition of Done** (cf @PM) — projet "validé" si toutes ces conditions sur 30j consécutifs : (a) TP_HIT/(TP_HIT+SL_HIT) ≥ 40% sur min 50 trades actionnés, (b) Sharpe Trader 1 ≥ 0.8, (c) Max DD < 5%, (d) zero silent failure (alerte externe < 30 min), (e) Expired rate < 40%, (f) ≥ 30 trades TP/SL pour learning utile.
   - **Kill criteria** (cf @elon) : 6 critères graver dans `kill_criteria.py` qui s'audit dimanche 22h : TP_HIT < 25% sur 90j (50+ trades), Sharpe backtest < 0.5 sur 6 mois historiques, coût Anthropic 12 mois > 3x P&L, > 10h/sem 3 mois sans TP, silent failure > 1/trim, 30+ EXPIRED consécutifs après pivot.
   - **Alertes performance manquantes** : ajouter dans `_detect_alerts()` (agent_performance.py) — CRITICAL si `expired_rate > 90%` ET `total_trades > 10`, et alerte "silence" si aucun trade > 3 jours consécutifs.
   - Owner : Thomas (rédaction décisions) + @fullstack (impl `kill_criteria.py`)
   - Dépendances : aucune (décision pure pour la NSM/DoD), Q5 pour le routage des alertes externes
   - Critère succès : commit `docs/north_star.md` + `backend/app/kill_criteria.py` + KPI Weekly TP_HIT ratio affiché en couleur sur DashboardPage.jsx
   - Sources : @elon (kill criteria), @product-manager (NSM + DoD), @data-analyst (NSM + alertes)

5. **[2-4h, @infrastructure + Thomas]** Monitoring externe URGENT (silent failure 21j inacceptable) — pack complet :
   - **Replit Hacker tier $7/mois** (Always On obligatoire — keepalive interne 127.0.0.1 ne déclenche AUCUN trafic externe vu par Replit autoscale, fix v8.7 reste cosmétique sans ça)
   - **UptimeRobot gratuit** : ping `/api/health` toutes les 5 min, keyword alert sur `"status":"degraded"` ou `"last_scan":{"status":"stale_critical"}` → email + webhook Discord/Slack
   - **Healthchecks.io gratuit** : alerte cron-based — chaque scan ping un endpoint à la fin, alerte si manqué (détecte spécifiquement "scan a-t-il tourné ?" — complémentaire à UptimeRobot)
   - **Sentry free tier (5K events/mois)** : `sentry-sdk[fastapi]` init dans lifespan, wrapper APScheduler events pour misfire/job errors, alertes email error rate > 5/h
   - **Authentifier `/api/admin/*`** : middleware FastAPI vérifiant `X-Admin-Token` contre env var `ADMIN_API_TOKEN` (Replit Secret généré via `openssl rand -hex 32`). Endpoints à protéger : `/api/admin/fix-entry-price`, `/api/admin/price-references`, `/api/infrastructure/reset`, `/api/infrastructure/reset-team/{id}`. CORS fallback `["*"]` à durcir en `["http://localhost:5173"]`.
   - Owner : Thomas (Replit upgrade + créations comptes UptimeRobot/Healthchecks/Sentry + Secrets) + @infrastructure (code endpoints + middleware auth)
   - Dépendances : aucune (action critique cette semaine)
   - Critère succès : test simulé "freeze le scheduler 30 min" → notification reçue dans 5 min ; tentative `curl POST /api/admin/fix-entry-price` sans token → 401
   - Coût total : $7/mois (Replit Hacker uniquement, tout le reste free tier)
   - Sources : @infrastructure (P0 plan complet 5 actions), @elon (recommandation #7), @product-manager (MUST #1+#2+#5), @data-analyst (recommandation #5)

### P1 — Mois 1 (validation edge + sécurité plateforme)

6. **[2-3h, @qa]** Fix la suite de tests + CI minimale :
   - Supprimer ou paramétrer les 8 `test_version_bumped` cassés (1h)
   - Fixer les 2 régressions réelles : SMA 200 disparu (Scoring 3.0), graceful degradation pipeline trader1 (2h)
   - Réparer collection `test_event_scanner.py` + `test_weekend.py` (`pip install sgmllib3k` ou bump feedparser, 30 min)
   - Ajouter GitHub Actions minimal `pytest --tb=short --maxfail=5` sur push (40 lignes YAML, 30 min)
   - Owner : @qa
   - Dépendances : aucune (parallélisable avec P0)
   - Critère succès : `pytest backend/tests/ -q` → 0 failure, CI verte sur push
   - Sources : @qa

7. **[4-8h, Thomas + @fullstack]** Pivot MVP 1-3 tickers (Scénario A @elon, conditionnel sur résultat backtest Q2) :
   - SI backtest Q2 retourne edge mesurable sur ≥1 ticker → branch `mvp-focused`, focus sur ces tickers, geler les autres
   - SI backtest retourne edge nul partout → décision Thomas : Scénario B (quant pur, no LLM) ou Scénario C (abandon)
   - Si MVP Team 1 retenu : configurer `ASSETS = [<top 3 tickers>]`, désactiver scan Teams 2/3/4 dans le scheduler (commenter, pas supprimer)
   - Owner : Thomas (décision) + @fullstack (config)
   - Dépendances : Q2, Q3 (nécessite vérité empirique avant pivot)
   - Critère succès : commit branche `mvp-focused`, scheduler exécute uniquement Team 1 sur tickers retenus, deploy Replit OK
   - Sources : @elon, @data-analyst, @product-manager

8. **[6-8h, @ia + @data-analyst]** Refonte LLM use-case en "prévisionniste de magnitude" :
   - Modifier prompt Claude : au lieu de score 0-100, demander `predicted_pct_4h, predicted_pct_24h, predicted_direction, confidence`
   - Stocker `predicted_pct_*` à côté de chaque trade
   - Journal 1 calcule `actual_pct_4h, actual_pct_24h` post-trade
   - Learning compare `predicted vs actual` → mesure de la précision LLM (le vrai edge mesurable)
   - Owner : @ia (prompt + parsing) + @data-analyst (métriques) + @fullstack (DB schema)
   - Dépendances : Q7 (à faire SUR le MVP, pas sur le système legacy)
   - Critère succès : 30 trades MVP avec predicted vs actual stockés, MAE/MAPE de prédiction calculé
   - Sources : @elon (recommandation #8), @ia (vision Claude)

### P2 — Mois 2+ (qualité, dette, scaling)

9. **[3h, @ia + @fullstack]** Observabilité coût Claude :
   - Endpoint `/api/ai/token-usage` (input, output, cache_read, cache_creation, coût estimé mensuel)
   - Table PG `claude_usage` (jour, agent, tokens, coût) survit aux restarts Replit
   - Alerte si coût mensuel > $20 (vs baseline $4-6)
   - Augmenter `SCORING_BATCH_SIZE` à 30 (économie ~25% tokens non-cachés)
   - Owner : @ia (specs) + @fullstack (impl)
   - Dépendances : Q5 (infrastructure monitoring) — réutilise les patterns
   - Critère succès : dashboard frontend affiche coût Claude live, alerte testée
   - Sources : @ia (P0 dans son audit, P2 ici car coût absolu faible — visibilité reste utile)

10. **[12-16h, @fullstack]** Refacto BasePersistedAgent (CONDITIONNEL — uniquement si Teams 2/3/4 conservées après MVP) :
    - `JsonPgStore[T]` paramétré (~120L) → factorise PG/JSON load/save
    - `BaseTradingAgent` (~180L) → factorise `_fetch_current_price`, `_get_agent_versions`, correlation check
    - `base_learning_math.py` (~150L) → factorise `_clamp`, `_compute_decay_weight`, `_is_significant`
    - Owner : @fullstack
    - Dépendances : Q7 (décision pivot)
    - Critère succès : -700 lignes nettes, tous tests passent, aucune régression silent failure
    - Sources : @fullstack (P0 dans son audit, P2 ici car gating)

### Bonus P1 (à insérer entre Q6 et Q7) — Backup PG + désactivation learning

**B1. [1h, @fullstack]** Backup PostgreSQL automatisé hebdomadaire :
   - `pg_backup_to_json()` existe déjà dans `database.py:1667` mais aucun cron ne l'appelle → RPO actuel = 0 (perte totale possible si Replit kill DB)
   - Ajouter cron APScheduler dimanche 22h CET (après pipeline hebdo) qui dump → upload Cloudflare R2 (free tier 10 Go) via boto3
   - Rétention 30 jours, pruning automatique
   - Doc plan recovery dans `docs/infra/disaster-recovery.md` : RTO < 1h, RPO = 7 jours
   - Owner : @fullstack + @infrastructure (R2 setup)
   - Sources : @infrastructure (action #5 du plan), @PM (SHOULD #8)

**B2. [30 min, @fullstack]** Figer temporairement le learning à multiplicateurs 1.0 :
   - Le learning sur 100% EXPIRED apprend P&L ≈ 0% sur toutes dimensions = bruit pur, peut introduire des biais parasites qui dégradent les futures décisions
   - Désactiver dans `learning.py` jusqu'à avoir ≥ 30 trades TP/SL réels
   - Garder le tracking (calcul des stats) mais retourner 1.0 dans tous les `*_adj` pour `apply_to_score()`
   - Owner : @fullstack
   - Sources : @data-analyst (analyse colinéarités dimensions 1+3, 4+5)

## Sujets sous-couverts (angles morts inter-audits)

- **Sécurité auth utilisateur** : aucun audit n'a regardé l'auth de `/api/admin/*` (réinit, fix-entry-price). Endpoints critiques sans rate limiting visible. À vérifier urgemment.
- **Code Frontend en détail** : @fullstack mentionne "32 composants 10651 lignes" sans deep dive. Pas de revue de la duplication des fetch hooks, du prop drilling dans `TeamPage.jsx` (1266L), de l'accessibilité, ni du bundle size.
- **Compliance financière** : aucun audit n'a regardé : déclaration trades fiscaux, conformité MiFID II, KYC/AML si capital tiers, mentions légales du frontend, conditions d'usage. À voir avec @legal si Thomas opère avec capital >€10k.
- **Perf Twelve Data rate limiter** : @ia parle des coûts Claude, mais personne n'a audité les 800 credits/jour Twelve Data. 4 scans × 41 tickers × 4 fetch (price + history + intraday + bar) = 656 credits/jour avant fallback. Marge serrée.
- **Coût Replit storage** : disque Replit limite à ~5GB. PG bloat + agent_logs (90j) + audit_reports + price_archive (1 an) peut dépasser. @infrastructure parle de VACUUM mais pas du disk usage absolu.
- **Backup / disaster recovery** : aucun mention de backup PG. Si Replit kill la DB, tout est perdu. Critical pour un projet financier.
- **Validation prompt Claude réelle** : @ia n'a pas analysé les `all_scored_news` du journal pour mesurer la précision réelle des dimensions. C'est le vrai edge à mesurer.
- **Frontend mobile / responsive** : @qa mentionne "responsive_design" comme critère audit mais aucune validation visuelle multi-viewport.
- **Performance backend** : pas de profiling runtime (`python -X importtime`, `cProfile`). Suspicion de cycles d'imports (lazy imports systémiques) mais non confirmé.
- **Recovery après crash Replit** : `_recover_pending_trades_on_startup` existe mais @qa note qu'il est testé par `inspect.getsource()` (anti-pattern). Comportement réel non validé.

## Decision tree Thomas

### Si 1h cette semaine
1. **[5 min]** Régénérer TWELVE_DATA_API_KEY sur twelvedata.com → Replit Secret + retirer de CLAUDE.md (Q1)
2. **[5 min]** Upgrader Replit Hacker tier ($7/mois) + activer Always On (Q5 — sans ça, tout le reste est cosmétique)
3. **[10 min]** Créer compte UptimeRobot gratuit + ajouter monitor HTTP `/api/health` 5 min interval, keyword `stale_critical`
4. **[30 min]** Lancer le backtest v5.1 (Q2) — POST `/api/price-archive/fill` puis POST `/api/backtest/replay` + lire JSON résultat
5. **[10 min]** Noter TP_HIT rate sur 90j historiques + écrire 1 phrase de North Star Metric (`Weekly TP_HIT ratio > 45%`)
6. **Si TP_HIT < 10%** → préparer mentalement le pivot Scénario A pour week-end suivant (MVP NG=F ou Top 3 tickers du backtest)
7. **Si TP_HIT > 30%** → continuer phase actuelle, ajouter monitoring complet puis itérer sur calibration

### Si 8h cette semaine
- **Phase 1 partielle (stop the bleeding complet)** :
  - Tout du "1h" ci-dessus (1h)
  - Q3 : analyser MAE/MFE des 30 EXPIRED via script Python ad-hoc — diagnostic H1/H2/H3/H4 chiffré (2h)
  - Q4 : écrire `docs/north_star.md` + DoD + créer `kill_criteria.py` qui audit chaque dimanche (1h)
  - Q5 : authentifier `/api/admin/*` avec `ADMIN_API_TOKEN` + alertes externes Sentry + Healthchecks.io (2h)
  - B2 : figer temporairement le learning à 1.0 jusqu'à 30 trades TP/SL réels (30 min)
  - Si H1 confirmée : recalibration TP/SL data-driven dans trade_selector.py — réduire `score_factor` de 50% (1h)
  - Buffer + commit + deploy (30 min)
- **Output** : système instrumenté, sécurisé, vérité empirique sur l'edge, calibration potentiellement corrigée. Prêt pour décision pivot week-end suivant.

### Si 40h ce mois
- **Phase 1 (Stop Bleeding) + Phase 2 (Validate Edge) démarrée** :
  - Semaine 1 (11h) : tout le "8h" + Q6 (fix 8 test_version_bumped + 2 régressions réelles SMA200/graceful degrad + sgmllib + CI GitHub Actions, 3h)
  - Semaine 2 (10h) : Q7 décision pivot — si TP_HIT backtest > 30% sur 1-3 tickers → branch `mvp-focused` + scheduler reconfiguré (8h) + B1 backup PG hebdo Cloudflare R2 (1h) + ajouter Sharpe + max_drawdown dans `_compute_trader_1_kpis` (1h)
  - Semaine 3 (10h) : Q8 refonte LLM en prévisionniste de magnitude (predicted_pct_4h/24h vs actual MAPE) sur le MVP (8h) + audit Team 4 opérationnelle (vérifier si tradé depuis 2026-03-16, 2h)
  - Semaine 4 (9h) : Q9 observabilité Claude `/api/ai/token-usage` + `claude_usage` PG + augmenter SCORING_BATCH_SIZE à 30 (3h) + monitoring MVP en paper trading + lessons-learned (6h)
  - Buffer débogage + Q10 conditionnel refacto BasePersistedAgent (selon décision pivot)
- **Output** : MVP focalisé, edge mesurable (predicted vs actual MAPE), monitoring complet (UptimeRobot + Healthchecks + Sentry + alertes silence), backup PG hebdo, kill criteria automatisés, NSM affiché en dashboard. Prêt pour 12 semaines de validation Phase 3 (Double Down ou Kill).

## Critères GO/NO-GO finaux (à 90 jours)

**Continuer (GO_POC) si TOUS** :
- TP_HIT rate sur 90 jours actifs > 25% (sur min 50 trades)
- Sharpe backtest > 0.5 sur 6 mois historiques
- LLM precision (predicted_pct_4h vs actual MAPE) < 30%
- Zero silent failure > 6h sur 60j
- Coût Claude < $20/mois (vs baseline $4-6)

**Pivoter (Scénario B quant pur, sans LLM) si** :
- TP_HIT 0-25% MAIS data-driven calibration testée à 3 reprises
- Backtest mean-reversion ATR breakout meilleur que LLM-driven
- Le LLM n'apporte pas d'edge mesurable (predicted vs actual non significatif)

**Kill (Scénario C abandon) si TOUS** :
- TP_HIT < 25% sur 90j actifs avec 50+ trades
- Backtest Sharpe < 0.5
- Coût Anthropic cumulé 12 mois > 3x P&L net
- Temps Thomas > 10h/semaine pendant 3 mois sans TP_HIT
- Silent failures > 1 par trimestre
- 30+ EXPIRED consécutifs après pivot

## Ce que NE DIT PAS l'audit (limites globales)

- **Pas accès aux logs prod réels Replit** : impossible de valider hit rate cache Anthropic, fréquence réelle des silent failures historiques, vraie latence du pipeline.
- **Pas accès au temps Thomas/semaine** : critère kill #4 (>10h/sem) repose sur estimation. Si Thomas y passe 30h/sem comme hobby, le critère ne s'applique pas pareil.
- **Pas accès aux trades historiques complets PG** : seuls les ~30 trades récents (post 2026-03-27) sont visibles. Possible que TP_HIT existent dans l'historique pré-silent-failure (à vérifier via `SELECT COUNT(*) FROM trades WHERE result='TP_HIT'`).
- **Pas de validation backtest historique** : le backtest n'a pas été lancé pendant l'audit. Toutes les conclusions edge/no-edge restent à confirmer empiriquement.
- **Pas accès au capital alloué** : si Thomas trade $1k notional vs $50k, le ROI psychologique change radicalement.
- **Pas de comparaison concurrentielle directe** : l'argument "vs Citadel/Renaissance" est qualitatif, pas mesuré sur les mêmes tickers/timeframes.
- **Pas d'audit @design ni @ux** : le frontend a-t-il une bonne expérience utilisateur ? Aucun audit ne l'a évalué.
- **Pas d'audit @legal** : compliance, fiscalité, conformité réglementaire non couvertes.
- **Audit produit par modèles LLM** : tous les audits sont produits par des agents IA. Biais possibles, hallucinations possibles, à recouper avec un audit humain externe avant décision majeure.

---

**Handoff → Thomas (réponse directe)**
- Fichier produit : `/home/user/Finance/docs/audit/SYNTHESE.md`
- Verdict : **PIVOT (Scénario A) — MVP focalisé après backtest empirique**
- Action immédiate (aujourd'hui ou demain) : Q1 (régen clé, 5 min) + Q2 (lancer backtest, 1h)
- Sans ces 2 actions, le reste de la synthèse est théorique.
- Critère de succès semaine prochaine : avoir un nombre TP_HIT chiffré sur 90j historiques. Tout le reste découle de ce nombre.
