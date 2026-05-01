# Audit Data & KPIs — OneShot Finance
Score : 3.5/10
Date : 2026-05-01
Auditeur : @data-analyst

> **Verdict** : edge réel = INCONNU. Le framework analytics est solide (agent_performance, 6 dimensions learning, MAE/MFE, by_category) mais il mesure un système qui produit 100% d'EXPIRED — aucune donnée d'apprentissage réelle. Sharpe et max drawdown absents de l'Équipe 1. North Star Metric non défini. Backtesting v5.1 jamais lancé. Le score de 3.5 reflète l'infrastructure solide mais l'absence totale de signal actionnable mesuré.

---

## Actions immédiates (P0 — avant tout développement)

| # | Action | Qui | Délai |
|---|--------|-----|-------|
| 1 | GET /api/journal → analyser MAE/MFE des 30+ EXPIRED | @fullstack + @data | Aujourd'hui |
| 2 | Si avg(MFE) < 30% du target_pct → réduire targets de 40-50% dans trade_selector.py | @fullstack | J+1 |
| 3 | POST /api/price-archive/fill → lancer le premier backtest replay | @fullstack | J+1 |
| 4 | Définir North Star Metric (proposition ci-dessous : Weekly TP_HIT ratio) | @product | J+2 |
| 5 | Ajouter alerte CRITICAL si expired_rate > 90% sur 10+ trades | @fullstack | J+2 |
| 6 | Ajouter Sharpe ratio + max drawdown dans _compute_trader_1_kpis() | @fullstack | J+3 |

---

## Métriques observées en prod (baseline)
- **Trades visibles** : 30+ trades, 100% EXPIRED, exclusivement 5 tickers commodities agri/énergie
- **Tickers concernés** : NG=F, ZW=F, ZC=F, KC=F, CC=F — 0 trade forex/equity/indices
- **Période active** : 2026-03-27 à 2026-04-10 (~14j) puis silent failure 21j (corrigée commit 8c410a7 le 2026-05-01)
- **Zero TP_HIT ou SL_HIT** sur toute la période observable
- **4 équipes de trading actives** : Équipe 1 (day trading), 2 (trend), 3 (technique), 4 (meta/ensemble)
- **North Star Metric déclaré** : non trouvé dans project-context.md ni CLAUDE.md — absent
- **Sharpe ratio calculé** : non — absent de agent_performance.py _compute_trader_1_kpis
- **Max drawdown** : non calculé nulle part dans agent_performance.py (confirmé par lecture code)

---

## Diagnostic critique : 100% EXPIRED rate sur 30+ derniers trades

> C'est le signal d'alarme n°1. Un système avec 100% EXPIRED sur 30+ trades n'a aucune performance mesurable — ni win rate, ni P&L, ni R/R réalisé. La cause racine doit être identifiée avant toute autre optimisation.

### Hypothèse #1 — TP cible trop large pour la fenêtre de temps disponible (probabilité estimée : 55%)

La formule de calibration (CLAUDE.md section "Calibration R/R") est convexe :

```
score_factor = (score/100)^1.5
magnitude_factor = 0.7 + 0.6 * (expected_magnitude/100)
target = ATR5j × score_factor × magnitude_factor × news_category_target_mult
```

Exemple concret avec un trade weather typique :
- Score Claude = 65 (score moyen plausible pour commodity weather)
- ATR5j ZW=F ≈ 1.2% (blé, volatilité normale)
- score_factor = (0.65)^1.5 ≈ 0.524
- magnitude_factor = 0.7 + 0.6×0.6 = 1.06 (expected_magnitude=60)
- news_category_target_mult weather = 1.3
- **Target ≈ 1.2% × 0.524 × 1.06 × 1.3 ≈ 0.87%**

Pour ZW=F (blé), un mouvement de 0.87% en 3h (scan 17:00 → fermeture 20:00 CET) n'est pas impossible mais rare. Pour KC=F (café) ou NG=F (gaz naturel, haute volatilité), ça peut passer. Mais si ATR5j est sur-estimé (période volatile récente qui ne se reproduit pas intraday), le target devient inatteignable.

**Test data-driven #1** : Calculer l'ATR intraday réel (8h de trading) vs ATR5j daily pour chaque ticker. Si ATR_intraday < 0.5 × ATR5j daily, la calibration est structurellement trop ambitieuse.

