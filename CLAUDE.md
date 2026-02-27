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
- **Persistence**: `data/trades.json` + `data/journal.json` + `data/scan_history.json` + `data/last_scans.json` (flat files, file locking via `fcntl`)

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
- **GNews** (cle gratuite `GNEWS_API_KEY`, 100 req/jour) : recherche ciblee par mots-cles — 21 queries (was 20, consolide puis +5)
  - Consolide : frost+coffee frost en 1, drought+harvest en 1, palm oil+China import en 1, PT queries en 1
  - Ajoute : cocoa Ghana/Ivory Coast, cotton Texas/India, orange juice Florida/citrus greening
  - **Betail** : "cattle disease screwworm BSE", "African swine fever pork hog"
  - 4 scans/jour x 21 queries = 84 req/jour, marge confortable vs 100/jour
  - Originales : "frost freeze crop damage", "oil sanctions embargo", "port congestion shipping"
  - "OPEC production cut", "wheat corn soybean USDA", "military strike missile", "copper mine strike"
  - "natural gas storage Europe TTF LNG", "palm oil export China import soybean"
  - "China import commodity soybean", "Baltic dry index shipping freight"
  - "coffee frost Brazil Minas Gerais", "hurricane tropical storm Gulf Mexico"
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
  - Forward guidance : spread M+1/M+2/M+3 vs spot (>15bps = anticipation forte)
  - Impact : GC=F (inverse), EURUSD=X, ^GSPC, USDJPY=X
- **SHFE/LME Inventaires** (GRATUIT via yfinance) : proxy inventaires metaux via volume/prix
  - Volume anormal (>2x moy 20j) = mouvement inventaires physiques
  - Mouvement mensuel >5% = tightness ou surplus
  - Impact : HG=F (cuivre)

Poids premium : Open-Meteo=1.15/1.2, EIA=1.15, NHC=1.15, NOAA=1.1, USDA=1.1, NASA EONET=1.1, GIE AGSI=1.1/1.15, CFTC=1.05/1.1, Options=0.95/1.0, FedWatch=1.0, SHFE=1.1

### Phase 1 : Early-Signal RSS (info brute, pas encore interpretee)
Sources configurees dans `EARLY_SIGNAL_FEEDS` (23 feeds — verifie 2026-02-27) :
- **Meteo/Agri** : Drought.gov (US drought monitor), SPC (orages), NWS (alertes), api.weather.gov (ATOM), NHC (ouragans Atlantique), Climate.gov (ENSO/outlooks)
- **USDA/FAO** : NASS reports (recoltes, stocks), FAO newsroom
- **Geopolitique/Defense** : war.gov + defense.gov fallback (operations militaires, geopolitique), IAEA (nucleaire/sanctions)
- **Energie** : EIA Today in Energy, OilPrice
- **Maritime/Shipping** : gCaptain, MarineLink, Maritime Executive, Splash247 (ports, containers, BDI)
- **Canal chokepoints** : Suez Canal Authority (SCA), Panama Canal Authority (ACP) — transit disruptions
- **Banques centrales** : ECB (corrige .html→.xml), Federal Reserve, Bank of England (speeches)

**Feeds remplaces** : NCEI news.xml (mort)→Drought.gov+Climate.gov, Defense.gov→war.gov, ECB .html→.xml

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

## Scans Evenementiels (reactifs)
Module `event_scanner.py` — surveillance continue des feeds early-signal :
- **Cron** : toutes les 30 minutes pendant les heures de trading (07:00-19:30 CET), lundi-vendredi
- **Weekend** : desactive (weekday check dans `should_trigger_scan()` + `day_of_week` dans CronTrigger)
- **Detection** : mots-cles a fort potentiel dans les titres RSS (v3.1 — elargi)
  - weather : drought, frost, hurricane, typhoon, tropical storm, el nino, la nina, wildfire, volcanic eruption, earthquake, monsoon failure, record heat/cold...
  - supply_chain : pipeline explosion, port closed, canal blocked, embargo, container shortage, baltic dry, freight rate surge, vessel grounding, lng terminal, strategic reserve...
  - geopolitical : military strike, sanctions, nuclear, invasion, carrier strike group, no-fly zone, military buildup, arms deal...
  - commodity : opec cut, crop failure, stockpile draw, shortage, gas storage, ttf price, palm oil export, coffee frost, china import, wheat export ban...
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

### Hard-caps sur categories zero-edge
- **earnings** : transmission_delay force a 5 si > 15, market_awareness force a >= 90
- **macro** : transmission_delay force a 10 si > 20, market_awareness force a >= 85

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
- **Metaux/safe haven** : GC=F (Or) → SI=F (argent, same), USDCHF=X (CHF, inverse)
- **Agriculture** : ZC=F (Mais) → ZS=F (soja), ZW=F (ble) | KC=F (Cafe) → SB=F (sucre)
- **Luxe/Chine** : MC.PA → RMS.PA, OR.PA (meme exposition consommateur chinois)
- **Cuivre** : HG=F → ^GSPC, ^FCHI (proxy activite industrielle)
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
- **Univers**: 54 actifs (15 actions Euronext Paris, 4 metaux, 9 forex, 14 commodities, 12 indices)
- **Filtrage par session**: Europe = Euronext + indices EUR/GBP + metaux/forex/commodities. US = indices USD/JPY/HKD/AUD + metaux/forex/commodities.
- **Correlation portfolio**: chaque scan verifie les trades de TOUS les autres scans (pas seulement l'autre session)
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

## Parametres cles (v3.1)
- Score minimum: 25/100 (abaisse de 55 — formule multiplicative trop punitive a 55)
- Edge factor floor: 0.05 (releve de 0.01)
- Ratio risque/rendement minimum: 1.3 (releve de 1.0)
- Freshness peak: < 2h (stocke, non inclus dans la formule)
- News max age: 8h (elargi de 6h pour capter overnight US au scan Europe 07:50)
- Dedup Jaccard threshold: 0.65 (abaisse de 0.75 pour meilleure dedup)
- Trigger cooldown: 300s (5 min entre deux triggers manuels)
- Schema version: 3
- Learning min trades: 5 (global), 4 (par ticker), 5 (par categorie/news_cat/session)
- Learning significance: t-stat > 1.0 requis avant d'appliquer un ajustement

## Secrets (Replit)
- **Requis** : `ANTHROPIC_API_KEY`
- **Optionnels** (gratuits, ameliorent la couverture) :
  - `EIA_API_KEY` : donnees energie EIA (https://www.eia.gov/opendata/register.php)
  - `GNEWS_API_KEY` : recherche news ciblee — 15 queries (https://gnews.io/)
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
- **Scheduler** : `misfire_grace_time=600` sur tous les jobs (evite de rater le scan si l'app demarre en retard)
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
- **ATTENTION** : `data/*.json` est dans `.gitignore` — les donnees de production vivent sur le disque Replit, PAS dans git. Un `git checkout` ou redeploy ne doit JAMAIS ecraser ces fichiers.

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
