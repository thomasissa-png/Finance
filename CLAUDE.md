# OneShot News Trading System — v3.0 Full Intelligence Pipeline

## Philosophie fondamentale (CRUCIAL)
**Notre edge est sur les signaux EN AVANCE DE PHASE — pas les news que tout le monde commente.**

Le systeme est concu pour detecter les **dislocations non encore pricees** par le marche :
- Un rapport meteo NOAA sur une secheresse au Midwest → impact ble/mais 6-12h avant que le marche reagisse
- Un gel au Bresil capte par bulletin local → cafe/sucre avant que Bloomberg reprenne l'info
- Un rapport USDA sur les stocks → commodities physiques avant que les traders de futures reagissent
- Un mouvement militaire capte par OSINT → petrole/or avant les medias mainstream

**Ce qu'on NE cherche PAS :**
- Earnings / resultats d'entreprise → deja prices en pre-market/after-hours par les algos HFT
- Decisions de taux / NFP / CPI → les algos reagissent en microsecondes, zero edge
- Headlines CNN/BBC/trending Twitter → 100% des participants ont deja vu

## Architecture
- **Backend**: FastAPI + APScheduler (Python)
- **Frontend**: React + Vite
- **Persistence**: `data/trades.json` + `data/journal.json` (flat files, file locking via `fcntl`)

## Data Sources — 4 phases par priorite d'edge

### Phase 0 : Structured Data APIs (PRIORITE MAX — donnees chiffrees)
Module `data_apis.py` — donnees numeriques que Claude peut interpreter precisement :
- **Open-Meteo** (GRATUIT, no key) : surveillance meteo 8 zones agricoles critiques
  - US Midwest Corn Belt, Brazil Minas Gerais (cafe/sucre), Brazil Sao Paulo
  - Ukraine/Mer Noire (ble), Inde (ble/riz), Golfe du Mexique (petrole offshore)
  - Asie du Sud-Est (huile palme), Australie (ble)
  - Alertes : gel, canicule, secheresse (<2mm/7j), vents violents (>100km/h)
- **EIA API** (cle gratuite `EIA_API_KEY`) : stocks petrole/gaz/distillats hebdo avec chiffres exacts
- **USDA NASS** (cle gratuite `USDA_API_KEY`) : crop progress, conditions, recoltes
- **GNews** (cle gratuite `GNEWS_API_KEY`, 100 req/jour) : recherche ciblee par mots-cles
  - Queries : "drought frost flood crop", "oil sanctions embargo", "port congestion shipping"
  - "OPEC production cut", "wheat corn harvest", "military strike missile"
- **CFTC COT** (GRATUIT, no key) : positionnement commerciaux vs speculateurs
  - Alertes quand positionnement extreme (>20% OI net)
- **Options Flow** (GRATUIT via yfinance) : put/call ratio extreme, volume spikes

Poids premium : Open-Meteo=1.15, EIA=1.1, USDA=1.1, CFTC=1.05, Options=0.95

### Phase 1 : Early-Signal RSS (info brute, pas encore interpretee)
Sources configurees dans `EARLY_SIGNAL_FEEDS` (14 feeds) :
- **Meteo/Agri** : drought.gov, NOAA CPC, weather.gov (alertes NWS)
- **USDA/FAO** : rapports recoltes, stocks, previsions mondiales
- **Geopolitique** : State Department, IAEA (nucleaire/sanctions)
- **Energie** : EIA (stocks petrole/gaz), OilPrice, gCaptain (maritime/shipping)
- **Banques centrales** : ECB, Federal Reserve, Bank of England (speeches/minutes subtils)

### Phase 2 : Yahoo Finance (yfinance)
Prix temps reel, news par ticker, historique de volatilite. Gratuit, pas de cle API.

### Phase 3 : Medias mainstream (info deja traitee par les algos)
RSS feeds (5 sources) : Reuters, CNBC, Investing.com. Poids reduits : CNBC=0.9, Investing.com=0.7

