# Plan : Refonte Team 3 — Pure Intraday 1H

## Contexte
- Passer d'un setup daily (70%) + 1H confirmation (30%) à **1H primaire + Daily filtre**
- Holding max : quelques heures, tout fermé avant 20:00 CET
- Plan Twelve Data Grow : pas de limite crédits, 8 req/min
- 1 seul timeframe de signal : **1H**

## Fichiers impactés

### 1. `backend/app/agents/agent_scoring_3.py` — Refonte indicateurs
**Version : 2.3 → 3.0**

- [ ] **A1** : Changer `TIMEFRAMES` de `["1d", "1h"]` à `["1h"]` (signal unique)
- [ ] **A2** : Changer `TIMEFRAME_WEIGHTS` de `{"1d": 0.70, "1h": 0.30}` à `{"1h": 1.0}`
- [ ] **A3** : Raccourcir les périodes indicateurs pour le 1H :
  - RSI : `[14, 21]` → `[9, 14]`
  - MACD : `12/26/9` → `5/13/4`
  - Bollinger : `20/2.0` → `12/1.8`
  - Stochastic : `14/3` → `9/3`
  - EMA/SMA : `20/50/200` → `9/21` (EMA only)
  - ADX : `14` → `10`
- [ ] **A4** : Changer le fetch primaire :
  - `fetch_history_batch(tickers, period_days=365, interval="1day")` → `fetch_history_batch(tickers, period_days=7, interval="1h")`
  - Ajouter un fetch daily séparé (60j) pour le filtre directionnel uniquement
  - Cacher le daily (1 seul fetch le matin, TTL longue)
- [ ] **A5** : Supprimer SMA 200 du calcul (inutile en intraday)
- [ ] **A6** : Ajouter filtre directionnel daily :
  - SMA 20 daily : LONG autorisé si prix > SMA20, SHORT si prix < SMA20
  - ADX 14 daily : > 25 → momentum strategies OK, < 20 → mean-reversion seulement
- [ ] **A7** : Supprimer `_compute_intraday_confirmation()` (plus de confirmation séparée, 1H EST le signal primaire)
- [ ] **A8** : Ajouter filtre dernier scan (17:00) : ne prendre que des stratégies mean-reversion rapides (RSI, Stochastic) car fenêtre restante < 3h
- [ ] **A9** : Incrémenter version à `3.0`

### 2. `backend/app/agents/agent_trader_3.py` — Holdings intraday
**Version : 2.2 → 3.0**

- [ ] **B1** : Remplacer `STRATEGY_MAX_HOLDING_HOURS` par des valeurs intraday :
  - rsi_snap (ex rsi_reversal) : 3h
  - stoch_reversal : 3h
  - bollinger_breakout (ex bollinger_squeeze) : 4h
  - macd_momentum (ex macd_crossover) : 5h
  - ema_trend (ex ma_trend) : 5h
  - momentum_divergence : 4h
  - Combos mean-rev : 3h
  - Combos momentum : 5h
- [ ] **B2** : Ajouter **hard deadline 19:45 CET** dans le monitor :
  - Si heure >= 19:45 → force-close toutes les positions actives (marge 15min avant 20:00)
  - Résultat : `EOD_CLOSE` (nouveau type, distinct de EXPIRED)
- [ ] **B3** : Holding max dynamique par scan :
  - `max_holding = min(deadline_19h45 - entry_time, strategy_max_hours)`
  - Le plus petit des deux gagne
- [ ] **B4** : Ajuster TP/SL pour l'intraday (plus serrés) :
  - Mean-reversion : TP 0.4-0.8%, SL 0.3-0.5%
  - Momentum : TP 0.6-1.2%, SL 0.4-0.6%
  - Breakout : TP 0.8-1.5%, SL 0.5-0.8%
  - Combos : TP 0.6-1.0%, SL 0.3-0.5%
  - Note : ces fourchettes seront calibrées par ATR, ce sont les bornes