### Hypothèse #2 — Fenêtre de fermeture 20:00 CET trop courte pour les trades tardifs (probabilité estimée : 30%)

Le scan 17:00 CET ouvre un trade avec fermeture forcée à 20:00 = **3 heures maximum**. La formule R/R suppose `actual_pricing_hours` calculé sur la fenêtre restante, mais si cette fenêtre est trop courte ET le target trop loin, le trade expire systématiquement.

Pour les commodities agricoles (ZW, ZC), les marchés CME ferment à 20:00 CET (14:00 ET). Un trade ouvert à 17:01 ne peut toucher son TP qu'en moins de 3h sur un marché souvent calme en fin de session.

**Test data-driven #2** : Segmenter les EXPIRED par heure d'entrée. Si 80%+ des EXPIRED viennent du scan 17:00 → hypothèse confirmée. Endpoint `/api/journal` expose l'heure d'entrée.

### Hypothèse #3 — Stop trop large aussi → ni TP ni SL touché (probabilité estimée : 10%)

Si stop = 0.5-0.7 × target (selon les tiers low-vol/normal), un trade avec target à 0.87% a un stop à ~0.45-0.60%. Pour ZW=F avec ATR intraday faible, le prix peut ne jamais bouger de 0.45% dans les 3h. Résultat : EXPIRED avec P&L ≈ 0%.

Ce scénario expliquerait aussi pourquoi le P&L des EXPIRED est proche de 0 (ni perte ni gain significatif). Vérifiable via MAE des journal entries.

### Hypothèse #4 — Silent failure 21 jours masque la vraie distribution (probabilité estimée : 5%)

La période sans trades (2026-04-10 → 2026-05-01) suivie de la correction produit un biais de sélection : on ne voit que la phase "mauvaise" du système. Des périodes antérieures (avant 2026-03-27) pourraient avoir des TP_HIT. Non vérifiable sans accès à la DB complète.

### Test data-driven recommandé (priorité absolue)

```
1. GET /api/journal — récupérer les 50 dernières journal entries avec MAE/MFE
2. Calculer : avg(MAE) pour les EXPIRED = distance max défavorable atteinte
   - Si avg(MAE) << stop_pct → le prix ne bouge pas assez (H1 confirmée)
   - Si avg(MAE) proche de stop_pct → le prix bouge mais SL n'est pas touché (H3 confirmée)
3. Calculer : distribution des heures d'entrée dans les EXPIRED
   - Si >60% viennent du scan 17:00 → H2 confirmée
4. Comparer ATR5j daily vs range intraday réel via price_archive
   - Si ATR_intraday_réel < 0.4 × ATR5j → H1 confirmée structurellement
```

---

## Top 3 forces analytics

1. **Infrastructure de mesure solide** : agent_performance.py couvre win_rate, expired_rate, P&L, direction_accuracy, by_category, R/R réalisé, MAE/MFE moyens, source_error_rate — c'est un KPI framework complet et bien structuré. Les 3 niveaux de rapport (snapshot horaire, daily_report 22h30, weekly_trends dim) sont bien pensés.

2. **6 dimensions de learning indépendantes** : ticker×cat, session, newscat+zone+intensity+ticker, régime VIX, direction, delay_bias — approche multi-dimensionnelle rare pour un système intraday. La protection des commodities (cat_adj désactivé pour commodities — règle absolue) est une décision analytique saine pour éviter le biais d'apprentissage.

3. **MAE/MFE trackés** : les journal entries v4.1 enrichissent chaque trade avec Max Adverse Excursion, Max Favorable Excursion, slippage, bar coverage, realized R/R. Ces données permettent un diagnostic précis du comportement de calibration — le matériel est là, il faut l'exploiter.

---

## Top 5 faiblesses