- **Claude API (Anthropic)**: scoring des news via Sonnet avec tool_use pour structured output + retry exponentiel (max 2 retries). Seul secret requis: `ANTHROPIC_API_KEY`.

## Calendrier Economique
Module `economic_calendar.py` — bloque les trades avant les evenements macro majeurs :
- **FOMC** : dates 2025-2026 hardcodees (publies par la Fed)
- **ECB** : dates 2025-2026 hardcodees
- **BOE** : dates 2025-2026 hardcodees
- **NFP** : premier vendredi de chaque mois (genere dynamiquement)
- **CPI** : ~2eme semaine de chaque mois (approxime)
- **Fenetre de danger** : 2h avant + 1h apres l'evenement
- **Action** : si evenement dans la fenetre, le trade est BLOQUE (zero edge sur macro)
- **Contexte Claude** : les evenements a venir sont injectes dans le prompt

## Scans Evenementiels (reactifs)
Module `event_scanner.py` — surveillance continue des feeds early-signal :
- **Cron** : toutes les 30 minutes pendant les heures de trading (07:00-19:30 CET), lundi-vendredi
- **Weekend** : desactive (weekday check dans `should_trigger_scan()` + `day_of_week` dans CronTrigger)
- **Detection** : mots-cles a fort potentiel dans les titres RSS
  - weather : drought, frost, hurricane, heatwave, flood...
  - supply_chain : pipeline explosion, port closed, canal blocked, embargo...
  - geopolitical : military strike, sanctions, nuclear, invasion...
  - commodity : opec cut, crop failure, stockpile draw, shortage...
- **Trigger** : si signal detecte → scan complet immediat (respecte cooldown 5min)
- **Scan type** : avant 14:00 = europe, apres 14:00 = us

## Scoring — Formule Edge-Weighted (v2.0)

### Criteres d'evaluation (par Claude)
1. **surprise** (0-100) : A quel point l'info est inattendue
2. **directional_clarity** (0-100) : Clarte de la direction d'impact
3. **transmission_delay** (0-100) : CRITIQUE — Temps avant que le marche price pleinement
   - 0 = deja price (earnings, NFP conforme)
   - 20 = algos HFT ont deja reagi (CPI, FOMC)
   - 50 = quelques acteurs ont vu, pas le gros du marche
   - 80 = info specialisee, seuls les experts comprennent (rapport USDA, alerte NOAA)
   - 100 = personne n'a fait le lien avec les actifs
4. **market_awareness** (0-100) : % des participants qui ont DEJA VU l'info
   - 0 = personne (bulletin meteo local)
   - 50 = desk institutionnels
   - 100 = tout le monde (headline CNN, trending Twitter)
5. **direction** : LONG / SHORT / NEUTRAL
6. **impacted_tickers** : tickers directement impactes
7. **news_category** : earnings, macro, geopolitical, regulatory, m_a, sector, commodity, weather, supply_chain, central_bank_subtle, other
8. **reasoning** : explication incluant l'estimation du delai de pricing

### Formule de score
```
edge_factor = max(transmission_delay/100 * (1 - market_awareness/100), 0.01)
score = surprise * (freshness/100) * (clarity/100) * edge_factor * source_weight * category_score_mult
```

**Exemples concrets :**
- Earnings Apple (delay=5, awareness=95) → edge_factor = 0.05*0.05 = 0.0025 (floor 0.01) → score ecrase
- Rapport NOAA secheresse (delay=80, awareness=10) → edge_factor = 0.8*0.9 = 0.72 → score booste
- Gel Bresil cafe (delay=90, awareness=5) → edge_factor = 0.9*0.95 = 0.855 → score maximal

