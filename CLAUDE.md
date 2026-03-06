# OneShot News Trading System — v3.4 Full Intelligence Pipeline

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
- **Persistence**: PostgreSQL (primary, via `DATABASE_URL`) avec fallback JSON flat files
  - `database.py`: connection pool (psycopg2, min=2 max=10), tables trades/journal_entries/scan_history/last_scans
  - Fallback: `data/trades.json` + `data/journal.json` + `data/scan_history.json` + `data/last_scans.json` (file locking via `fcntl`)
  - Auto-migration JSON→PG au demarrage si PG est vide mais JSON a des donnees
- **Market Data**: Twelve Data (primary) + yfinance (fallback) — module `market_data.py`

## Branche active : `claude/json-to-postgres-migration-ToNVq`

### Travail effectue sur cette branche (20 commits)

#### 1. Migration JSON → PostgreSQL (`database.py`, `migrate_json_to_postgres.py`)
- 4 tables PG : `trades`, `journal_entries`, `scan_history`, `last_scans`
- CRUD complet avec `psycopg2.extras.RealDictCursor` et JSONB pour champs complexes
- `ON CONFLICT DO NOTHING` pour dedup (trades: ticker+timestamp, journal: ticker+entry_time)
- `is_pg_enabled()` → True si psycopg2 importe ET DATABASE_URL set
- Script de migration : `python -m backend.migrate_json_to_postgres`
- Tous les modules (journal, learning, scan_history, scheduler, main) utilisent PG quand disponible
- Auto-migration : si PG est vide mais JSON a des donnees, migration automatique au load

#### 2. Migration Market Data vers Twelve Data (`market_data.py`)
- Module unifie : Twelve Data (primary) + yfinance (fallback)
- Rate limiter thread-safe : 7 req/min (free tier = 8)
- TTL cache 3 niveaux : 2min (quotes), 10min (daily), 30min (intraday)
- Mapping complet des 39 tickers yfinance → Twelve Data, verifie via API :
  - **Indices** : ^FCHI→FCHI, ^GDAXI→GDAXI, ^FTSE→FTSE, ^N225→N225
  - **US indices blacklistes** : ^GSPC, ^DJI, ^IXIC, ^RUT, ^VIX → yfinance only (licences S&P)
  - **Forex** : EURUSD=X→EUR/USD, USDJPY=X→USD/JPY, etc.
  - **Paris stocks** : TTE.PA→TTE (mic_code=XPAR), MC.PA→MC, etc.
  - **Energie** : CL=F→CL1, BZ=F→CO1, NG=F→NG/USD
  - **Metaux** : GC=F→XAU/USD, SI=F→XAG/USD, HG=F→HG1, PL=F→XPT/USD, PA=F→XPD/USD
  - **Agriculture** : ZC=F→C_1, ZW=F→W_1, ZS=F→S_1, KC=F→KC1, SB=F→SB1, CC=F→CC1, CT=F→CT1, OJ=F→JO1
  - **Livestock** : LE=F→LC1, HE=F→LH1
- Fonctions : `fetch_price()`, `fetch_history()`, `fetch_history_range()`, `fetch_intraday()`
- Secret optionnel : `TWELVE_DATA_API_KEY` (free tier 800 credits/jour)

#### 3. Fixes Journal (4 root causes corrigees — commit 389b649)
- **misfire_grace_time** : 60s → 3600s pour le job journal 22h (pas de risque crash loop — pur data processing)
- **Startup recovery** : `_recover_pending_trades_on_startup()` detecte et ferme les vieux trades PENDING au boot
- **Trades stuck PENDING** : `update_trade_result()` toujours appele meme si exit_price=None (fallback entry_price)
- **Trigger format** : reponse toujours `{entries: [...], diagnostic: {...}}` (plus de format inconsistant)
- **Debug endpoint** : `GET /api/journal/debug` — inspecte PG vs JSON, parse errors, pending trades