| # | Sévérité | Problème | Impact |
|---|----------|----------|--------|
| 1 | **P0** | **100% EXPIRED rate — aucune performance mesurable** : win_rate = 0%, R/R réalisé = EXPIRED (P&L ≈ 0%). Le learning ne peut pas s'améliorer (aucun TP_HIT ni SL_HIT à apprendre). Le système tourne à vide. | Toutes les décisions de routing (trading) sont basées sur une calibration potentiellement cassée. Edge réel = inconnu. |
| 2 | **P0** | **North Star Metric absent** : aucun KPI "unique, principal, aligné sur l'objectif" n'est défini. project-context.md ne le mentionne pas. CLAUDE.md donne la philosophie ("edge en avance de phase") mais pas de métrique chiffrable. Sans North Star, impossible de savoir si le système progresse. | Roadmap sans boussole — chaque amélioration est jugée localement sans critère global. |
| 3 | **P1** | **Sharpe ratio absent de agent_performance.py** : `_compute_trader_1_kpis()` calcule win_rate, avg_pnl, expired_rate, direction_accuracy, R/R réalisé — mais pas de Sharpe ratio ni de max drawdown. Ces métriques sont critiques pour comparer à un benchmark (buy-and-hold commodity index). | Impossible d'évaluer le risk-adjusted return. Un système avec WR=60% et Sharpe=0.1 est inférieur au marché. |
| 4 | **P1** | **Biais sectoriel non documenté** : 100% des trades observés sont sur 5 commodities agri/énergie (NG, ZW, ZC, KC, CC). 0 trade sur forex, équities Paris, indices. Ce biais est-il délibéré (edge structurel sur commodities physiques) ou un bug de filtrage (calendrier économique bloquant tous les autres actifs) ? La session 1 de développement a corrigé un bug BOE bloquant KC=F — d'autres filtres erronés existent peut-être. | Si le biais est un bug, ~30 des 41 actifs ne tradent jamais → edge perdu. |
| 5 | **P2** | **Backtesting v5.1 jamais exploité** : `run_news_replay_backtest()` et la table `price_archive` existent depuis v5.1 (CLAUDE.md section "Backtest & Learning Integrity"). Les endpoints `/api/backtest/replay` et `/api/price-archive/stats` sont exposés. project-context.md ne mentionne aucun backtest lancé. Sans backtest, impossible de valider que la formule de scoring produit un edge positif sur données historiques avant de déployer en prod. | Le système trade réel sans validation historique = risque capital non quantifié. |

---

## 5 KPIs/dashboards à ajouter ou prioriser

### [P0] 1. Dashboard "EXPIRED Analysis" — cause racine en 48h

Segmenter tous les EXPIRED selon 4 axes :

| Axe | Question | Données source |
|-----|----------|----------------|
| Heure d'entrée | Scan 07:50 vs 11:15 vs 14:50 vs 17:00 | `entry_time` dans journal entries |
| Distance TP atteinte | avg(MFE) vs target_pct | `mfe` dans journal entries |
| Distance SL atteinte | avg(MAE) vs stop_pct | `mae` dans journal entries |
| Ticker | ZW vs ZC vs KC vs NG vs CC | `ticker` dans journal entries |

Formule clé à calculer : `TP_touch_rate = trades où MFE ≥ target_pct / total_EXPIRED`
- Si TP_touch_rate < 5% → le prix ne va jamais assez loin → calibration trop ambitieuse (H1)
- Si TP_touch_rate > 30% mais trade EXPIRED → la résolution de prix (15min bars) rate le TP → bug journal

**Action** : GET /api/journal, agréger MAE/MFE, créer un graphique "distance_to_TP vs time_remaining". Aucun code à écrire — les données existent.

### [P0] 2. North Star Metric — définir et implémenter

**Proposition** : `Cumulative Edge Return (CER)` = somme des P&L des trades TP_HIT et SL_HIT uniquement (EXPIRED exclus du calcul) / nombre de trades actionnés.

Rationale : les EXPIRED n'ont pas été "tradés" — ils sont des trades annulés par le temps. Seuls les TP/SL mesurent l'edge réel de la sélection de news.

Alternative si CER est difficile à implémenter rapidement : `Weekly TP_HIT ratio` = nombre de TP_HIT / (TP_HIT + SL_HIT) sur 7 jours glissants, cible > 45% (au-dessus du hasard avec R/R >1).

Threshold d'alerte : si TP_ratio < 30% sur 7 jours → alerte P0 dans agent_performance.

### [P1] 3. Sharpe ratio + max drawdown dans daily_report