### Category Score Multipliers (edge-priority)
```
earnings:           0.2   # Quasi zero-edge — deja price en pre-market
macro:              0.3   # Algos HFT dominent — aucun avantage
m_a:                0.5   # Fort si rumeur, rarement en avance de phase
central_bank_subtle: 0.6  # Speeches secondaires — edge faible
other:              0.6   # Defaut conservateur
regulatory:         0.6   # Generalement telegraphe, faible edge
sector:             1.2   # Liens indirects = edge reel
geopolitical:       1.3   # Fort edge si signal early
commodity:          1.5   # Edge max — signaux physiques
supply_chain:       1.6   # Disruptions logistiques — delai long
weather:            1.6   # Fort edge mais faux-positifs possibles sur previsions
```

### News Category Multipliers (calibration R/R)
Ajustent target et stop selon la categorie :
- **earnings** : target_mult=0.8, stop_mult=1.2 (petit target, large stop — peu de conviction)
- **weather** : target_mult=1.3, stop_mult=1.0 (gros moves directionnels)
- **commodity** : target_mult=1.2, stop_mult=1.05
- **geopolitical** : target_mult=1.15, stop_mult=1.2 (ambitieux mais volatile)

## Chain Reaction Detector
Detection automatique des effets de second ordre. Le marche est LENT a connecter les impacts indirects.

### Liens configures (`CHAIN_REACTIONS`)
- **Energie** : CL=F (WTI) → TTE.PA, BZ=F, NG=F | BZ=F → CL=F
- **Metaux/safe haven** : GC=F (Or) → SI=F (argent, same), USDCHF=X (CHF, inverse)
- **Agriculture** : ZC=F (Mais) → ZS=F (soja), ZW=F (ble) | KC=F (Cafe) → SB=F (sucre)
- **Luxe/Chine** : MC.PA → RMS.PA, OR.PA (meme exposition consommateur chinois)
- **Cuivre** : HG=F → ^GSPC, ^FCHI (proxy activite industrielle)
- **Yen carry** : USDJPY=X → GC=F (risk-off inverse)

### Fonctionnement
Apres le scoring Claude, `_detect_chain_reactions()` enrichit automatiquement `impacted_tickers` avec les cibles de second ordre. La direction est propagee (same/inverse).

## Strategie
- **Type**: Day trading event-driven (news-based), focus edge detection
- **Scans**: 2/jour — Europe 07:50 CET, US 14:30 CET, **lundi-vendredi uniquement**
- **Weekend**: tous les scans, event checks et journal sont desactives samedi-dimanche (marches fermes). Les triggers manuels retournent HTTP 400 le week-end.
- **Execution**: 0 ou 1 trade par scan
- **Fenetres de sortie**: Europe 09:00-20:00 CET, US 15:30-20:00 CET
- **Cloture**: toutes les positions fermees avant 20:00 CET. Pas d'overnight.
- **Univers**: 49 actifs (15 actions Euronext Paris, 4 metaux, 9 forex, 9 commodities, 12 indices)
- **Filtrage par session**: Europe = Euronext + indices EUR/GBP + metaux/forex/commodities. US = indices USD/JPY/HKD/AUD + metaux/forex/commodities.
- **DST**: toutes les heures utilisent `ZoneInfo("Europe/Paris")` (pas de CET hardcode)

## Calibration R/R (decorrellee)
- **Target**: ATR x (0.25 + score/100 x 0.45) x news_category_target_mult — pondere par le score de confiance
- **Stop**: ATR x 0.4 x news_category_stop_mult — fixe, independant du target
- **R/R variable**: le ratio varie selon le score (plus le score est eleve, plus le target est ambitieux)
- **Planchers**: target min = TARGET_PERCENT, stop min = TARGET_PERCENT x 0.7

## Correlation Groups
Groupes d'actifs correles pour eviter les doubles expositions :
- energy: TTE.PA, CL=F, BZ=F, NG=F
- gold_safe: GC=F, SI=F, USDCHF=X
- risk_on_eu: ^FCHI, ^GDAXI, ^FTSE, ^IBEX, ^FTSEMIB
- risk_on_us: ^GSPC, ^DJI, ^IXIC, ^RUT
- jpy_carry: USDJPY=X, EURJPY=X, ^N225
- luxury: MC.PA, RMS.PA, OR.PA
- agri: ZC=F, ZW=F, ZS=F