#### 4. Audit complet — 17 fixes (commit 1c15010)
- Scoring: freshness retiree de la formule, edge_factor floor 0.05, category multipliers recalibres
- Journal: post-entry price tracking (1h bars, filtre bars avant l'entree du trade)
- Threading: shutdown(wait=False, cancel_futures=True) partout
- Calendar: dates 2025-2026 hardcodees FOMC/ECB/BOE

#### 5. Learning v3.4 — audit ML (commit 42709d2)
- 10 fixes : structured return format, session/newscat/regime adj separees, per-ticker stricter significance
- Signal PnL-signe, t-stat configurable, decay temporel adaptatif, anti-double-counting

#### 6. Cross-day dedup + Journal diagnostics (commit ffc965b)
- Dedup Jaccard ticker cross-day, diagnostic wrapper pour trigger

#### 7. Resilient data loading (commit 688271d)
- Resilient per-entry parsing (skip invalids instead of crash)
- Auto-migration JSON→PG on load

### Etat actuel des fichiers cles
- `backend/app/main.py` : 4 scans + journal 22h + startup recovery + keepalive + debug endpoints
- `backend/app/market_data.py` : Twelve Data + yfinance, 39 mappings verifies
- `backend/app/database.py` : PG persistence layer, 4 tables, pool, CRUD
- `backend/app/journal.py` : fermeture trades, intraday 1h bars, post-entry filtering, PG save
- `backend/app/learning.py` : v3.4, 5 dimensions (ticker, cat, session, newscat, regime)
- `backend/app/scan_history.py` : PG support, pruning 30j
- `backend/migrate_json_to_postgres.py` : script de migration one-shot

## REGLE ABSOLUE — Protection des donnees de production

**JAMAIS committer les fichiers `data/*.json` dans git.**

Ces fichiers contiennent les trades reels, le journal, l'historique de scans.
Ils sont dans `.gitignore` et crees automatiquement au demarrage par les guards `_ensure_file()`.
Un commit accidentel de ces fichiers = **perte de toutes les donnees a chaque deploiement**.

Fichiers proteges :
- `data/trades.json` — tous les trades passes et pending
- `data/journal.json` — journal quotidien avec analyse post-trade
- `data/scan_history.json` — historique de tous les scans (audit trail)
- `data/last_scans.json` — cache des derniers scans (volatile)

Regles :
1. **Ne JAMAIS `git add data/`** ou `git add -A` sans verifier
2. **Ne JAMAIS ecraser** ces fichiers (write_text, truncate) sans lire d'abord
3. **Toujours utiliser file locking** (`fcntl.LOCK_EX` / `LOCK_SH`) pour les acces concurrents
4. **Toujours avoir un guard `_ensure_file()`** qui cree le fichier vide `[]` si absent
5. **Les tests `test_data_persistence.py`** verifient ces regles — ne pas les supprimer

## Data Sources — 4 phases par priorite d'edge

### Phase 0 : Structured Data APIs (PRIORITE MAX — donnees chiffrees)
Module `data_apis.py` — 11 sources de donnees numeriques que Claude peut interpreter precisement :
- **Open-Meteo** (GRATUIT, no key) : surveillance meteo 16 zones agricoles critiques
  - US Midwest Corn Belt, Brazil Minas Gerais (cafe/sucre), Brazil Sao Paulo, Brazil Rio Grande do Sul (soja/mais)
  - Ukraine/Mer Noire (ble), Inde Punjab/Haryana (ble/riz), Argentine Pampas (soja/mais/ble)
  - Golfe du Mexique (petrole offshore), Asie du Sud-Est (huile palme), Australie (ble)
  - **NOUVEAU** : Cote d'Ivoire (cacao), Ghana (cacao), US South Texas (coton), Inde Gujarat (coton), Floride (OJ), Bresil SP (OJ)
  - Alertes : gel (seuils calibres par culture), canicule, stress thermique cumule (3j+ au-dessus du seuil)
  - Secheresse avec seuils adaptatifs (periode critique vs normale)
  - Periodes critiques : silking mais (Jun-Aug), grain fill ble, floraison soja (Dec-Feb hemisph. sud)
- **EIA API** (cle gratuite `EIA_API_KEY`) : stocks petrole/gaz/distillats + utilisation raffineries hebdo
  - Seuil de significance : |change_pct| >= 0.5% (ignore le bruit de rounding)
  - Poids: lit `SOURCE_WEIGHTS["EIA"]` (1.15) au lieu d'un hardcode
- **USDA NASS** (cle gratuite `USDA_API_KEY`) : crop progress, conditions, recoltes
  - Queries saisonnieres : planting (Apr-Jun), condition (May-Sep), emergence (May-Jul), harvest (Sep-Dec)
  - Ble condition : toute l'annee (winter wheat)
  - Delta WoW : calcul automatique semaine vs semaine, flag [SWING MAJEUR] si >= 5pp
- **GNews** (cle gratuite `GNEWS_API_KEY`, 100 req/jour) : recherche ciblee par mots-cles — 25 queries
  - Consolide : frost+coffee frost en 1, drought+harvest en 1, palm oil+China import en 1, PT queries en 1
  - Ajoute : cocoa Ghana/Ivory Coast, cotton Texas/India, orange juice Florida/citrus greening
  - **Betail** : "cattle disease screwworm BSE", "African swine fever pork hog"
  - 4 scans/jour x 25 queries = 100 req/jour, exactement au plafond free tier
  - v3.2: +4 queries (Suez Canal, China demand, EU energy crisis, Middle East tensions)
  - v3.2: 2 queries geopolitiques consolidees en 1 → slot libere pour PGM
  - **PGM** : "Eskom load shedding platinum Nornickel sanctions palladium" (PL=F, PA=F)
  - Originales : "frost freeze crop damage", "oil sanctions military strike missile pipeline embargo"
  - "port congestion shipping", "copper mine strike", "Baltic dry index shipping freight"
  - "OPEC production cut", "wheat corn soybean USDA", "gold reserve central bank"
  - "natural gas storage Europe TTF LNG", "palm oil export China import soybean"
  - "hurricane tropical storm Gulf Mexico"
  - **Portugais** : "geada cafe Minas Gerais frio", "seca milho soja safra quebra" (12-24h avant medias EN)
  - **Maladies/engrais** : "wheat rust crop disease blight", "fertilizer potash phosphate shortage"
  - **Betail** : "avian flu bird flu livestock disease outbreak"
- **CFTC COT** (GRATUIT, no key) : positionnement commerciaux vs speculateurs
  - Alertes sur CHANGEMENTS hebdo (>8pp swing en 1 semaine = signal fort)
  - Alertes positionnement extreme commerciaux (>20% OI net)
  - Alertes speculateurs surexposes (>25% long ou < -20% short = risque reversal)
  - Detection divergence commerciaux vs speculateurs (signal contrarian)
- **Options Flow** (GRATUIT via yfinance) : put/call ratio extreme, volume spikes, IV skew
  - EU equities : TTE.PA, MC.PA, BNP.PA, SAN.PA, AI.PA (seuil P/C > 3.0)
  - US ETFs : SPY, QQQ, USO, GLD, SLV, CORN, WEAT (seuil P/C > 1.5)
  - Seuil volume calls US ETFs : 50000 (was 500 — SPY trade millions/jour)
  - Check 3 expirations les plus proches (was 1 — smart money utilise souvent la 2e/3e)
  - Mappage ETF→tickers : SPY→^GSPC, USO→CL=F/BZ=F, GLD→GC=F, CORN→ZC=F, WEAT→ZW=F
  - IV skew analysis : put IV >> call IV (+25%) = smart money hedging baissier
- **NASA EONET** (GRATUIT, no key) : Earth Observatory Natural Events Tracker
  - Evenements : tempetes, feux de foret, volcans, inondations, seismes
  - Filtrage geographique : 9 regions commodity (US Midwest, Bresil, Golfe, Ukraine, SE Asia, etc.)
  - Evenements hors zones commodity = ignores (reduit faux positifs)
  - Retry automatique sur 503 (serveur sous charge)
- **GIE AGSI** (cle gratuite `GIE_AGSI_API_KEY`) : stockage gaz europeen
  - Donnees EU aggregate + pays individuels (DE, FR, NL, IT)
  - Seuils saisonniers : hiver (draw) vs ete (injection) — niveaux normaux differents
  - Detection stress regional masque par l'agregat EU (ex: Allemagne a 20% = crise)
  - Impact : NG=F (gaz naturel)
- **USDA WASDE** (cle gratuite `USDA_API_KEY`) : World Agri Supply and Demand Estimates
  - Production et rendement mais/soja/ble — rapport mensuel le plus market-moving en agri
  - Detection revision vs estimation precedente (seuil 0.5%)
  - Impact : ZC=F, ZS=F, ZW=F
- **CME FedWatch** (GRATUIT via yfinance ZQ=F) : taux implicites Fed Funds futures
  - Taux implicite = 100 - prix du future, variation 5j en bps
  - Seuil alerte : |shift| >= 5bps (repricing significatif)
  - Forward guidance : spread M+1/M+2/M+3 vs spot (>15bps = anticipation forte) — tickers dynamiques (calcules a partir de la date courante)
  - Impact : GC=F (inverse), EURUSD=X, ^GSPC, USDJPY=X
- **SHFE/LME Inventaires** (GRATUIT via yfinance) : proxy inventaires metaux via volume/prix
  - Volume anormal (>2x moy 20j) = mouvement inventaires physiques
  - Mouvement mensuel >5% = tightness ou surplus
  - Impact : HG=F (cuivre)

Poids premium (v3.3) : Open-Meteo=1.2 (via SOURCE_WEIGHTS), EIA=1.15, NHC=1.15, NOAA=1.1, USDA=1.1, NASA EONET=1.1, GIE AGSI=1.15, CFTC=1.1, Options=1.0 (via SOURCE_WEIGHTS), FedWatch=1.0, SHFE=1.1, OilPrice=0.9
- Tous les poids lus via `SOURCE_WEIGHTS.get()` — plus de hardcodes dans data_apis.py
- GNews: poids variable par categorie query (weather=1.0, commodity/supply_chain=0.95, geopolitical=0.85)

### Phase 1 : Early-Signal RSS (info brute, pas encore interpretee)
Sources configurees dans `EARLY_SIGNAL_FEEDS` (20 feeds — v3.3 nettoye) :
- **Meteo/Agri** : Drought.gov (US drought monitor), SPC (orages), NWS (alertes), api.weather.gov (ATOM), NHC (ouragans Atlantique), Climate.gov (ENSO/outlooks)
- **USDA/FAO** : NASS reports (recoltes, stocks), FAO newsroom
- **Geopolitique/Defense** : war.gov (operations militaires, geopolitique), IAEA (nucleaire/sanctions)
- **Energie** : EIA Today in Energy, OilPrice
- **Maritime/Shipping** : gCaptain, MarineLink, Maritime Executive, Splash247 (ports, containers, BDI)
- **Canal chokepoints** : Panama Canal Authority (ACP) — transit disruptions
  - v3.2: Suez Canal Authority retire (page HTML, pas RSS — jamais parsee correctement)
  - Couvert par GNews query "Suez Canal disruption" a la place
- **Banques centrales** : ECB (corrige .html→.xml), Federal Reserve, Bank of England (speeches)

**Feeds remplaces** : NCEI news.xml (mort)→Drought.gov+Climate.gov, Defense.gov→war.gov, ECB .html→.xml
**v3.2** : Defense.gov fallback retire (redondant avec war.gov), Suez Canal retire (HTML pas RSS)→GNews query

### Phase 2 : Yahoo Finance (yfinance)
Prix temps reel, news par ticker, historique de volatilite. Gratuit, pas de cle API.

### Phase 3 : Medias mainstream (info deja traitee par les algos)
RSS feeds (5 sources) : BBC Business + World, CNBC World + Business, Investing.com. Poids reduits : BBC=0.85, CNBC=0.9, Investing.com=0.7
**Note** : Reuters feeds.reuters.com DNS dead → remplace par BBC

- **Claude API (Anthropic)**: scoring des news via Sonnet avec tool_use pour structured output + retry exponentiel (max 2 retries). Headlines incluent la description RSS quand disponible (contexte enrichi). WARNING logs si GNEWS_API_KEY ou EIA_API_KEY manquantes. Seul secret requis: `ANTHROPIC_API_KEY`.
  - **Pre-filtrage** : cap a 50 items max avant Claude (heuristique source_weight + freshness + description)
  - **Batching** : si > 50 items, decoupe en batchs de 50 pour eviter timeout API
  - **Timeout** : 90s par batch (50 items ~30s processing, marge pour queueing API)

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

## Scans Evenementiels (reactifs) — NON ACTIFS
Module `event_scanner.py` — code implemente mais **non wire dans le scheduler** (`run_event_check()` n'est jamais schedule dans `main.py`).
Fonctionnalite disponible pour activation future :
- **Cron prevu** : toutes les 30 minutes pendant les heures de trading (07:00-19:30 CET), lundi-vendredi
- **Weekend** : desactive (weekday check dans `should_trigger_scan()` + `day_of_week` dans CronTrigger)
- **Detection** : mots-cles a fort potentiel dans les titres RSS (v3.1 — elargi)
  - weather : drought, frost, hurricane, typhoon, tropical storm, el nino, la nina, wildfire, volcanic eruption, earthquake, monsoon failure, record heat/cold...
  - supply_chain : pipeline explosion, port closed, canal blocked, embargo, container shortage, baltic dry, freight rate surge, vessel grounding, lng terminal, strategic reserve...
  - geopolitical : military strike, sanctions, nuclear, invasion, carrier strike group, no-fly zone, military buildup, arms deal...
  - commodity : opec cut, crop failure, stockpile draw, shortage, gas storage, ttf price, palm oil export, coffee frost, china import, wheat export ban...
  - **v3.2 livestock** : african swine fever, avian flu, bird flu, foot-and-mouth, bse, mad cow, screwworm, herd liquidation, cattle disease, swine fever...
- **Keywords prioritaires** : hurricane warning, pipeline explosion, military strike, export ban... → bypass cooldown categorie
- **Cooldowns par categorie** : geopolitique=60s, supply_chain=120s, commodity/weather=300s (minimum global 30s)
- **Trigger** : si signal detecte → scan complet immediat (respecte cooldown par categorie)
- **Scan type** : avant 14:00 = europe, apres 14:00 = us

## Scoring — Formule Edge-Weighted (v3.1)

### Criteres d'evaluation (par Claude)
1. **surprise** (0-100) : A quel point l'info est inattendue
2. **freshness** (0-100) : Calculee automatiquement (age de la news). **NON incluse dans la formule** — stockee pour le journal/tracing uniquement. Claude voit deja l'age "[il y a X.Xh]" et ajuste transmission_delay en consequence. L'inclure causerait une double penalisation.
3. **directional_clarity** (0-100) : Clarte de la direction d'impact
4. **transmission_delay** (0-100) : CRITIQUE — Temps avant que le marche price pleinement
   - 0 = deja price (earnings, NFP conforme)
   - 20 = algos HFT ont deja reagi (CPI, FOMC)
   - 50 = quelques acteurs ont vu, pas le gros du marche
   - 80 = info specialisee, seuls les experts comprennent (rapport USDA, alerte NOAA)
   - 100 = personne n'a fait le lien avec les actifs
5. **market_awareness** (0-100) : % des participants qui ont DEJA VU l'info
   - 0 = personne (bulletin meteo local)
   - 50 = desk institutionnels
   - 100 = tout le monde (headline CNN, trending Twitter)
6. **direction** : LONG / SHORT / NEUTRAL
7. **impacted_tickers** : tickers directement impactes
8. **news_category** : earnings, macro, geopolitical, regulatory, m_a, sector, commodity, weather, supply_chain, central_bank_subtle, other
9. **reasoning** : explication incluant l'estimation du delai de pricing

### Formule de score (v3.1 — freshness retiree)
```
edge_factor = max(transmission_delay/100 * (1 - market_awareness/100), 0.05)
score = surprise * (clarity/100) * edge_factor * source_weight * category_score_mult
```
**Changements v3.1 vs v2.0 :**
- freshness retiree de la formule (evite double penalisation avec transmission_delay)
- edge_factor floor releve de 0.01 → 0.05 (empeche l'ecrasement total des scores mid-range)
- MIN_SCORE_THRESHOLD abaisse de 55 → 25 (formule multiplicative trop punitive a 55)

**Exemples concrets :**
- Earnings Apple (delay=5, awareness=95) → edge_factor = 0.05*0.05 = 0.0025 (floor 0.05) → score ecrase
- Rapport NOAA secheresse (delay=80, awareness=10) → edge_factor = 0.8*0.9 = 0.72 → score booste
- Gel Bresil cafe (delay=90, awareness=5) → edge_factor = 0.9*0.95 = 0.855 → score maximal

### Hard-caps sur categories zero-edge (v3.2 — stratifie)
- **earnings** : transmission_delay force a 5 si > 15, market_awareness force a >= 90
- **macro** : transmission_delay force a 10 si > 20, market_awareness force a >= 85
- **m_a** (v3.2) : 2 niveaux :
  - **M&A confirme** ("confirms", "agrees to buy") → delay=5, awareness>=90 (comme earnings)
  - **M&A rumeur** ("talks", "in discussions") → delay cap a 40, awareness>=50 (edge reel)
- **central_bank_subtle** (v3.2) : 3 niveaux :
  - **Decision de taux** ("rate decision", "holds rates") → delay=5, awareness>=95 (HFT domine)
  - **Discours president** ("Powell", "Lagarde") → delay cap a 25, awareness>=70
  - **Discours secondaire** (regional Fed, membre ECB) → delay cap a 45, awareness>=50 (edge max)

### Category Score Multipliers (edge-priority)
```
earnings:           0.2   # Quasi zero-edge — deja price en pre-market
macro:              0.3   # Algos HFT dominent — aucun avantage
m_a:                0.7   # Fort si rumeur, rarement en avance de phase (releve de 0.5)
central_bank_subtle: 0.8  # Speeches secondaires — edge faible mais non nul (releve de 0.6)
other:              0.8   # Defaut moins punitif (releve de 0.6)
regulatory:         0.9   # Peut avoir de l'edge si signal early (releve de 0.6)
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
- **Metaux/safe haven** : GC=F (Or) → SI=F (same), USDCHF=X (inverse), EURUSD=X (same), USDJPY=X (inverse, risk-off)
- **Agriculture** : ZC=F (Mais) → ZS=F, ZW=F (same), LE=F, HE=F (inverse — feed cost) | KC=F (Cafe) → SB=F (sucre)
- **Luxe/Chine** : MC.PA → RMS.PA, OR.PA (meme exposition consommateur chinois)
- **Cuivre** : HG=F → ^GSPC, ^FCHI (proxy industriel), AUDUSD=X (same — Australie producteur)
- **PGM** : PL=F ↔ PA=F (memes mines sud-africaines — disruption impacte les deux)
- **Yen carry** : USDJPY=X → GC=F (risk-off inverse)

### Fonctionnement
Apres le scoring Claude, `_detect_chain_reactions()` enrichit automatiquement `impacted_tickers` avec les cibles de second ordre. La direction est propagee (same/inverse).

## Strategie
- **Type**: Day trading event-driven (news-based), focus edge detection
- **Scans**: 4/jour — **lundi-vendredi uniquement**
  - **07:50 CET** (Europe) : capte overnight US/Asie, rapports meteo nuit — edge max (12h+ de delay)
  - **11:15 CET** (Mid-Session) : capte PMIs matin, donnees EU, meteo actualisee — 9h de trading restant
  - **14:50 CET** (Pre-US) : decale de 14:30 pour eviter conflit NFP/CPI — les algos ont reagi, on capte le second ordre
  - **17:00 CET** (US Session) : capte EIA petrole (mer 16:30), ISM (16:00), WASDE mensuel, reaction US open
- **Scan keys**: `europe`, `mid_session`, `us`, `us_session` (cache + trigger API)
- **Mapping scan → actifs**: europe/mid_session → ScanType.EUROPE, us/us_session → ScanType.US
- **Weekend**: tous les scans, event checks et journal sont desactives samedi-dimanche (marches fermes). Les triggers manuels retournent HTTP 400 le week-end.
- **Execution**: 0 ou 1 trade par scan
- **Fenetres de sortie**: Europe 09:00-20:00 CET, US 15:30-20:00 CET
- **Cloture**: toutes les positions fermees avant 20:00 CET. Pas d'overnight.
- **Univers**: 39 actifs (7 actions Euronext Paris, 4 metaux, 6 forex, 14 commodities, 8 indices)
  - v3.3: elagage de 15 actifs sans source dediee (8 actions, 3 forex, 4 indices)
- **Filtrage par session**: Europe = Euronext + indices EUR/GBP + metaux/forex/commodities. US = indices USD/JPY/HKD/AUD + metaux/forex/commodities.
- **Correlation portfolio**: chaque scan verifie les trades de TOUS les autres scans (pas seulement l'autre session)
- **DST**: toutes les heures utilisent `ZoneInfo("Europe/Paris")` (pas de CET hardcode)

## Calibration R/R (decorrellee)
3 tiers de volatilite :
- **Low-vol** (ATR < 1%, forex/large indices) : factor=0.35+score/100*0.55, stop=0.5 (plus large pour absorber le bruit)
- **Normal** (ATR 1-5%) : factor=0.25+score/100*0.45, stop=0.4
- **High-vol** (ATR > 5%, NG, small-cap) : factor=0.15+score/100*0.30, stop=0.3 (plus serre, target realiste)
- **Target**: ATR x score_factor x news_category_target_mult — pondere par le score de confiance
- **Stop**: ATR x stop_fraction x news_category_stop_mult — fixe, independant du target
- **R/R variable**: le ratio varie selon le score (plus le score est eleve, plus le target est ambitieux)
- **Planchers**: target min = TARGET_PERCENT, stop min = TARGET_PERCENT x 0.7

## Correlation Groups
Groupes d'actifs correles pour eviter les doubles expositions :
- energy: TTE.PA, CL=F, BZ=F, NG=F
- gold_safe: GC=F, SI=F, USDCHF=X
- risk_on_eu: ^FCHI, ^GDAXI, ^FTSE
- risk_on_us: ^GSPC, ^DJI, ^IXIC, ^RUT
- jpy_carry: USDJPY=X, EURJPY=X, ^N225
- luxury: MC.PA, RMS.PA, OR.PA
- agri: ZC=F, ZW=F, ZS=F
- tropical_soft: KC=F, SB=F, CC=F, OJ=F
- livestock: LE=F, HE=F
- pgm: PL=F, PA=F

## Journal quotidien (22h CET)
- **Scheduler**: job automatique a 22:00 CET chaque jour ouvrable (lundi-vendredi)
  - `misfire_grace_time=3600` (1h) — le journal est du pur data processing, pas de risque crash loop
  - Si le job est rate (app down), la startup recovery ferme les vieux trades au redemarrage
- **Startup recovery** : `_recover_pending_trades_on_startup()` dans `main.py`
  - Au demarrage, detecte les trades PENDING de jours precedents
  - Lance automatiquement `run_daily_journal()` pour les fermer
  - Evite les trades bloques PENDING quand l'app est tuee avant 22h
- **Actions**: ferme tous les trades PENDING, recupere les prix reels du jour (1h bars via Twelve Data/yfinance)
- **Resultat auto**: TP verifie en priorite via bars chronologiques (1h), puis SL, puis EXPIRED
  - Bars filtrees pour ne garder que post-entry (ignore les extremes d'avant le trade)
  - Si TP et SL touches dans la meme barre 1h → conservatif SL
- **Dedup**: verifie `(ticker, entry_time)` pour eviter les doublons lors de triggers manuels
- **Trades anciens**: recupere les prix historiques pour la date reelle du trade (pas seulement aujourd'hui)
- **Persistence**: PostgreSQL (primary) ou `data/journal.json` (fallback)
  - Resilient: si une entree echoue au parsing, elle est skippee (pas de crash)
  - Auto-migration JSON→PG si PG est vide mais JSON a des donnees
- **Prix indisponibles** : si le fetch de prix echoue, le trade est marque EXPIRED avec entry_price comme fallback
  - `update_trade_result()` est TOUJOURS appele (plus de trades stuck PENDING)
- **Categories de news**: earnings, macro, geopolitical, regulatory, m_a, sector, commodity, weather, supply_chain, central_bank_subtle, other
- **Categories d'actifs**: actions_europe, metaux, forex, commodities, indices
- **Learning**: apres chaque cloture, les resultats alimentent `compute_learning_adjustments()`
- **Frontend**: onglet "Journal" avec tableau groupe par date, badges categorie news et actif
- **API**: `GET /api/journal`, `GET /api/journal/{date}`, `POST /api/journal/trigger`, `GET /api/journal/debug`
  - `/api/journal/trigger` retourne toujours `{entries: [...], diagnostic: {...}}`
  - `/api/journal/debug` : diagnostic PG vs JSON, parse errors, pending trades details
- **Decision trace** (v3): chaque entree journal stocke le raisonnement complet :
  - `all_scored_news` : toutes les news scorees par Claude avec scores et reasoning
  - `rejection_log` : pourquoi chaque candidat a ete rejete (NEUTRAL, score bas, correle, deja price, R/R)
  - `decision_summary` : pourquoi ce trade a ete selectionne plutot que les alternatives
  - `learning_state` : etat des ajustements learning au moment du scan
  - `raw_claude_score` / `learning_multiplier` : decomposition du score
  - `vix_at_trade` / `market_regime` : contexte marche au moment du trade
  - `predicted_transmission_delay` / `actual_pricing_time_hours` / `delay_accuracy` : tracking precision

## Learning adaptatif (v3.4 — audit ML)
- **Par ticker**: ajustement 0.5-1.5 (v3.4: min **8** trades, t-stat > **1.5** — plus strict pour eviter overfitting sur petits echantillons)
- **Par categorie d'actif**: ajustement 0.7-1.3 (min 5 trades, t-stat > 1.0)
- **Par categorie de news**: ajustement 0.7-1.3 (min 5 trades, t-stat > 1.0) — v3.4: **applique contextuellement** par la news_category du trade courant (pas moyennee sur l'historique du ticker)
- **Par session** (europe/us): ajustement 0.8-1.2 (min 5 trades, t-stat > 1.0) — v3.4: **applique par le scan courant** (pas le dernier scan du ticker)
- **Par regime VIX** (v3.4 NEW): ajustement 0.7-1.3 par regime calm/normal/elevated/stress (min 5 trades)
  - Permet de differencier "les trades weather marchent en regime calm" vs "echouent en regime stress"
- **Blending multiplicatif** (v3.4): base = `ticker * cat`, les dimensions contextuelles (session, newscat, regime) sont appliquees **au moment du trade** par `select_trade()`
  - Avant v3.4 : session/newscat bakes dans le blend de maniere statique (bugs #2/#4 corriges)
  - v3.4: `final_mult = base(ticker*cat) * session_adj[current_scan] * newscat_adj[current_newscat] * regime_adj[current_regime]`
  - Ex: mauvais ticker (0.6) * regime stress (0.8) = 0.48 (punition reelle en regime defavorable)
- **Significance test** (v3.4): pseudo t-test avec **seuil configurable par dimension**
  - Per-ticker: t > 1.5, min 8 trades (strict — echantillons petits, bruit eleve)
  - Per-category/session/newscat/regime: t > 1.0, min 5 trades (pool plus large)
- **Signal PnL-signe** (v3.4): `_compute_adjustment()` utilise le PnL signe normalise au lieu du binaire win_rate
  - Un EXPIRED a +0.8% contribue positivement (pas traite comme un echec)
  - Formule: `1.0 + clamp(avg_pnl / 2, -cap, cap) * sensitivity`
- **Decay temporel adaptatif**: demi-vie 45j (peu de trades) → 30j (beaucoup de trades)
  - Transition graduelle entre 50 et 200 trades clotures
- **Feedback loop Claude** (v3.4): `build_performance_summary()` injecte dans le prompt :
  - Win rate global avec **benchmark baseline 50%** pour comparaison
  - PnL moyen par categorie de news (avec benchmark)
  - Derniers 15 trades (ticker, direction, resultat, PnL)
  - Detection de biais transmission_delay (surestime/sous-estime)
  - **Anti-double-counting** (v3.4): note explicite demandant a Claude de NE PAS ajuster ses scores en fonction de l'historique (le learning s'en charge automatiquement)
- **Score decomposition** (v3/v3.4): chaque trade stocke `raw_claude_score` et `learning_multiplier`
  pour diagnostiquer si un echec vient de Claude ou du learning
- **Decomposition par dimension** (v3.4): logs detaillent `ticker_mult`, `cat_mult`, `session_mult`, `newscat_mult`, `regime_mult` pour chaque trade (diagnostic)
- **learning_helped tracking** (v3.4): chaque trade tracke si le learning a booste ou penalise la selection
- **Cache learning** : invalide apres le journal 22h. Les trades ajoutes en journee ne sont PAS pris en compte avant le prochain journal (acceptable pour 4 scans/jour)
- **Return format** (v3.4): `compute_learning_adjustments()` retourne un dict structure :
  - `adjustments`: dict[ticker, multiplier] — base blend ticker*cat
  - `session_adj`: dict[scan_type, multiplier] — applique par scan courant
  - `newscat_adj`: dict[news_category, multiplier] — applique par news courante
  - `regime_adj`: dict[regime, multiplier] — applique par VIX courant
  - `decomposition`: dict[ticker, {ticker_mult, cat_mult}] — pour diagnostics
- **File locking**: `fcntl.LOCK_EX` / `fcntl.LOCK_SH` pour acces concurrent sur aux fichiers JSON

## Parametres cles (v3.4)
- Score minimum: 20/100 (abaisse de 25 — capte les signaux mid-range)
- Edge factor floor: 0.05 (releve de 0.01)
- Ratio risque/rendement minimum: 1.2 (abaisse de 1.3 — plus realiste en intraday)
- Freshness peak: < 2h (stocke, non inclus dans la formule)
- News max age: 8h (elargi de 6h pour capter overnight US au scan Europe 07:50)
- Dedup Jaccard threshold: 0.65 (abaisse de 0.75 pour meilleure dedup)
- Trigger cooldown: 300s (5 min entre deux triggers manuels)
- Schema version: 3
- Learning min trades: 5 (global), **8** (par ticker, releve de 4), 5 (par categorie/news_cat/session/regime)
- Learning significance: t-stat > **1.5** per-ticker (releve de 1.0), t-stat > 1.0 pour les autres dimensions

## Secrets (Replit)
- **Requis** : `ANTHROPIC_API_KEY`
- **Auto** : `DATABASE_URL` (cree automatiquement par Replit quand on ajoute PostgreSQL)
- **Optionnels** (gratuits, ameliorent la couverture) :
  - `TWELVE_DATA_API_KEY` : market data rapide (https://twelvedata.com/ — free tier 800 credits/jour, 8 req/min)
  - `EIA_API_KEY` : donnees energie EIA (https://www.eia.gov/opendata/register.php)
  - `GNEWS_API_KEY` : recherche news ciblee — 25 queries (https://gnews.io/)
  - `USDA_API_KEY` : donnees agricoles USDA (https://quickstats.nass.usda.gov/api)
  - `GIE_AGSI_API_KEY` : stockage gaz europeen (https://agsi.gie.eu/ — inscription gratuite)
- yfinance, Open-Meteo, CFTC COT, NASA EONET et RSS ne necessitent aucune cle

## Performance (optimisations v3.1)

### Backend — Parallelisation I/O
- **collect_structured_data()** : 8 sources API en parallele (ThreadPoolExecutor, max_workers=8, timeout 45s)
- **collect_all_news()** : 4 sources (structured, early-signal, yfinance, rss) en parallele
- **_fetch_market_context()** : 9 appels yfinance en parallele (VIX + indices + trends)
- **select_trade()** : pre-fetch de tous les prix candidats en parallele avant evaluation
- **run_daily_journal()** : pre-fetch des prix de cloture en parallele pour tous les trades pending
- **scan_feeds_for_triggers()** : 8 early-signal feeds en parallele (event scanner)
- **Claude API** : timeout 90s par batch, pre-filtrage a 50 items max, batching automatique si > 50
- **Scheduler** : retry immediat (pas de `time.sleep()` qui bloquerait le thread scheduler)
- **Scheduler** : `misfire_grace_time=60` scans (evite crash loops), `misfire_grace_time=3600` journal (safe, pur data processing)
- **Trigger scan** : non-bloquant — execute en background thread (evite timeout HTTP)
- **RSS** : `requests.get(url, timeout=15)` + `feedparser.parse(content)` (au lieu de `feedparser.parse(url)` qui n'a pas de timeout reseau)
- **RSS** : User-Agent navigateur pour eviter les 403 (CNBC, gCaptain, BoE)
- **ThreadPoolExecutor** : pattern `try/finally + shutdown(wait=False, cancel_futures=True)` partout (evite blocage si un thread hang)

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
- **PostgreSQL** : ajouter une base PostgreSQL sur Replit → `DATABASE_URL` auto-set. Au premier demarrage, `init_db()` cree les tables. Pour migrer les donnees existantes : `python -m backend.migrate_json_to_postgres`
- **Twelve Data** : ajouter `TWELVE_DATA_API_KEY` dans les secrets Replit (optionnel, yfinance fallback)
- **ATTENTION** : `data/*.json` est dans `.gitignore` — les donnees de production vivent sur le disque Replit (ou PG), PAS dans git. Un `git checkout` ou redeploy ne doit JAMAIS ecraser ces fichiers.

## Tests
- Framework: pytest
- Lancer: `python -m pytest backend/tests/ -v` (depuis la racine du projet)
- Couvre: config, models, news_scorer, trade_selector, journal, learning, economic_calendar, data_apis, event_scanner
- v3 tests ajoutés : significance test, compute_adjustment, multiplicative blending, build_performance_summary
- v3.4 tests ajoutés (10 tests) : structured return format, session_adj separate, regime_adj, per-ticker stricter significance, newscat_adj separate, decomposition, configurable t_threshold, PnL-signed adjustment, benchmark in summary, anti-double-counting
- **test_workflow_e2e.py** (57 tests) : backtest complet du pipeline end-to-end
  - Phase 1 : scoring formula edge cases (weather vs earnings, stale vs fresh, etc.)
  - Phase 2 : trade selection (filtering, session, correlation, pre-move, R/R)
  - Phase 3 : chain reactions (same/inverse/NEUTRAL, dedup)
  - Phase 4 : journal closure (TP/SL/EXPIRED, PnL, delay tracking, scan trace)
  - Phase 5 : learning (significance, blending, feedback loop, adjustments applied)
  - Phase 6 : integration multi-jours (score → select → save → journal → learn)
  - Phase 7 : resilience erreurs (corrupt files, missing data, empty inputs)
- **test_weekend.py** : verification que scans, event checks et triggers sont bloques le week-end
- **test_data_persistence.py** : PG fallback, JSON guard, corrupt file resilience, auto-migration
- **Note** : 1 test flaky (`test_collect_structured_data_returns_list`) — SHFE/LME volume detection depends on live market data
