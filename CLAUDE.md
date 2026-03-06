# OneShot News Trading System — v4.1 Journal Audit Pipeline

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

#### 8. Audit speculateur v3.5 — 17 ameliorations
- **6 nouvelles sources data** : WOAH (maladies animales), NASA POWER (satellite NDVI), Freight proxy (BDRY ETF), LME inventory proxy (metal ETFs), Chokepoint monitoring (tanker ETFs), Dark pool signals (volume/price divergence)
- **GNews** : +6 queries (export bans Inde/Indonesie/Russie, Chine PMI/PBOC, Argentine), consolidation 8→5 commodity queries, rotation intelligente 28 queries / 4 scans (100 req/jour)
- **5 RSS feeds** : Caixin, Xinhua, Chinese govt, US Federal Register (ITC), EUR-Lex
- **2 nouveaux actifs** : USDCNH=X (yuan offshore), URA (uranium ETF) → 41 total
- **Cooldown adaptatif** : weather/supply_chain=1j, commodity=2j, default=3j (au lieu de 3j fixe)
- **Re-entry apres faux stop** : si SL_HIT aujourd'hui sur weather/supply_chain/commodity, re-entry autorise
- **Position sizing VIX** : stress (VIX≥30)=0.5x, elevated (VIX≥20)=0.75x, calm (VIX≤13)=1.2x
- **Gestion vendredi** : apres-midi=0.6x, matin=0.8x (liquidite + gap weekend)
- **Correlation** : +groupe china_proxy (USDCNH, HG=F, AUDUSD), chain reactions USDCNH↔HG/AUD
- **Event scanner** : frequence 15→10 min
- **Dynamic correlation** : skip computation si static group couvre deja la paire
- **collect_structured_data()** : 11→17 sources, timeout 90→120s

#### 9. Audit decision v4.0 — 19 ameliorations (audit complet du pipeline de decisions)
- **A1** : Bug fix `impacted` variable utilisee avant definition dans news_scorer.py
- **A2** : Convergence direction — validation que les news convergent dans la meme direction
- **A3** : Dynamic asset count dans le prompt (`len(ASSETS)` au lieu de 39 hardcode)
- **B1+B2** : `expected_magnitude` et `signal_reliability` — 2 nouvelles dimensions scoring + `reliability_factor` dans la formule
- **B3** : Confidence decorrellee du score (`reliability * clarity / 100`)
- **B4** : Filtre spread — rejette si spread > 40% du target estime, `ESTIMATED_SPREADS` dans config.py
- **B5** : Pre-move depuis `today_open` au lieu de `previous_close`
- **C1** : ATR 5 jours (au lieu de 20) — plus reactif aux conditions recentes
- **C2** : Stop SHORT asymetrique +20% (les squeezes haussiers sont plus violents)
- **C3** : Stop floor adaptatif = max(ancien plancher, 2x estimated_spread)
- **C4** : Score factor convexe `(score/100)^1.5` — recompense disproportionnellement les hauts scores
- **D1** : Fallback ticker — essaie les tickers secondaires si le principal est bloque
- **D3** : Cache correlation dynamique TTL 1h
- **D4** : Daily cap adaptatif VIX (stress=2, elevated=4, normal=6)
- **E1** : PnL par categorie d'actif dans le feedback Claude
- **E2** : Direction accuracy tracking dans le feedback Claude
- **E3** : Separation DONNEES DE PERFORMANCE vs INSTRUCTIONS SCORING dans le prompt