- [ ] **B5** : Trailing stop activation ajustée :
  - Mean-reversion : 40% du target
  - Momentum : 35% du target (abaissé de 50%, fenêtre courte)
  - Breakout : 30% du target (abaissé de 35%)
- [ ] **B6** : Filtre scan 17:00 : rejeter les stratégies momentum (holding 5h > fenêtre 2h45)
- [ ] **B7** : Incrémenter version à `3.0`

### 3. `backend/app/agents/agent_journal_3.py` — Clôture intraday
**Version : 2.1 → 3.0**

- [ ] **C1** : Remplacer le hardcode `3 * 24` (72h) par le max des STRATEGY_MAX_HOLDING_HOURS importé de trader_3
- [ ] **C2** : Force-close à 20:00 CET au lieu de compter sur le monitor seul (sécurité)
- [ ] **C3** : Ajouter le résultat `EOD_CLOSE` dans le tracking (Learning 3 doit le distinguer de EXPIRED)
- [ ] **C4** : Incrémenter version à `3.0`

### 4. `backend/app/main.py` — Scheduler
- [ ] **D1** : Augmenter la fréquence du monitor Trader 3 :
  - De `minute="10,40"` (2x/heure) à `minute="5,20,35,50"` (4x/heure, toutes les 15min)
  - Justification : holdings de 1-5h, besoin de granularité pour trailing stop et TP/SL
- [ ] **D2** : Ajouter un job de force-close à 19:50 CET :
  - Appelle `run_position_monitor_3()` avec flag `force_close_all=True`
  - Sécurité pour s'assurer que rien ne reste ouvert

### 5. `backend/app/agents/agent_learning_3.py` — Adaptation
**Version : 2.1 → 3.0**

- [ ] **E1** : Réduire `DECAY_HALF_LIFE_DAYS` de 30 à 15 (intraday = feedback loop plus rapide)
- [ ] **E2** : Ajouter dimension `scan_time_adj` (07:50 vs 11:15 vs 14:50 vs 17:00) — les scans du matin performent-ils mieux ?
- [ ] **E3** : Tracker `EOD_CLOSE` comme catégorie de sortie séparée (ni win ni loss, signal calibration)
- [ ] **E4** : Incrémenter version à `3.0`

### 6. Tests
- [ ] **F1** : Mettre à jour les tests existants de Scoring 3 (périodes indicateurs, timeframes)
- [ ] **F2** : Ajouter tests pour le hard deadline 19:45 (EOD_CLOSE)
- [ ] **F3** : Ajouter tests pour le filtre scan 17:00 (mean-reversion seulement)
- [ ] **F4** : Ajouter tests pour le holding max dynamique (min de strategy max et deadline)

### 7. CLAUDE.md
- [ ] **G1** : Mettre à jour la section Équipe 3 avec les nouveaux paramètres
- [ ] **G2** : Mettre à jour le tableau des versions agents

## Ordre d'implémentation
1. Scoring 3 (A1-A9) — fondation, les indicateurs
2. Trader 3 (B1-B7) — exécution, holdings, exits
3. Journal 3 (C1-C4) — clôture cohérente
4. Learning 3 (E1-E4) — adaptation feedback
5. Scheduler (D1-D2) — fréquence monitor
6. Tests (F1-F4)
7. CLAUDE.md (G1-G2)

## Points d'attention
- Le Daily devient un **filtre** (direction SMA 20, régime ADX), pas un signal. Un seul fetch le matin, caché toute la journée.
- Le 1H devient le **seul timeframe de signal**. Toutes les stratégies y sont recalibrées.
- `_compute_intraday_confirmation()` est supprimé — plus besoin de "confirmer" le daily par du 1H.
- Le monitor passe à 15min de fréquence (4x/h au lieu de 2x/h).
- Hard deadline 19:45 CET pour le force-close (pas 20:00, marge de sécurité).
- Budget API : ~4 batch calls par scan (3 groupes 1H + 1 daily caché). Confortable avec le plan Grow.
