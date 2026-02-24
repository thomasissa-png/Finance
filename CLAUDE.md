# OneShot News Trading System

## Architecture
- **Backend**: FastAPI + APScheduler (Python)
- **Frontend**: React + Vite
- **Persistence**: `data/trades.json` + `data/journal.json` (flat files, file locking via `fcntl`)

## Data Sources
- **Yahoo Finance (yfinance)**: prix temps réel, news par ticker, historique de volatilité. Gratuit, pas de clé API. Collecte parallélisée (ThreadPoolExecutor, 10 workers).
- **RSS feeds** (5 sources): Reuters, CNBC, Investing.com. Publics, pas de clé. Collecte parallélisée (5 workers).
- **Claude API (Anthropic)**: scoring des news via Sonnet avec retry exponentiel (max 2 retries). Seul secret requis: `ANTHROPIC_API_KEY`.

## Stratégie
- **Type**: Day trading event-driven (news-based)
- **Scans**: 2/jour — Europe 07:50 CET, US 14:30 CET
- **Exécution**: 0 ou 1 trade par scan
- **Fenêtres de sortie**: Europe 09:00-20:00 CET, US 15:30-20:00 CET
- **Clôture**: toutes les positions doivent être fermées avant 20:00 CET. Pas d'overnight.
- **Univers**: 49 actifs (15 actions Euronext Paris, 4 métaux, 9 forex, 9 commodities, 12 indices)
- **Filtrage par session**: Europe = Euronext + indices EUR/GBP + métaux/forex/commodities. US = indices USD/JPY/HKD/AUD + métaux/forex/commodities. Pas d'actions EU pendant le scan US.
- **DST**: toutes les heures utilisent `ZoneInfo("Europe/Paris")` (pas de CET hardcodé)

## Calibration R/R (décorrélée)
- **Target**: ATR × (0.25 + score/100 × 0.45) — pondéré par le score de confiance
- **Stop**: ATR × 0.4 — fixe, indépendant du target
- **R/R variable**: le ratio varie selon le score (plus le score est élevé, plus le target est ambitieux)
- **Planchers**: target min = TARGET_PERCENT, stop min = TARGET_PERCENT × 0.7

## Journal quotidien (22h CET)
- **Scheduler**: job automatique à 22:00 CET chaque jour
- **Actions**: ferme tous les trades PENDING, récupère les prix réels du jour (high/low/close via yfinance)
- **Résultat auto**: TP vérifié en priorité (TP > SL quand les deux sont touchés le même jour), puis SL, puis EXPIRED
- **Dedup**: vérifie `(ticker, entry_time)` pour éviter les doublons lors de triggers manuels
- **Trades anciens**: récupère les prix historiques pour la date réelle du trade (pas seulement aujourd'hui)
- **Persistence**: `data/journal.json` (flat file avec file locking)
- **Colonnes**: news (titre + catégorie), analyse, score, actif (ticker + catégorie), direction, heure/prix entrée, heure/prix sortie, high/low du jour, résultat, bilan
- **Catégories de news**: earnings, macro, geopolitical, regulatory, m_a, sector, commodity, other
- **Catégories d'actifs**: actions_europe, metaux, forex, commodities, indices
- **Learning**: après chaque clôture, les résultats alimentent `compute_learning_adjustments()` pour améliorer les futurs scores
- **Frontend**: onglet "Journal" avec tableau groupé par date, badges catégorie news et actif
- **API**: `GET /api/journal`, `GET /api/journal/{date}`, `POST /api/journal/trigger`

## Learning adaptatif
- **Par ticker**: ajustement 0.5-1.5 basé sur l'historique de résultats
- **Par catégorie**: ajustement 0.7-1.3 basé sur la performance de la catégorie d'actif
- **Blending**: 60% ticker + 40% catégorie, borné à [0.5, 1.5]
- **Decay temporel**: demi-vie de 30 jours — les trades récents comptent plus que les anciens
- **File locking**: `fcntl.LOCK_EX` / `fcntl.LOCK_SH` pour accès concurrent sûr aux fichiers JSON

## Paramètres clés
- Score minimum: 40/100
- Ratio risque/rendement minimum: 1.0
- Freshness peak: < 2h
- News max age: 6h
- Formule score: `surprise * 0.4 + freshness * 0.3 + directional_clarity * 0.3`

## Secrets (Replit)
- Seul secret nécessaire: `ANTHROPIC_API_KEY`
- yfinance et RSS ne nécessitent aucune clé

## Déploiement
- Plateforme cible: Replit
- Backend: `uvicorn backend.app.main:app`
- Frontend: Vite dev server ou build statique

## Tests
- Framework: pytest (50 tests)
- Lancer: `cd backend && python -m pytest tests/ -v`
- Couvre: config, models, trade_selector, journal, learning