#### 10. Audit journal v4.1 — 27 ameliorations (audit complet du systeme de journal)
- **A1** : File locking `fcntl.LOCK_EX` sur `_save_journal()` — evite corruption donnees
- **A2** : Guard division par zero dans `_compute_pnl()` (entry_price=0)
- **A3** : Sort explicite chronologique des bars + bars 5-tuple (ts, high, low, open, close)
- **A4** : `exit_time` = timestamp reel du bar TP/SL (pas l'heure du journal run)
- **B1** : Bars 15min en priorite (fallback 1h puis daily) — resolution TP/SL plus fine
- **B2** : Correction midpoint sur `actual_pricing_hours` (+7.5min pour 15min, +30min pour 1h)
- **B3** : Smart fallback `actual_pricing_hours` — utilise remaining trading window au lieu de 8h fixe
- **B4** : EXPIRED utilise last post-entry bar close au lieu de session_close
- **B5** : Slippage tracking — difference entry_price vs first post-entry bar open
- **C1** : Streak tracking — alerte quand >= 3 pertes consecutives par ticker
- **C2** : Performance par heure d'entree dans le feedback Claude
- **C3** : Performance par jour de semaine (Lun-Ven) dans le feedback Claude
- **C4** : Drawdown tracking — max pertes consecutives + max drawdown cumulatif
- **C5** : PnL skewness — positive=profil favorable, negative=alerte
- **C6** : Direction accuracy segmentee par categorie de news
- **D1** : Suppression appel redundant `compute_learning_adjustments()` en fin de journal
- **D2** : Date-range limiting — learning ne considere que les 6 derniers mois
- **D3** : Journal pruning automatique des entries > 1 an
- **D4** : `compute_learning_adjustments()` accepte parametre `trades` optionnel (evite reload)
- **E1** : Metrics structurees — timing, taux succes fetch prix
- **E2** : Alerte si > 50% des fetch prix echouent (possible API outage)
- **E3** : Cross-check PnL entre journal entry et trade update (validation duale)
- **E4** : Bar coverage tracking — nombre de bars post-entry disponibles
- **F1** : Max Adverse Excursion (MAE) et Max Favorable Excursion (MFE)
- **F2** : EXPIRED rate monitoring — alerte calibration si > 60%
- **F3** : Realized R/R vs predicted R/R — alerte si stops trop serres
- **F4** : Price anomaly detection — flag si mouvement > 20% (split/data error)
- **G1** : DST-safe date filtering — conversion Paris timezone pour filtrage bars
- **G2** : Market holidays 2025-2026 dans config.py (US + EU)
- **G3** : Global timeout 300s pour le journal run

#### 11. Audit learning v4.2 — 21 ameliorations (audit complet du systeme de learning)
- **A2** : Fix stderr=0 bug — check min_effect_size au lieu de retourner True aveuglément
- **A3** : Minimum effect size threshold (0.1) dans `_is_significant()` — ignore signaux négligeables
- **A4** : PnL divisor 2.0 → 1.0 dans `_compute_adjustment()` — doublait la sensibilité
- **A5** : Average multipliers across ALL eligible tickers (pas juste le premier) dans `trade_selector.py`
- **B1** : `delay_bias_adj` — adjustment global basé sur la précision des prédictions transmission_delay
- **B2** : `magnitude_accuracy` tracking dans performance summary
- **B3** : Slippage vs estimated spread feedback dans performance summary
- **B4** : `hour_adj` — per-hour-of-day learning adjustment (0.85-1.15, min 8 trades)
- **B5** : `direction_adj` — LONG vs SHORT accuracy adjustment (0.8-1.2, min 8 trades)
- **C1** : Regime min trades raised to 15, merged 4→2 buckets (low_vol, high_vol)
- **C2** : Confidence-scaled bounds (via different min_significant per dimension)
- **C3** : Decay handled by cache invalidation (no separate multiplier needed)
- **C4** : Convergence source dedup (documented, handled in news_scorer)
- **D1** : Lookback reduced to 2x half_life (60-90j instead of 180j fixed)
- **D3** : Detect partial PG migration — warning if PG/JSON divergent
- **D4** : `build_performance_summary(trades=...)` accepts optional trades param
- **E1** : Restructured prompt to alerts-only — compact N=/WR=/PnL= header, outliers only
- **E2** : MAE feedback — alerte si SL_HIT > 40% et MAE élevé
- **E3** : signal_reliability precision tracking — alerte si low-rel > high-rel WR
- **7 dimensions** de learning au lieu de 4 : ticker*cat + session + newscat + regime + hour + direction + delay_bias
- **Blending** : `final_mult = base * session * newscat * regime * hour * dir * delay_bias` (clamp [0.5, 1.5])

### Etat actuel des fichiers cles
- `backend/app/main.py` : 4 scans + journal 22h + startup recovery + keepalive + debug endpoints + event check q10min
- `backend/app/market_data.py` : Twelve Data + yfinance, 41 mappings verifies
- `backend/app/database.py` : PG persistence layer, 4 tables, pool, CRUD
- `backend/app/journal.py` : v4.1, 15min bars, MAE/MFE, slippage, pruning, PnL cross-check, global timeout
- `backend/app/learning.py` : v4.2, 21 audit fixes, 7 learning dimensions, alerts-only summary, 2-bucket regimes
- `backend/app/trade_selector.py` : v4.0, convex calibration, fallback ticker, spread filter, VIX daily cap, asymmetric SHORT
- `backend/app/news_scorer.py` : v4.0, 11 dimensions scoring, convergence direction, dynamic asset count
- `backend/app/config.py` : ESTIMATED_SPREADS, DEFAULT_SPREAD, MARKET_HOLIDAYS 2025-2026, is_market_holiday()
- `backend/app/models.py` : v4.1, +6 JournalEntry fields (slippage, MAE, MFE, bar_coverage, bar_interval, realized_rr)
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
Module `data_apis.py` — 17 sources de donnees numeriques que Claude peut interpreter precisement :
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
- **GNews** (cle gratuite `GNEWS_API_KEY`, 100 req/jour) : recherche ciblee par mots-cles — 28 queries avec rotation
  - **Core queries** (22, toujours executees) : frost/freeze, oil/sanctions, port/shipping, copper/mine, BDI/freight, OPEC, cereals USDA, gold reserves, nat gas/TTF, palm oil/China, hurricane/Gulf, cocoa, cotton, OJ/citrus, PGM, portugais x2, disease/blight, fertilizer, avian flu, cattle disease, ASF
  - **Extra queries** (6, rotation par scan) : India rice/wheat/sugar export ban, Indonesia palm oil export, Russia/Ukraine wheat/Black Sea, China PMI Caixin, PBOC rate/RRR/yuan, Argentina peso/capital controls
  - **Rotation** : 22 core + ~2 extras/scan × 4 scans = ~96 req/jour (dans le plafond 100)
  - v3.5: consolidation 8→5 commodity queries pour liberer 3 slots
  - v3.5: +6 queries emerging markets / export bans (India, Indonesie, Russie, Chine, Argentine)
  - **Portugais** : "geada cafe Minas Gerais frio", "seca milho soja safra quebra" (12-24h avant medias EN)
  - **Maladies/engrais** : "wheat rust crop disease blight", "fertilizer potash phosphate shortage"
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
- **WOAH/OIE** (GRATUIT, no key) : surveillance maladies animales mondiales
  - ASF (peste porcine africaine), HPAI (grippe aviaire), FMD (fievre aphteuse), BSE (vache folle)
  - Parsing JSON API WOAH pour outbreaks recents (7 derniers jours)
  - Impact : LE=F, HE=F (betail)
- **NASA POWER** (GRATUIT, no key) : donnees satellite pour stress vegetatif
  - Precipitation + temperature sur 14 jours par zone agricole
  - Detection secheresse satellite (<5mm/14j) et stress thermique
  - Complement aux previsions Open-Meteo avec observation reelle
  - Impact : ZW=F, ZC=F, ZS=F, KC=F selon la zone
- **Freight Index proxy** (GRATUIT via yfinance BDRY) : Baltic Dry Index via ETF
  - Spike >20% mensuel ou collapse >20% = signal shipping
  - Surge 5j >10% = signal court terme
  - Impact : HG=F, ZC=F, ZW=F, ZS=F (commodities physiques)
- **LME Inventory proxy** (GRATUIT via yfinance) : volume anomaly sur ETFs metaux
  - CPER (cuivre), JJN (nickel), PALL (palladium), PPLT (platine)
  - Volume >2x moy 20j = signal inventaires/demande physique
  - Impact : HG=F, PL=F, PA=F
- **Chokepoint Monitoring** (GRATUIT via yfinance) : proxy tankers pour disruptions maritimes
  - STNG, FRO (tanker companies) — spike prix >5% sur 5j avec volume anormal = disruption
  - Impact : CL=F, BZ=F, NG=F (energie)
- **Dark Pool Signals** (GRATUIT via yfinance) : divergence volume/prix sur ETFs majeurs
  - GLD, USO, SPY, QQQ, XLE, XLF — volume >2x sans mouvement prix (<0.5%) = accumulation institutionnelle
  - Impact : tickers selon ETF (GLD→GC=F, USO→CL=F, etc.)

Poids premium (v3.5) : Open-Meteo=1.2, EIA=1.15, NHC=1.15, NOAA=1.1, USDA=1.1, NASA EONET=1.1, GIE AGSI=1.15, CFTC=1.1, WOAH=1.15, NASA POWER=1.15, Freight Index=1.1, Shipping Proxy=1.1, LME Proxy=1.05, Dark Pool Proxy=1.0, Options=1.0, FedWatch=1.0, SHFE=1.1, OilPrice=0.9
- Tous les poids lus via `SOURCE_WEIGHTS.get()` — plus de hardcodes dans data_apis.py
- GNews: poids variable par categorie query (weather=1.0, commodity/supply_chain=0.95, geopolitical=0.85)

### Phase 1 : Early-Signal RSS (info brute, pas encore interpretee)
Sources configurees dans `EARLY_SIGNAL_FEEDS` (25 feeds — v3.5 elargi) :
- **Meteo/Agri** : Drought.gov (US drought monitor), SPC (orages), NWS (alertes), api.weather.gov (ATOM), NHC (ouragans Atlantique), Climate.gov (ENSO/outlooks)
- **USDA/FAO** : NASS reports (recoltes, stocks), FAO newsroom
- **Geopolitique/Defense** : war.gov (operations militaires, geopolitique), IAEA (nucleaire/sanctions)
- **Energie** : EIA Today in Energy, OilPrice
- **Maritime/Shipping** : gCaptain, MarineLink, Maritime Executive, Splash247 (ports, containers, BDI)
- **Canal chokepoints** : Panama Canal Authority (ACP) — transit disruptions
- **Banques centrales** : ECB (corrige .html→.xml), Federal Reserve, Bank of England (speeches)
- **Chine** (v3.5) : Caixin RSS, Xinhua English finances, Chinese govt latest releases
- **Reglementation** (v3.5) : US Federal Register (ITC trade rules), EUR-Lex (recent EU legislation)

**v3.5** : +5 feeds Chine/reglementation (edge sur export bans, tarifs, decisions commerciales)

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

## Scans Evenementiels (reactifs) — ACTIFS
Module `event_scanner.py` — schedule dans `main.py` toutes les 10 minutes.
- **Cron** : toutes les 10 minutes pendant les heures de trading (07:00-19:30 CET), lundi-vendredi
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

## Scoring — Formule Edge-Weighted (v4.0)

### Criteres d'evaluation (par Claude) — 11 dimensions
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
10. **expected_magnitude** (0-100, v4.0) : Amplitude attendue du mouvement prix
    - 0 = bruit, mouvement negligeable
    - 50 = mouvement notable 1-3%
    - 100 = choc majeur > 3%
11. **signal_reliability** (0-100, v4.0) : Fiabilite du signal
    - 0 = rumeur/speculation non confirmee
    - 50 = rapport de presse
    - 100 = fait confirme / mesure officielle (USDA, EIA, NOAA)

### Formule de score (v4.0 — reliability_factor ajoute)
```
edge_factor = max(transmission_delay/100 * (1 - market_awareness/100), 0.05)
reliability_factor = 0.4 + 0.6 * (signal_reliability / 100)
score = surprise * (clarity/100) * edge_factor * reliability_factor * source_weight * category_score_mult
```
**Changements v4.0 vs v3.1 :**
- **signal_reliability** ajoute : les faits confirmes (EIA, USDA) scorent 2.5x plus que les rumeurs
- reliability_factor plancher a 0.4 (meme une rumeur garde 40% de valeur — utile si elle est vraie)
- **expected_magnitude** ajoute : utilise dans la calibration R/R (pas dans le score)
- **convergence direction** (A2) : validation que les news convergent dans la meme direction
- **dynamic asset count** (A3) : prompt utilise `len(ASSETS)` au lieu de 39 hardcode
- **bug fix A1** : `impacted` variable utilise avant definition — corrige

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
- **Yuan/Chine** (v3.5) : USDCNH=X → HG=F (inverse — demand proxy), AUDUSD=X (inverse — China trade partner)

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
- **Univers**: 41 actifs (7 actions Euronext Paris, 4 metaux, 7 forex, 15 commodities, 8 indices)
  - v3.5: +USDCNH=X (yuan offshore — proxy Chine), +URA (uranium ETF — geopolitique nucleaire)
- **Filtrage par session**: Europe = Euronext + indices EUR/GBP + metaux/forex/commodities. US = indices USD/JPY/HKD/AUD + metaux/forex/commodities.
- **Correlation portfolio**: chaque scan verifie les trades de TOUS les autres scans (pas seulement l'autre session)
- **DST**: toutes les heures utilisent `ZoneInfo("Europe/Paris")` (pas de CET hardcode)

## Calibration R/R (v4.0 — decorrellee, convexe)
3 tiers de volatilite avec **facteur convexe** et **magnitude scaling** :
- **Low-vol** (ATR < 1%, forex/large indices) : factor=0.30+(score/100)^1.5*0.60, stop=0.5
- **Normal** (ATR 1-5%) : factor=0.20+(score/100)^1.5*0.50, stop=0.4
- **High-vol** (ATR > 5%, NG, small-cap) : factor=0.10+(score/100)^1.5*0.35, stop=0.3
- **Convexe** (v4.0 C4) : `(score/100)^1.5` au lieu de lineaire — les scores eleves sont disproportionnellement recompenses
- **Magnitude factor** (v4.0 B1) : `0.7 + 0.6 * (expected_magnitude/100)` multiplie le score_factor
- **ATR 5 jours** (v4.0 C1) : utilise les 5 derniers jours au lieu de 20 (plus reactif aux conditions recentes)
- **Asymmetrie SHORT** (v4.0 C2) : stop SHORT +20% plus large (les squeezes sont plus violents a la hausse)
- **Stop floor adaptatif** (v4.0 C3) : `max(TARGET_PERCENT * 0.7, spread_estime * 2)` — evite les stops en dessous du spread
- **Target**: ATR x score_factor x magnitude_factor x news_category_target_mult
- **Stop**: ATR x stop_fraction x news_category_stop_mult (x1.2 si SHORT)
- **R/R variable**: le ratio varie selon le score (convexe, plus le score est eleve, plus le target est ambitieux)
- **Planchers**: target min = TARGET_PERCENT, stop min = max(TARGET_PERCENT x 0.7, 2x estimated_spread)
- **Spread filter** (v4.0 B4) : rejette les trades ou le spread represente >40% du target estime

## Trade Selection — Risk Management (v4.0)

### Cooldown adaptatif par categorie
- **weather/supply_chain** : cooldown 1 jour (signaux persistants, re-scan rapide)
- **commodity** : cooldown 2 jours
- **Autres** : cooldown 3 jours (default)

### Re-entry apres faux stop
- Si un trade est SL_HIT aujourd'hui sur weather/supply_chain/commodity → re-entry autorise
- Justification : les signaux physiques persistent — un stop intraday ne signifie pas que le signal est invalide
- Ne s'applique PAS aux TP_HIT (le signal a deja ete price)

### Position sizing dynamique
- **Quarter-Kelly** de base, ajuste par :
  - **VIX regime** : stress (VIX≥30) → 0.5x | elevated (VIX≥20) → 0.75x | calm (VIX≤13) → 1.2x
  - **Vendredi** : apres-midi → 0.6x | matin → 0.8x (liquidite reduite + risque gap weekend)

### Fallback ticker (v4.0 D1)
- Si le ticker principal est bloque (correlation, cooldown, prix indisponible), essaie le ticker suivant dans `impacted_tickers`
- Ordre : parcourt tous les tickers eligibles d'une news avant de rejeter le candidat
- Evite de perdre des signaux valides quand seul le ticker primaire est bloque

### Daily cap adaptatif VIX (v4.0 D4)
- **stress** (VIX≥30) : max 2 trades/jour (MAX_TRADES_PER_DAY // 3)
- **elevated** (VIX≥20) : max 4 trades/jour (MAX_TRADES_PER_DAY * 2/3)
- **normal/calm** : max 6 trades/jour (MAX_TRADES_PER_DAY)

### Confidence decorrellee (v4.0 B3)
- `confidence = min(100, signal_reliability * directional_clarity / 100)`
- Plus liee au score — reflete la fiabilite x clarte, pas le score total

### Spread filter (v4.0 B4)
- Avant calibration, estime le target potentiel
- Rejette si spread > 40% du target estime (trade non profitable apres spread)
- `ESTIMATED_SPREADS` dans config.py pour les 41 tickers

### Pre-move depuis open (v4.0 B5)
- Utilise `today_open` au lieu de `previous_close` pour detecter les pre-moves intraday
- Plus precis pour le day trading (capte le mouvement depuis l'ouverture)

### Dynamic correlation optimization
- Si deux tickers sont dans le meme groupe statique, skip le calcul de correlation dynamique (rolling 20j)
- **Cache TTL 1h** (v4.0 D3) : resultats de correlation dynamique caches pendant 1h
- Gain de performance : evite des appels yfinance inutiles quand la correlation est deja connue

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
- china_proxy: USDCNH=X, HG=F, AUDUSD=X

## Journal quotidien (22h CET)
- **Scheduler**: job automatique a 22:00 CET chaque jour ouvrable (lundi-vendredi)
  - `misfire_grace_time=3600` (1h) — le journal est du pur data processing, pas de risque crash loop
  - Si le job est rate (app down), la startup recovery ferme les vieux trades au redemarrage
- **Startup recovery** : `_recover_pending_trades_on_startup()` dans `main.py`
  - Au demarrage, detecte les trades PENDING de jours precedents
  - Lance automatiquement `run_daily_journal()` pour les fermer
  - Evite les trades bloques PENDING quand l'app est tuee avant 22h
- **Actions**: ferme tous les trades PENDING, recupere les prix reels du jour (15min bars preferred, 1h fallback via Twelve Data/yfinance)
- **Resultat auto**: TP verifie en priorite via bars chronologiques (15min/1h), puis SL, puis EXPIRED
  - Bars 5-tuple (timestamp, high, low, open, close) triees chronologiquement
  - Bars filtrees pour ne garder que post-entry (ignore les extremes d'avant le trade)
  - Si TP et SL touches dans la meme barre → conservatif SL
  - EXPIRED utilise le close de la derniere barre post-entry (pas le session close global)
- **v4.1 Enrichissement journal** :
  - Slippage tracking : entry_price vs first post-entry bar open
  - MAE/MFE : Max Adverse/Favorable Excursion pendant le trade
  - Bar coverage : nombre de bars post-entry disponibles + intervalle detecte
  - Realized R/R : ratio risque/rendement realise vs predit
  - Price anomaly detection : flag si mouvement > 20% (possible split/data error)
  - PnL cross-check : validation duale entre journal et trade update
  - DST-safe date filtering : conversion Paris timezone pour filtrage bars
  - Global timeout 300s pour eviter les journal runs infinis
  - Exit time = timestamp reel du bar TP/SL (pas l'heure du journal run)
  - Midpoint correction sur actual_pricing_hours (+7.5min pour 15min, +30min pour 1h)
- **Pruning** : entries > 1 an prunees automatiquement apres chaque journal run
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

## Learning adaptatif (v4.2 — audit complet)
- **Par ticker**: ajustement 0.5-1.5 (min **8** trades, t-stat > **1.5**)
- **Par categorie d'actif**: ajustement 0.7-1.3 (min 5 trades, t-stat > 1.0)
- **Par categorie de news**: ajustement 0.7-1.3 (min 5 trades, t-stat > 1.0) — applique contextuellement par la news_category du trade courant
- **Par session** (europe/us): ajustement 0.8-1.2 (min 5 trades, t-stat > 1.0) — applique par le scan courant
- **Par regime VIX** (v4.2 C1): ajustement 0.7-1.3, **2 buckets** (low_vol=calm+normal, high_vol=elevated+stress), **min 15 trades** (was 5)
  - Merged from 4 regimes to 2 for larger sample sizes — reduces overfitting
- **Par heure d'entree** (v4.2 B4 NEW): ajustement 0.85-1.15 (min 8 trades) — heures Paris
- **Par direction** (v4.2 B5 NEW): ajustement 0.8-1.2 (LONG vs SHORT accuracy, min 8 trades)
- **Delay bias** (v4.2 B1 NEW): ajustement global basé sur la précision des prédictions transmission_delay
  - Si surestimation systématique → pénalise (on entre trop agressivement)
  - Si sous-estimation → booste (on rate de l'edge)
- **Blending multiplicatif** (v4.2): `final_mult = base(ticker*cat) * session * newscat * regime * hour * direction * delay_bias`
  - 7 dimensions (was 4) — toutes appliquées contextuellement par `select_trade()`
  - Clamp final [0.5, 1.5]
- **A5: Average ticker mults** (v4.2): utilise la moyenne des multipliers de TOUS les tickers éligibles (pas juste le premier)
- **Significance test**: pseudo t-test avec **effet size minimum** (v4.2 A3)
  - A2: stderr=0 vérifie min_effect_size au lieu de retourner True aveuglément
  - A3: |mean| doit être >= 0.1 (ignore les signaux négligeables)
  - Per-ticker: t > 1.5, min 8 trades
  - Per-category/session/newscat: t > 1.0, min 5 trades
  - Per-regime: t > 1.0, min 15 trades (raised from 5)
  - Per-hour/direction: t > 1.0, min 8 trades
- **Signal PnL-signe**: `_compute_adjustment()` normalise par 1.0 (v4.2 A4, was 2.0 — doublait la sensibilité)
  - Formule: `1.0 + clamp(avg_pnl / 1.0, -cap, cap) * sensitivity`
- **Decay temporel adaptatif**: demi-vie 45j (peu de trades) → 30j (beaucoup de trades)
- **D1: Lookback = 2x half_life** (v4.2): was 180 jours fixe — maintenant 60-90j selon half_life
- **D4: Trades parameter**: `compute_learning_adjustments(trades=...)` et `build_performance_summary(trades=...)` acceptent un paramètre optionnel
- **D3: Partial PG migration detection** (v4.2): warning si PG et JSON ont des données divergentes
- **Feedback loop Claude** (v4.2 E1): format **alerts-only** — compact, n'inclut que les anomalies :
  - Header compact: `N=X | WR=X% | PnL=X% | moy=X%`
  - Outliers newscat seulement (WR < 35% ou > 70%)
  - Direction accuracy si anomalous (< 45% ou > 65%)
  - Delay bias si significatif (> 10pts)
  - EXPIRED rate si > 40%
  - Drawdown si >= 3 pertes consécutives
  - Skewness si négative < -0.5
  - **E2: MAE feedback** — alerte si SL_HIT > 40% et MAE élevé (stops trop serrés)
  - **E3: signal_reliability precision** — alerte si low-rel WR > high-rel WR
  - **B2: magnitude_accuracy** — alerte si surestimation/sous-estimation systématique
  - **B3: slippage vs spread** — alerte si slippage > 2x spread estimé
  - Streaks négatifs actifs (>= 3)
  - Derniers 10 trades (compact)
  - Instructions scoring compactes
- **Score decomposition**: chaque trade stocke `raw_claude_score` et `learning_multiplier`
- **Decomposition par dimension**: logs détaillent `ticker_mult`, `cat_mult`, `session_mult`, `newscat_mult`, `regime_mult`, `hour_mult`, `dir_mult`, `delay_bias_adj`
- **learning_helped tracking**: chaque trade tracke si le learning a boosté ou pénalisé la sélection
- **Cache learning**: invalidé après le journal 22h
- **Return format** (v4.2): `compute_learning_adjustments()` retourne un dict structuré :
  - `adjustments`: dict[ticker, multiplier] — base blend ticker*cat
  - `session_adj`: dict[scan_type, multiplier] — appliqué par scan courant
  - `newscat_adj`: dict[news_category, multiplier] — appliqué par news courante
  - `regime_adj`: dict[regime, multiplier] — low_vol/high_vol (v4.2 merged)
  - `hour_adj`: dict[hour_label, multiplier] — v4.2 B4
  - `direction_adj`: dict[direction, multiplier] — v4.2 B5
  - `delay_bias_adj`: float — v4.2 B1
  - `decomposition`: dict[ticker, {ticker_mult, cat_mult}] — pour diagnostics
- **File locking**: `fcntl.LOCK_EX` / `fcntl.LOCK_SH` pour accès concurrent sur les fichiers JSON

## Parametres cles (v4.0)
- Score minimum: 20/100 (abaisse de 25 — capte les signaux mid-range)
- Edge factor floor: 0.05 (releve de 0.01)
- Reliability factor floor: 0.4 (rumeur garde 40% de valeur)
- Ratio risque/rendement minimum: 1.2 (abaisse de 1.3 — plus realiste en intraday)
- Freshness peak: < 2h (stocke, non inclus dans la formule)
- News max age: 8h (elargi de 6h pour capter overnight US au scan Europe 07:50)
- Dedup Jaccard threshold: 0.65 (abaisse de 0.75 pour meilleure dedup)
- Trigger cooldown: 300s (5 min entre deux triggers manuels)
- Schema version: 3
- ATR window: **5 jours** (reduit de 20 — plus reactif)
- Score factor: **convexe** `(score/100)^1.5` (v4.0, au lieu de lineaire)
- Magnitude factor: `0.7 + 0.6 * (expected_magnitude/100)` (v4.0)
- SHORT stop asymmetrie: **+20%** (v4.0)
- Spread filter seuil: spread > 40% du target estime → rejet (v4.0)
- Correlation cache TTL: 1h (v4.0)
- Daily cap adaptatif: stress=2, elevated=4, normal=6 (v4.0)
- ESTIMATED_SPREADS: 41 tickers dans config.py, DEFAULT_SPREAD=0.10% (v4.0)
- Learning min trades: 5 (global), **8** (par ticker/hour/direction), 5 (par categorie/news_cat/session), **15** (par regime — v4.2 C1)
- Learning significance: t-stat > **1.5** per-ticker, t-stat > 1.0 autres, min_effect_size=0.1 (v4.2 A3)
- Learning PnL divisor: **1.0** (v4.2 A4, was 2.0)
- Learning lookback: **2x half_life** (v4.2 D1, was 180 jours fixe)

## Secrets (Replit)
- **Requis** : `ANTHROPIC_API_KEY`
- **Auto** : `DATABASE_URL` (cree automatiquement par Replit quand on ajoute PostgreSQL)
- **Optionnels** (gratuits, ameliorent la couverture) :
  - `TWELVE_DATA_API_KEY` : market data rapide (https://twelvedata.com/ — free tier 800 credits/jour, 8 req/min)
  - `EIA_API_KEY` : donnees energie EIA (https://www.eia.gov/opendata/register.php)
  - `GNEWS_API_KEY` : recherche news ciblee — 28 queries avec rotation (https://gnews.io/)
  - `USDA_API_KEY` : donnees agricoles USDA (https://quickstats.nass.usda.gov/api)
  - `GIE_AGSI_API_KEY` : stockage gaz europeen (https://agsi.gie.eu/ — inscription gratuite)
- yfinance, Open-Meteo, CFTC COT, NASA EONET et RSS ne necessitent aucune cle

## Performance (optimisations v3.1)

### Backend — Parallelisation I/O
- **collect_structured_data()** : 17 sources API en parallele (ThreadPoolExecutor, max_workers=8, timeout 120s)
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
- v4.0 tests ajoutés (12 tests) :
  - **test_models.py** : reliability_factor, reliability_floor, magnitude_defaults, trade_recommendation magnitude/reliability
  - **test_news_scorer.py** : tool schema magnitude/reliability, dynamic asset count, magnitude instructions, convergence direction param
  - **test_trade_selector.py** : convex_score_factor, short_asymmetric_stop, magnitude_scales_target, spread_stop_floor
- v4.1 tests ajoutés (18 tests) :
  - **TestJournalAuditV41** (11 tests) : PnL div-by-zero, MAE/MFE long/short, slippage, price anomaly, bar interval, v4.1 fields, EXPIRED last bar close, find_hit_time
  - **TestLearningV41** (7 tests) : D2 old trades filtered, D4 trades param, C2 time-of-day, C3 day-of-week, F2 expired rate alert, G2 market holidays, D3 journal pruning
- **test_workflow_e2e.py** (82 tests) : backtest complet du pipeline end-to-end
  - Phase 1 : scoring formula edge cases (weather vs earnings, stale vs fresh, etc.)
  - Phase 2 : trade selection (filtering, session, correlation, pre-move, R/R)
  - Phase 3 : chain reactions (same/inverse/NEUTRAL, dedup)
  - Phase 4 : journal closure (TP/SL/EXPIRED, PnL, delay tracking, scan trace)
  - Phase 5 : learning (significance, blending, feedback loop, adjustments applied)
  - Phase 6 : integration multi-jours (score → select → save → journal → learn)
  - Phase 7 : resilience erreurs (corrupt files, missing data, empty inputs)
  - Phase 8 : multi-trade per scan (v3.5)
  - Phase 9 : v4.1 journal audit (MAE/MFE, slippage, pruning, holidays, D2 cutoff)
- v4.2 tests ajoutés (12 tests) :
  - **test_learning.py** : stderr=0 effect size (A2), min effect size (A3), divisor fix (A4), hour_adj, direction_adj, delay_bias_adj, D1 lookback, regime merged buckets, trades param summary (D4), alerts-only format (E1)
  - **test_workflow_e2e.py** : updated "Win rate" → "WR=" assertions for v4.2 compact format
- **test_weekend.py** : verification que scans, event checks et triggers sont bloques le week-end
- **test_data_persistence.py** : PG fallback, JSON guard, corrupt file resilience, auto-migration
- **Note** : 1 test flaky (`test_collect_structured_data_returns_list`) — SHFE/LME volume detection depends on live market data