## Journal quotidien (22h CET)
- **Scheduler**: job automatique a 22:00 CET chaque jour ouvrable (lundi-vendredi)
- **Actions**: ferme tous les trades PENDING, recupere les prix reels du jour (high/low/close via yfinance)
- **Resultat auto**: TP verifie en priorite (TP > SL quand les deux sont touches le meme jour), puis SL, puis EXPIRED
- **Dedup**: verifie `(ticker, entry_time)` pour eviter les doublons lors de triggers manuels
- **Trades anciens**: recupere les prix historiques pour la date reelle du trade (pas seulement aujourd'hui)
- **Persistence**: `data/journal.json` (flat file avec file locking)
- **Categories de news**: earnings, macro, geopolitical, regulatory, m_a, sector, commodity, weather, supply_chain, central_bank_subtle, other
- **Categories d'actifs**: actions_europe, metaux, forex, commodities, indices
- **Learning**: apres chaque cloture, les resultats alimentent `compute_learning_adjustments()`
- **Frontend**: onglet "Journal" avec tableau groupe par date, badges categorie news et actif
- **API**: `GET /api/journal`, `GET /api/journal/{date}`, `POST /api/journal/trigger`
- **Decision trace** (v3): chaque entree journal stocke le raisonnement complet :
  - `all_scored_news` : toutes les news scorees par Claude avec scores et reasoning
  - `rejection_log` : pourquoi chaque candidat a ete rejete (NEUTRAL, score bas, correle, deja price, R/R)
  - `decision_summary` : pourquoi ce trade a ete selectionne plutot que les alternatives
  - `learning_state` : etat des ajustements learning au moment du scan
  - `raw_claude_score` / `learning_multiplier` : decomposition du score
  - `vix_at_trade` / `market_regime` : contexte marche au moment du trade
  - `predicted_transmission_delay` / `actual_pricing_time_hours` / `delay_accuracy` : tracking precision

## Learning adaptatif (v3.0)
- **Par ticker**: ajustement 0.5-1.5 base sur l'historique de resultats (min 4 trades significatifs)
- **Par categorie d'actif**: ajustement 0.7-1.3 (min 5 trades significatifs)
- **Par categorie de news**: ajustement 0.7-1.3 (min 5 trades significatifs)
- **Par session** (europe/us): ajustement 0.8-1.2 (min 5 trades significatifs)
- **Blending multiplicatif** (v3): `ticker * cat * newscat * hour`, borne a [0.5, 1.5]
  - Avant v3 : blending additif (signaux dilues). Maintenant les signaux se composent.
  - Ex: mauvais ticker (0.6) * mauvaise categorie (0.8) = 0.48 (punition reelle)
- **Significance test** (v3): pseudo t-test (`|mean/stderr| > 1.0`) avant d'appliquer un ajustement
  - Evite les faux signaux sur echantillons trop petits ou trop bruyants
- **Decay temporel adaptatif**: demi-vie 45j (peu de trades) → 30j (beaucoup de trades)
  - Transition graduelle entre 50 et 200 trades clotures
- **Feedback loop Claude** (v3): `build_performance_summary()` injecte dans le prompt :
  - Win rate global et par categorie de news
  - Derniers 15 trades (ticker, direction, resultat, PnL)
  - Detection de biais transmission_delay (surestime/sous-estime)
- **Score decomposition** (v3): chaque trade stocke `raw_claude_score` et `learning_multiplier`
  pour diagnostiquer si un echec vient de Claude ou du learning
- **File locking**: `fcntl.LOCK_EX` / `fcntl.LOCK_SH` pour acces concurrent sur aux fichiers JSON

## Parametres cles (v3.0)
- Score minimum: 55/100 (releve de 40 — filtre plus strict)
- Ratio risque/rendement minimum: 1.3 (releve de 1.0)
- Freshness peak: < 2h
- News max age: 6h
- Trigger cooldown: 300s (5 min entre deux triggers manuels)
- Schema version: 3
- Learning min trades: 5 (global), 4 (par ticker), 5 (par categorie/news_cat/session)
- Learning significance: t-stat > 1.0 requis avant d'appliquer un ajustement

## Secrets (Replit)
- **Requis** : `ANTHROPIC_API_KEY`
- **Optionnels** (gratuits, ameliorent la couverture) :
  - `EIA_API_KEY` : donnees energie EIA (https://www.eia.gov/opendata/register.php)
  - `GNEWS_API_KEY` : recherche news ciblee (https://gnews.io/)
  - `USDA_API_KEY` : donnees agricoles USDA (https://quickstats.nass.usda.gov/api)
- yfinance, Open-Meteo, CFTC COT et RSS ne necessitent aucune cle

## Performance (optimisations v3.1)

### Backend — Parallelisation I/O
- **collect_structured_data()** : 6 sources API en parallele (ThreadPoolExecutor, max_workers=6, timeout 45s)
- **_fetch_market_context()** : 9 appels yfinance en parallele (VIX + indices + trends)
- **select_trade()** : pre-fetch de tous les prix candidats en parallele avant evaluation
- **run_daily_journal()** : pre-fetch des prix de cloture en parallele pour tous les trades pending
- **Claude API** : timeout 60s pour eviter les blocages infinis
- **Scheduler** : retry immediat (pas de `time.sleep()` qui bloquerait le thread scheduler)
- **yfinance/feedparser/RSS** : timeouts sur tous les appels reseau (10-30s)

### Backend — Caches
- **Performance summary** : cache `build_performance_summary()` invalide apres chaque journal
- **Learning adjustments** : cache invalide apres chaque journal (#26)
- **CFTC COT CSV** : cache 24h (publie hebdomadairement, inutile de re-telecharger a chaque scan)
- **Health check yfinance** : cache 5 min (evite de bloquer `/api/health`)

### Frontend — React
- **React.lazy + Suspense** : code splitting par onglet (Dashboard, Journal, History, Performance)
- **ErrorBoundary** : capture les erreurs de rendu avec bouton de recovery
- **Polling intelligent** : Dashboard skip le polling quand l'onglet est masque (`document.hidden`)
- **Loading states** : tous les onglets affichent un etat de chargement pendant le fetch
- **useMemo** : grouping/sorting du journal memoize pour eviter les re-calculs
- **Keys stables** : `timestamp-ticker` au lieu de `key={i}` dans les tables
- **Preconnect** : `<link rel="preconnect">` pour Google Fonts (gain ~100ms)

## Deploiement
- Plateforme cible: Replit
- Backend: `uvicorn backend.app.main:app`
- Frontend: Vite dev server ou build statique

## Tests
- Framework: pytest
- Lancer: `python -m pytest backend/tests/ -v` (depuis la racine du projet)
- Couvre: config, models, news_scorer, trade_selector, journal, learning, economic_calendar, data_apis, event_scanner
- v3 tests ajoutés : significance test, compute_adjustment, multiplicative blending, build_performance_summary
- **test_workflow_e2e.py** (57 tests) : backtest complet du pipeline end-to-end
  - Phase 1 : scoring formula edge cases (weather vs earnings, stale vs fresh, etc.)
  - Phase 2 : trade selection (filtering, session, correlation, pre-move, R/R)
  - Phase 3 : chain reactions (same/inverse/NEUTRAL, dedup)
  - Phase 4 : journal closure (TP/SL/EXPIRED, PnL, delay tracking, scan trace)
  - Phase 5 : learning (significance, blending, feedback loop, adjustments applied)
  - Phase 6 : integration multi-jours (score → select → save → journal → learn)
  - Phase 7 : resilience erreurs (corrupt files, missing data, empty inputs)
- **test_weekend.py** : verification que scans, event checks et triggers sont bloques le week-end