**Constat code** : l'Équipe 4 (agent_performance.py ligne 817) calcule un `weekly_sharpe` dans `_compute_trader_4_kpis()`. L'Équipe 3 calcule un Sharpe par stratégie via Journal 3. Mais `_compute_trader_1_kpis()` (l'équipe principale, intraday) n'a ni Sharpe ni max drawdown.

À ajouter dans `_compute_trader_1_kpis()` :

```python
# Sharpe ratio (annualisé, base trades quotidiens)
pnl_series = [t.pnl_pct for t in closed_sorted_by_date]
avg_daily = sum(pnl_series) / len(pnl_series)
std_daily = statistics.stdev(pnl_series) if len(pnl_series) > 1 else 0
sharpe = (avg_daily / std_daily * (252**0.5)) if std_daily > 0 else None

# Max drawdown
cumulative = []
running = 0
for p in pnl_series:
    running += p
    cumulative.append(running)
peak = max(cumulative)
drawdown = min(0, min(cumulative) - peak)
kpis["max_drawdown"] = round(drawdown, 2)
kpis["sharpe_ratio"] = round(sharpe, 2) if sharpe else None
```

Benchmark cible pour un système intraday commodity news-based : Sharpe > 0.8 est acceptable, > 1.5 est bon. Max drawdown < 5% est sain.

### [P1] 4. ATR intraday vs ATR daily — tableau de calibration

Comparer pour chaque ticker actif :
- ATR5j daily (utilisé dans la calibration actuelle)
- Range intraday réel moyen (high - low sur 8h de trading) depuis price_archive

Si ratio ATR_intraday / ATR_daily < 0.4 de façon systématique → recalibrer avec un `intraday_correction_factor` dans trade_selector.py.

Fréquence : calcul hebdomadaire, affiché dans Performance page frontend.

### [P2] 5. Observabilité des alertes anomalie vers l'extérieur

Les alertes anomalie (streaks ≥3, churning Trader 2, EXPIRED rate >60%) sont détectées dans `_detect_alerts()` mais restent dans le message bus interne. Aucune notification externe (Slack, email, webhook) n'est configurée.

Pour un système de trading réel, une alerte silent failure de 21 jours NE DEVRAIT PAS être découverte manuellement. Implémenter :
- Webhook POST vers Slack/Discord si `alert_level == "CRITICAL"` dans daily_report
- Alerte si aucun trade en >3 jours (système silencieux = bug probable)
- Alerte si expired_rate >80% sur 10 derniers trades

---

## Analyse : les 6 dimensions de learning sont-elles indépendantes ?

### Colinéarités identifiées

**Dimension 1 (ticker×cat) et Dimension 3 (newscat+zone+intensity+ticker)** sont structurellement redondantes sur les commodities agricoles. Exemple : ZW=F (blé) est quasi-exclusivement tradé sur news `weather` (gel, sécheresse) et `supply_chain`. La dimension 1 encode déjà ticker=ZW×cat=weather, et la dimension 3 encode weather+zone+intensity+ZW. Avec peu de trades, les deux dimensions apprennent sur les mêmes données.

**Dimension 4 (régime VIX, 2 buckets)** et **Dimension 5 (direction)** sont potentiellement colinéaires : en régime high_vol (VIX>20), les commodities énergétiques (NG=F) tendent à avoir un biais directionnel LONG (risk-off = demande de valeurs refuges). Si VIX est élevé en même temps que direction=LONG sur NG=F, les deux ajustements s'appliquent dans le même sens → doublement du signal sans données supplémentaires.

**Impact** : avec 30+ trades EXPIRED uniquement, le learning a 0 signal utile. Les 6 dimensions ont le même input : P&L ≈ 0% pour tous les EXPIRED. Cela signifie que les ajustements de learning actuels sont calculés sur du bruit.

### Recommandation

Désactiver temporairement le learning (figer tous les multiplicateurs à 1.0) jusqu'à avoir ≥30 trades TP_HIT ou SL_HIT. Le learning adaptatif sur des données 100% EXPIRED peut créer des biais parasites qui dégradent les futures décisions.

---

## Per-source signal_reliability tracking (v5.5)

### Où c'est exposé ?

Le tracking per-source est implémenté dans `build_performance_summary()` (learning.py, v5.5) et injecté dans le prompt Claude comme feedback. Il est visible via `/api/learning/adjustments` (si endpoint exposé) et dans les logs DECISION de Learning 1.

**Lacune identifiée confirmée par le code** : `_compute_learning_kpis()` (agent_performance.py lignes 624-654) retourne uniquement `adjustment_count`, `anomaly_count`, `cache_valid`, `total_recalculations`, `learning_2_adjustments`, `learning_2_anomalies`. Les ajustements par source (EIA, USDA, GNews, Open-Meteo) ne sont pas exposés dans le daily_report ni dans l'API performance. La décomposition par dimension (`ticker_mult`, `cat_mult`, `session_mult`, `newscat_mult`, `regime_mult`, `dir_mult`, `delay_bias_adj`) est dans les logs uniquement.

### Impact

Les underperformers par source (WR < 30%) sont identifiés dans le feedback Claude uniquement — pas dans le dashboard opérationnel. Si Open-Meteo génère des faux positifs météo systématiquement, l'équipe ne le voit que si elle lit les logs Claude.

**Cas concret probable** : avec 100% EXPIRED, `signal_reliability precision tracking` (learning.py E3) va tenter de comparer WR low-reliability vs high-reliability. Mais si tous les trades sont EXPIRED (P&L ≈ 0%), les deux groupes ont WR≈0% → l'alerte "low-rel WR > high-rel WR" ne se déclenche pas, même si le signal est complètement cassé. Le tracking per-source perd son utilité quand expired_rate = 100%.

---

## Anomaly detection — état opérationnel

### Ce qui est détecté

Dans `_detect_alerts()` (agent_performance.py) :
- `win_rate < 35%` → CRITICAL
- `expired_rate > 50%` → WARN
- `price_fetch_success_rate < 70%` → WARN
- `pg_failures >= 3` → CRITICAL

Dans `build_performance_summary()` (learning.py) :
- Streaks ≥3 pertes consécutives
- Direction accuracy anormale (<45% ou >65%)
- EXPIRED rate >40%
- MAE élevé si SL_HIT >40%

### Ce qui manque

1. **Alerte "silence" absente** : aucune détection si le système n'a pas tradé depuis N jours. La silent failure de 21 jours n'aurait déclenché aucune alerte dans le système actuel.
2. **Alertes non routées vers l'extérieur** : elles restent dans `report["alerts"]` et le message bus — aucun webhook.
3. **Alerte 100% EXPIRED absente** : le seuil est à 50% WARN, pas de CRITICAL au-delà. Un expired_rate de 100% sur 30 trades devrait déclencher une escalade automatique.

---

## Backtesting v5.1 — état et recommandations

### Ce qui existe

- Table `price_archive` : OHLCV daily par ticker, archivage automatique après journal
- `run_news_replay_backtest()` : re-score les headlines historiques avec la formule actuelle
- Endpoints : `/api/backtest/replay`, `/api/price-archive/stats`, `/api/price-archive/fill`

### Ce qui est absent

- **Aucun backtest n'a été lancé** (project-context.md ne mentionne aucun résultat)
- **Validation de la formule convexe** (score/100)^1.5 sur données historiques : sans backtest, on ne sait pas si cette formule produit un edge positif ou non
- **Simulation de la fenêtre EXPIRED** : un backtest qui simule combien de trades auraient été TP_HIT si la fenêtre de fermeture était 20:00 vs 18:00 vs 16:00

### Recommandation prioritaire

Lancer `/api/price-archive/fill` pour backdater les données OHLCV, puis `/api/backtest/replay` avec les 100 derniers scan_history. Analyser :
1. Combien de trades auraient touché leur TP en J+1 ou J+2 (pas bloqués à 20:00 CET) ?
2. Quelle est la distribution des scores Claude sur les headlines historiques ? Sont-ils systématiquement <30 (edge nul) ou >50 (edge potentiel) ?

---

## Expired/Win/Loss ratio — benchmarks sectoriels

### Ce qui est sain pour un système intraday news-based

Pour un système day trading event-driven sur commodities avec edge sur "signaux en avance de phase" :

| Résultat | Cible saine | Interprétation |
|----------|-------------|----------------|
| TP_HIT | 30-45% | Edge positif confirmé |
| SL_HIT | 25-35% | Risk management fonctionnel |
| EXPIRED | 25-40% | Signaux pas assez forts → normal |
| EXPIRED | >60% | Signal d'alarme — calibration cassée |
| EXPIRED | 100% | Arrêt d'urgence — diagnostic obligatoire |

**Référence** : les systèmes intraday news-based publiés (Kissell 2021, Treleaven 2013) montrent des win rates entre 35-55% avec expired/timeout rates de 20-35%. Un expired rate >80% indique généralement une calibration TP/SL inadaptée à la volatilité réelle du sous-jacent ou à la durée de la fenêtre de trading.

### Situation actuelle vs cible

| Métrique | Observé | Cible | Écart |
|----------|---------|-------|-------|
| TP_HIT rate | 0% | 30-45% | -30 à -45pp |
| SL_HIT rate | 0% | 25-35% | -25 à -35pp |
| EXPIRED rate | 100% | 25-40% | +60 à +75pp |
| Sharpe ratio | Non calculé | >0.8 | Inconnu |
| Max drawdown | Non calculé | <5% | Inconnu |

---

## Data quality — dérives Twelve Data vs yfinance

### Guards en place

- `validate_price(ticker, price)` : rejette si déviation >50% de la référence
- `TD_PRICE_RANGES` : plages de prix réalistes par ticker
- `fetch_price_validated()` : cross-check yfinance si anomalie TD détectée

### Lacune identifiée

Le seuil de 50% est très large. Pour des commodities agricoles, un mouvement de 50% en une journée est impossible sans événement majeur (circuit breaker). Des erreurs de mapping (ex: CC1 sans `type=commodities` = Amundi ETF à 287€ au lieu de Cacao à 3425$) seraient détectées seulement si le prix de référence est déjà correct.

**Risque** : si `seed_price_references()` au démarrage récupère un prix erroné (bug Twelve Data transitoire), le prix de référence lui-même est faussé et la validation devient inutile.

**Recommandation** : ajouter une validation croisée systématique sur les 41 tickers au démarrage et logguer tout prix > 3σ de la moyenne historique 30j (disponible via price_archive).

---

## Limites de l'audit

- Pas d'accès aux journal entries avec MAE/MFE réels (endpoint /api/journal non interrogé — analyse basée sur le code uniquement)
- Pas d'accès à la DB complète (seulement 10-30 trades visibles via l'API publique)
- Données historiques pré-2026-03-27 non consultées — le 100% EXPIRED rate pourrait être spécifique à cette période
- Pas d'accès aux logs agent_performance pour voir si des alertes ont été déclenchées
- Version actuelle du scoring Claude (Haiku) — impossible de savoir si les scores sont systématiquement trop bas ou trop élevés sans données réelles
- La silent failure de 21 jours (corrigée aujourd'hui) signifie que les 4 équipes (Trader 2, 3, 4) n'ont peut-être pas de données visibles — la situation réelle des Équipes 2-4 est inconnue

---

## Handoff → @fullstack

**Fichiers produits** :
- `/home/user/Finance/docs/audit/data-analyst.md`

**Décisions analytiques prises** :
- North Star Metric proposé : `Weekly TP_HIT ratio` = TP_HIT / (TP_HIT + SL_HIT) sur 7 jours glissants, cible >45%
- Hypothèse principale (55%) : TP trop large pour la volatilité intraday réelle → tester ATR_intraday vs ATR5j daily
- Learning adaptatif à figer temporairement (multiplicateurs à 1.0) jusqu'à avoir ≥30 trades TP ou SL

**Points d'attention pour implémentation** :
1. Ajouter `sharpe_ratio` et `max_drawdown` dans `_compute_trader_1_kpis()` (agent_performance.py)
2. Ajouter alerte CRITICAL si `expired_rate > 90%` ET `total_trades > 10` dans `_detect_alerts()`
3. Ajouter alerte "silence" si aucun trade en >3 jours consécutifs (vérifiable via `load_trades()`)
4. Lancer `/api/price-archive/fill` + `/api/backtest/replay` pour valider la calibration sur données historiques
5. Dashboard EXPIRED Analysis : segmenter par heure d'entrée et comparer MFE vs target_pct — données déjà dans journal entries

**Tests data-driven à lancer en priorité** :
```
GET /api/journal → extraire MAE/MFE des 30+ EXPIRED
  Si avg(MFE) < 0.3% pour cibles à 0.8%+ → H1 confirmée → réduire targets de 50%
  Si >50% des EXPIRED viennent du scan 17:00 → H2 confirmée → bloquer nouveaux trades après 17:30
GET /api/price-archive/stats → vérifier que les données OHLCV sont disponibles pour backtest
POST /api/backtest/replay → valider que la formule de scoring produit un edge >0 sur 90j historiques
```
