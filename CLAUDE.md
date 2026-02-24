# OneShot News Trading System

## Architecture
- **Backend**: FastAPI + APScheduler (Python)
- **Frontend**: React + Vite
- **Persistence**: `data/trades.json` + `data/journal.json` (flat files, no DB)

## Data Sources
- **Yahoo Finance (yfinance)**: prix temps réel, news par ticker, historique de volatilité. Gratuit, pas de clé API.
- **RSS feeds** (5 sources): Reuters, CNBC, Investing.com. Publics, pas de clé.
- **Claude API (Anthropic)**: scoring des news via Sonnet. Seul secret requis: `ANTHROPIC_API_KEY`.

## Stratégie
- **Type**: Day trading event-driven (news-based)
- **Scans**: 2/jour — Europe 07:50 CET, US 14:30 CET
- **Exécution**: 0 ou 1 trade par scan
- **Fenêtres de sortie**: Europe 09:00-20:00 CET, US 15:30-20:00 CET
- **Clôture**: toutes les positions doivent être fermées avant 20:00 CET. Pas d'overnight.
- **Univers**: 49 actifs (15 actions Euronext Paris, 4 métaux, 9 forex, 9 commodities, 12 indices)

## Journal quotidien (22h CET)
- **Scheduler**: job automatique à 22:00 CET chaque jour
- **Actions**: ferme tous les trades PENDING, récupère les prix réels du jour (high/low/close via yfinance)
- **Résultat auto**: TP_HIT si le high/low a touché le target, SL_HIT si le stop a été touché, EXPIRED sinon
- **Persistence**: `data/journal.json` (flat file)
- **Colonnes**: news, analyse, score, actif, direction, heure/prix entrée, heure/prix sortie, high du jour, résultat, bilan
- **Learning**: après chaque clôture, les résultats alimentent `compute_learning_adjustments()` pour améliorer les futurs scores
- **Frontend**: onglet "Journal" avec tableau groupé par date
- **API**: `GET /api/journal`, `GET /api/journal/{date}`, `POST /api/journal/trigger`

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
- Framework: pytest
- Lancer: `cd backend && python -m pytest tests/ -v`
