# OneShot News Trading System — v2.0 Edge-Detection

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

## Data Sources — 3 phases par priorite d'edge

### Phase 1 : Early-Signal (PRIORITAIRE — info brute, pas encore interpretee)
Sources configurees dans `EARLY_SIGNAL_FEEDS` (14 feeds) :
- **Meteo/Agri** : drought.gov, NOAA CPC, weather.gov (alertes NWS)
- **USDA/FAO** : rapports recoltes, stocks, previsions mondiales
- **Geopolitique** : State Department, IAEA (nucleaire/sanctions)
- **Energie** : EIA (stocks petrole/gaz), OilPrice, gCaptain (maritime/shipping)
- **Banques centrales** : ECB, Federal Reserve, Bank of England (speeches/minutes subtils)

Poids premium : USDA=1.1, NOAA=1.1, EIA=1.1, gCaptain=1.05

### Phase 2 : Yahoo Finance (yfinance)
Prix temps reel, news par ticker, historique de volatilite. Gratuit, pas de cle API. Collecte parallelisee (ThreadPoolExecutor, 10 workers).

### Phase 3 : Medias mainstream (info deja traitee par les algos)
RSS feeds (5 sources) : Reuters, CNBC, Investing.com. Poids reduits : CNBC=0.9, Investing.com=0.7

- **Claude API (Anthropic)**: scoring des news via Sonnet avec tool_use pour structured output + retry exponentiel (max 2 retries). Seul secret requis: `ANTHROPIC_API_KEY`.

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
edge_factor = max(transmission_delay/100 * (1 - market_awareness/100), 0.05)
score = surprise * (freshness/100) * (clarity/100) * edge_factor * source_weight * category_score_mult
```

**Exemples concrets :**
- Earnings Apple (delay=5, awareness=95) → edge_factor = 0.05*0.05 = 0.0025 (floor 0.05) → score ecrase
- Rapport NOAA secheresse (delay=80, awareness=10) → edge_factor = 0.8*0.9 = 0.72 → score booste
- Gel Bresil cafe (delay=90, awareness=5) → edge_factor = 0.9*0.95 = 0.855 → score maximal

### Category Score Multipliers (edge-priority)
```
earnings:           0.2   # Quasi zero-edge — deja price en pre-market
macro:              0.3   # Algos HFT dominent — aucun avantage
m_a:                0.5   # Fort si rumeur, rarement en avance de phase
central_bank_subtle: 0.6  # Speeches secondaires — edge faible
other:              0.7
regulatory:         0.8   # Depend du timing
sector:             1.2   # Liens indirects = edge reel
geopolitical:       1.3   # Fort edge si signal early
commodity:          1.5   # Edge max — signaux physiques
supply_chain:       1.6   # Disruptions logistiques — delai long
weather:            1.8   # Edge maximal — marche met 2-12h a pricer
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
- **Scans**: 2/jour — Europe 07:50 CET, US 14:30 CET
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
- **Scheduler**: job automatique a 22:00 CET chaque jour
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

## Learning adaptatif
- **Par ticker**: ajustement 0.5-1.5 base sur l'historique de resultats
- **Par categorie**: ajustement 0.7-1.3 base sur la performance de la categorie d'actif
- **Blending**: 60% ticker + 40% categorie, borne a [0.5, 1.5]
- **Decay temporel**: demi-vie de 30 jours — les trades recents comptent plus que les anciens
- **File locking**: `fcntl.LOCK_EX` / `fcntl.LOCK_SH` pour acces concurrent sur aux fichiers JSON

## Parametres cles (v2.0)
- Score minimum: 55/100 (releve de 40 — filtre plus strict)
- Ratio risque/rendement minimum: 1.3 (releve de 1.0)
- Freshness peak: < 2h
- News max age: 6h
- Trigger cooldown: 300s (5 min entre deux triggers manuels)
- Schema version: 2

## Secrets (Replit)
- Seul secret necessaire: `ANTHROPIC_API_KEY`
- yfinance et RSS ne necessitent aucune cle

## Deploiement
- Plateforme cible: Replit
- Backend: `uvicorn backend.app.main:app`
- Frontend: Vite dev server ou build statique

## Tests
- Framework: pytest (107 tests)
- Lancer: `cd backend && python -m pytest tests/ -v`
- Couvre: config, models, news_scorer, trade_selector, journal, learning
