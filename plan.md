# Plan: Architecture Multi-Agents v6.0

## Vision

Transformer le pipeline monolithique actuel (collect → score → select → journal → learn)
en **6 agents autonomes** qui travaillent en parallèle, chacun avec son domaine d'expertise,
son propre log d'activité visible dans le frontend, et une communication via un **message bus**
partagé (table PostgreSQL `agent_messages`).

## Les 6 Agents

### 1. Agent News (`agent_news.py`)
**Rôle** : Collecte, curation et fiabilité des sources
**Hérite de** : `news_collector.py`, `data_apis.py`, `source_monitor.py`, `event_scanner.py`
**Schedule** : Collecte toutes les 10 min (heures trading), health check continu
**Actions** :
- Collecte les news de toutes les sources (RSS, APIs, GNews)
- Déduplique (Jaccard cross-scan)
- Évalue la santé des sources (latence, taux d'erreur)
- Publie les news brutes sur le message bus → Agent Scoring les consomme
- Revue hebdomadaire dimanche 20h (déjà en place)

**Log visible** : sources interrogées, nb items par source, latence, erreurs, items publiés

### 2. Agent Scoring (`agent_scoring.py`)
**Rôle** : Expert scoring avec 15+ ans d'expertise edge/news trading
**Hérite de** : `news_scorer.py` (appels Claude API, formule edge-weighted)
**Trigger** : Réagit quand Agent News publie des news
**Actions** :
- Consomme les news brutes depuis le bus
- Applique le pré-filtrage zero-edge
- Score via Claude API (surprise, delay, awareness, reliability, magnitude...)
- Applique la formule edge-weighted
- Détecte les chain reactions
- Publie les news scorées sur le bus → Agent Trader les consomme

**Log visible** : items reçus, items filtrés (zero-edge), scores attribués, tokens utilisés, cache hits

### 3. Agent Trader (`agent_trader_1.py`)
**Rôle** : Expert décision d'investissement, 15+ ans news trading
**Hérite de** : `trade_selector.py`, `economic_calendar.py`, `position_monitor.py`
**Trigger** : Réagit quand Agent Scoring publie des scores
**Actions** :
- Consomme les news scorées
- Applique les learning multipliers (reçus de l'Agent Learning)
- Vérifie calendrier éco, correlation, cooldowns, VIX regime
- Calibre R/R (ATR, convexe, spread filter)
- Décide d'investir ou non — documente le raisonnement complet
- Monitore les positions ouvertes (trailing stop, time stop)
- Publie les trades exécutés sur le bus → Agent Journal les consomme

**Log visible** : candidats évalués, raisons de rejet, trade sélectionné, sizing, R/R, état positions

**Extensibilité** : Prêt pour `agent_trader_2.py`, `agent_trader_3.py` (différentes stratégies/profils de risque). Le framework supporte N traders dès le départ.

### 4. Agent Journal (`agent_journal.py`)
**Rôle** : Documentation et analyse business, expertise spéculation
**Hérite de** : `journal.py`
**Schedule** : 22h CET (clôture) + réactif sur trade exécuté
**Actions** :
- À chaque trade : note immédiatement le contexte d'entrée
- À 22h : ferme toutes les positions, calcule P&L réel (15min/1h bars)
- Calcule MAE/MFE, slippage, realized R/R
- Analyse la qualité de chaque décision
- Publie les résultats sur le bus → Agent Learning les consomme

**Log visible** : trades fermés, P&L, TP/SL/EXPIRED, métriques qualité, alertes

### 5. Agent Learning (`agent_learning.py`)
**Rôle** : ML expert, optimisation continue des paramètres
**Hérite de** : `learning.py`, `backtest.py`
**Trigger** : Réagit après chaque journal run
**Actions** :
- Consomme les résultats du journal
- Recalcule les 6 dimensions de learning (ticker*cat, session, newscat, regime, direction, delay_bias)
- Met à jour les multipliers pour l'Agent Trader et l'Agent Scoring
- Génère le performance summary (alerts-only)
- Identifie les patterns, anomalies, drifts
- Publie les ajustements sur le bus → Agent Trader et Agent Scoring les consomment

**Log visible** : dimensions recalculées, multipliers modifiés, anomalies détectées, recommandations

### 6. Agent UX (`agent_ux/`)
**Rôle** : Frontend expert, 15 ans UX/flat design
**C'est le frontend React** redesigné avec une vision agent-centric

## Architecture Technique

### Message Bus (PostgreSQL)

```sql
CREATE TABLE agent_messages (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    from_agent VARCHAR(50) NOT NULL,
    to_agent VARCHAR(50),          -- NULL = broadcast
    msg_type VARCHAR(50) NOT NULL, -- 'news_collected', 'news_scored', 'trade_executed', etc.
    payload JSONB NOT NULL,
    consumed BOOLEAN DEFAULT FALSE
);

CREATE TABLE agent_logs (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    agent_name VARCHAR(50) NOT NULL,
    level VARCHAR(10) DEFAULT 'INFO',  -- INFO, WARN, ERROR, DECISION
    action VARCHAR(100) NOT NULL,
    details JSONB,
    duration_ms INTEGER
);
```

### Agent Base Class

```python
# backend/app/agents/base.py
class BaseAgent:
    name: str
    description: str

    def publish(msg_type, payload, to_agent=None)  # Publie sur le bus
    def consume(msg_types) -> list                  # Consomme depuis le bus
    def log(action, details, level="INFO")          # Log structuré visible frontend
    def get_logs(limit=50) -> list                  # Récupère ses propres logs
    def run()                                       # Boucle principale (override)
    def status() -> dict                            # État courant (idle/working/error)
```

### Structure Fichiers

```
backend/app/agents/
├── __init__.py
├── base.py              # BaseAgent + MessageBus + AgentLog
├── agent_news.py        # Agent News (collecte + sources)
├── agent_scoring.py     # Agent Scoring (Claude + formule)
├── agent_trader.py      # Agent Trader 1 (décision + monitoring)
├── agent_journal.py     # Agent Journal (clôture + analyse)
├── agent_learning.py    # Agent Learning (ML + optimisation)
└── registry.py          # Registre des agents + orchestration

# Les anciens modules restent comme librairies (pas supprimés)
# Les agents les importent et les orchestrent
backend/app/
├── news_collector.py    # → importé par agent_news
├── data_apis.py         # → importé par agent_news
├── source_monitor.py    # → importé par agent_news
├── news_scorer.py       # → importé par agent_scoring
├── trade_selector.py    # → importé par agent_trader
├── journal.py           # → importé par agent_journal
├── learning.py          # → importé par agent_learning
├── ...
```

### Nouveaux Endpoints API

```
GET  /api/agents                    → liste des 6 agents + status
GET  /api/agents/{name}/logs        → logs structurés d'un agent (paginé)
GET  /api/agents/{name}/status      → état courant (idle/working/error + dernière action)
GET  /api/agents/messages           → messages bus récents (debug)
POST /api/agents/{name}/trigger     → forcer un agent à s'exécuter
WS   /ws/agents/live                → WebSocket pour updates temps réel (optionnel phase 2)
```

### Refonte Frontend (Agent UX)

**Layout** : Vue agent-centric avec sidebar + main content

```
┌─────────────────────────────────────────────────────────────┐
│  ONESHOT NEWS TRADING — Agent Dashboard           [status]  │
├──────────┬──────────────────────────────────────────────────┤
│          │                                                  │
│  AGENTS  │   MAIN CONTENT (tab sélectionné)                │
│          │                                                  │
│ ┌──────┐ │   Vue par défaut: Dashboard avec les 6 agents   │
│ │ News │ │   en cards montrant leur activité en temps réel  │
│ │  ●   │ │                                                  │
│ ├──────┤ │   ┌─────────┐ ┌─────────┐ ┌─────────┐          │
│ │Score │ │   │ Agent   │ │ Agent   │ │ Agent   │          │
│ │  ●   │ │   │ News    │ │ Scoring │ │ Trader  │          │
│ ├──────┤ │   │ 42 news │ │ 12 scrd │ │ 1 trade │          │
│ │Trade │ │   │ 0 err   │ │ 3.2k tk │ │ LONG GC │          │
│ │  ●   │ │   └─────────┘ └─────────┘ └─────────┘          │
│ ├──────┤ │   ┌─────────┐ ┌─────────┐ ┌─────────┐          │
│ │Journl│ │   │ Agent   │ │ Agent   │ │ Agent   │          │
│ │  ○   │ │   │ Journal │ │ Learning│ │ UX      │          │
│ ├──────┤ │   │ 3 close │ │ 6 dims  │ │ online  │          │
│ │Learn │ │   │ +1.2%PnL│ │ 2 alert │ │         │          │
│ │  ○   │ │   └─────────┘ └─────────┘ └─────────┘          │
│ ├──────┤ │                                                  │
│ │  UX  │ │   + Tabs existants en dessous:                  │
│ │  ●   │ │   [Journal] [Historique] [Performance]          │
│ └──────┘ │                                                  │
└──────────┴──────────────────────────────────────────────────┘
```

**Sidebar gauche** : Les 6 agents avec indicateur d'état (● actif, ○ idle, ✕ erreur)
- Clic sur un agent → affiche ses logs détaillés dans le main content

**Page Agent Detail** (quand on clique sur un agent dans la sidebar) :
- Header : nom, description, état, dernière action, durée
- Timeline : logs structurés scrollables (action, détails, timestamp)
- Métriques clés en temps réel (spécifiques à chaque agent)

**Onglets existants** conservés et améliorés :
- Dashboard : overview des 6 agents (cards résumé)
- Journal : inchangé (enrichi avec les logs de l'Agent Journal)
- Historique : inchangé
- Performance : inchangé (enrichi avec insights de l'Agent Learning)

## Plan d'Implémentation (ordre)

### Phase 1 : Infrastructure (agents/base.py + DB)
1. Créer `backend/app/agents/__init__.py` et `base.py` (BaseAgent, MessageBus, AgentLog)
2. Ajouter tables `agent_messages` et `agent_logs` dans `database.py`
3. Créer `registry.py` — registre + orchestration des agents

### Phase 2 : Migration des 5 agents backend
4. `agent_news.py` — wraps news_collector + data_apis + source_monitor + event_scanner
5. `agent_scoring.py` — wraps news_scorer
6. `agent_trader.py` — wraps trade_selector + economic_calendar + position_monitor
7. `agent_journal.py` — wraps journal
8. `agent_learning.py` — wraps learning + backtest

### Phase 3 : Intégration main.py
9. Refactorer `main.py` : remplacer les appels directs par les agents
10. Refactorer `scheduler.py` : le scheduler orchestre les agents
11. Ajouter les nouveaux endpoints API (`/api/agents/*`)

### Phase 4 : Refonte Frontend
12. Nouveau layout avec sidebar agents
13. Page Agent Detail (logs timeline)
14. Dashboard agent-centric (6 cards)
15. Enrichir Journal et Performance avec données agents

### Phase 5 : Multi-Trader
16. Framework pour N traders (agent_trader_2.py, etc.)
17. Agrégation des décisions multi-traders dans le dashboard

## Principes

- **Les agents wrappent les modules existants** — pas de réécriture du code métier
- **Communication async via bus PG** — découplage total
- **Chaque agent est testable indépendamment**
- **Le frontend montre TOUT** — transparence totale sur ce que fait chaque agent
- **Fallback JSON** : le bus marche aussi en JSON si PG est down
- **Pas de sur-engineering** : les agents sont des classes Python simples, pas un framework complexe
