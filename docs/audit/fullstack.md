# Audit Fullstack — OneShot Finance
Score : 5.5/10
Date : 2026-05-01
Périmètre : 21 agents Python (`backend/app/agents/`), main.py monolithe (3022L), 32 composants React, registry singleton, message bus, 81 endpoints.

## Top 3 forces architecture

1. **Versioning explicite par agent + filtrage du learning par version** (`base.py:335`, `learning.py::_filter_by_current_versions`). Pratique rare et puissante : chaque agent a un `version` class attribute, chaque trade est stamped avec `agent_versions`, et le learning filtre les trades par version courante. Évite la contamination des données quand la logique métier évolue. Pattern à conserver et généraliser dans tout refacto.
2. **Pipeline de scan résilient (try/except + PG pool recovery)** dans `registry.py:191-253`. Top-level try/except avec détection des erreurs PG transitoires (`_is_pg_transient_error`), reset du pool et retry une fois, puis fallback dégradé qui persiste un scan d'erreur explicite (jamais de crash silencieux). Chaque équipe est isolée par try/except — Team 1 down ne casse pas Teams 2-4.
3. **Frontend déjà refactoré sur le bon pattern** : 4 pages d'équipes consolidées dans un seul `TeamPage.jsx` (1266L) paramétré par `team1/2/3/4`, lazy-loading, ErrorBoundary, polling adaptatif (`document.hidden`). Découplage navigation/pages sain via hash routing. Aucune duplication de code Team 1/2/3/4 côté React.

## Top 5 faiblesses

| # | Sévérité | Problème | Lignes/Modules concernés | Coût refactor |
|---|---|---|---|---|
| 1 | P0 | **Duplication massive Trader/Journal/Learning 2-3-4** : `_ensure_positions_file`, `_load_positions`, `_save_positions`, `_pg_load_positions`, `_pg_save_positions`, `_load_positions_json_only`, `_fetch_current_price`, `_get_agent_versions` répétés à l'identique dans 3 traders ; `_clamp`, `_compute_decay_weight`, `_is_significant`, `_filter_by_current_versions`, `_compute_adj`, `_load/_persist_weekly_config`, `_load/_persist_config_history` répétés dans 3 learnings ; `_ensure_journal_file`, `_pg_load_entries`, `_pg_save_entries`, `_compute_mae_mfe` répétés dans 3 journals. Estimation : **~700-900 lignes dupliquées** (≈11% des 8000 lignes des 9 agents Team 2/3/4). | `agent_trader_{2,3,4}.py` (lignes 92-237 chacun), `agent_journal_{2,3,4}.py` (lignes 41-220 chacun), `agent_learning_{2,3,4}.py` (lignes 71-240 chacun) | 12-16h |
| 2 | P0 | **main.py = 3022L god file** : 110+ fonctions internes (recovery, scheduler jobs, cleanup, admin) + 81 endpoints HTTP dans un seul fichier. Aucune séparation entre lifecycle, jobs cron, et API. Très difficile à naviguer, tests E2E quasi impossibles à isoler par feature. | `main.py:1-3022` (138 fonctions) | 8-12h |
| 3 | P1 | **Agent Auditor = 4146L, 22 profils dans un seul fichier** : 22 méthodes `_audit_<X>` de 150-300 lignes chacune. Chaque profil pourrait vivre dans son propre module sans aucun couplage. La plupart des `_audit_*` ne se réutilisent pas mutuellement. Violation flagrante du SRP. | `agent_auditor.py:1-4146` | 6-8h |
| 4 | P1 | **Lazy imports systémiques (anti-pattern circular)** : `from ..database import is_pg_enabled/get_conn` répété **à l'intérieur de chaque fonction** dans tous les agents (7-8 occurrences par agent). Symptôme classique d'un cycle d'imports non résolu (`agents/` → `database` → `agents/` ?). 80 occurrences de `hasattr(...)` et 24 de `getattr(obj, "field", default)` (alors que CLAUDE.md v6.4 disait avoir nettoyé) montrent que la couche modèle reste ambiguë. | `agent_trader_2.py:101,122,147,177,213,362`, `agent_journal_4.py:60,83,115`, `agent_performance.py:302,514,554,602,1149,1167,1175`, etc. | 4-6h |
| 5 | P1 | **API design incohérent — naming et nesting** : 81 endpoints sur 4 équipes mais nomenclature mélangée (`/api/trader2/...`, `/api/scoring2/result`, `/api/learning3/weekly-config`, `/api/journal3/weekly-summary`). Pas de versioning (`/api/v1/`). Pas de préfixes par équipe (`/api/team/2/trader`). Difficile de lister/router proprement. Aucun `APIRouter` FastAPI utilisé — tout est `@app.get/post` direct sur l'instance. | `main.py:1296-3000` (81 endpoints) | 4-6h |

## 5 refactor opportunities chiffrées

### 1. [P0] BasePersistedAgent + BaseTradingAgent → factorise ~700 lignes (12-16h)

Créer dans `backend/app/agents/`:

- **`base_persistence.py`** (~120 lignes) : classe `JsonPgStore[T]` paramétrée par `(table_name, json_file, json_serializer)` qui expose `load() / save() / load_json_only()`. Remplace les 3×~110 lignes de PG/JSON load/save dans Trader 2/3/4 et les 3×~80 lignes dans Journal 2/3/4.
- **`base_trader.py`** (~180 lignes) : `BaseTradingAgent(BaseAgent)` avec `_fetch_current_price`, `_get_agent_versions`, `_check_correlation_conflict`, `reset_daily_counters`, `_update_prices_parallel`. Trader 2/3/4 héritent et n'implémentent que `run()` + logique métier propre (~600 lignes au lieu de ~1050 chacun).
- **`base_learning_math.py`** (~150 lignes) : `_clamp`, `_compute_decay_weight`, `_is_significant`, `_filter_by_current_versions`, `_compute_adj`, `_compute_sharpe`, `_load/_persist_weekly_config`, `_load/_persist_config_history`. Importé par Learning 2/3/4 (élimine 3×~250 lignes dupliquées).

Bénéfice : 22738 lignes → ~21900. Single source of truth pour la persistence (corrige risque de divergence — déjà observé `tech_journal_entries` UNIQUE constraint mismatch d'après CLAUDE.md).

### 2. [P0] Découper main.py 3022L → 6 modules de ~500L (8-12h)

```
backend/app/
├── main.py                 (~200L : FastAPI init, lifespan, scheduler wiring, mount static)
├── lifecycle.py            (~250L : _recover_pending_trades, _recover_missed_scans, _keepalive_loop, _one_time_cleanup)
├── scheduler_jobs.py       (~600L : 25 fonctions _run_*_scan, _run_event_check, _run_position_monitor_*, _run_daily_journal, _run_scan_watchdog)
├── api/
│   ├── __init__.py         (APIRouter init)
│   ├── scan.py             (/api/scan/* — 4 endpoints)
│   ├── trades.py           (/api/trades, /api/performance, /api/learning, /api/journal — Team 1)
│   ├── teams.py            (/api/trader{2,3,4}, /api/scoring{2,3,4}, /api/journal{2,3,4}, /api/learning{2,3,4} — 33 endpoints)
│   ├── agents.py           (/api/agents/*, /api/agents/auditor/*)
│   ├── infrastructure.py   (/api/infrastructure/*, /api/db/*, /api/health, /api/source-health)
│   ├── performance.py      (/api/performance/*)
│   └── admin.py            (/api/admin/*, reset, fix-entry-price)
```

Chaque module exporte un `APIRouter` ; `main.py` fait `app.include_router(scan.router)`. Tests par module possibles. PR review reasonable (chaque module < 600L).

### 3. [P1] Splitter agent_auditor.py 4146L → 4 modules par équipe (6-8h)

```
agents/auditor/
├── __init__.py             (re-export AgentAuditor singleton)
├── agent_auditor.py        (~400L : classe orchestrateur, AUDIT_PROFILES dispatcher, _analyze_agent_errors helper, _compute_trends, persistence)
├── audit_shared.py         (~600L : _audit_news, _audit_scoring, _audit_infrastructure, _audit_performance, _audit_ux, _audit_self)
├── audit_team_1.py         (~700L : _audit_trader, _audit_journal, _audit_learning, _audit_news_for_trader_1)
├── audit_team_2.py         (~800L : _audit_trader_2, _audit_journal_2, _audit_learning_2, _audit_scoring_2, _audit_news_for_trader_2)
├── audit_team_3.py         (~700L : _audit_scoring_3, _audit_trader_3, _audit_journal_3, _audit_learning_3)
└── audit_team_4.py         (~700L : _audit_scoring_4, _audit_trader_4, _audit_journal_4, _audit_learning_4)
```

L'orchestrateur dispatcher reste dans `AgentAuditor.run()` qui importe les fonctions per-team. Couplage zéro entre les fichiers d'audit team N (déjà aucune réutilisation cross-team). Permet à un dev de modifier les checks Team 3 sans rebuild mental d'un fichier de 4146 lignes.

### 4. [P1] Centraliser la couche I/O DB → éliminer les lazy imports (4-6h)

Créer `backend/app/agents/_db.py` (50 lignes) qui ré-exporte ce que les agents utilisent :

```python
from ..database import is_pg_enabled, get_conn  # imports module-level, pas function-level
```

Puis remplacer dans tous les `agent_*.py` :
- `from ..database import is_pg_enabled` (lazy, dans 7 fonctions) → import top-level via `from ._db import is_pg_enabled`

Si un cycle d'import existe réellement, le résoudre proprement via une interface (`Protocol`) ou un module séparé `db_interface.py`. **Investigation requise** : 80 `hasattr` et 24 `getattr` sur des champs Pydantic suggèrent que l'on importe les modèles tardivement pour casser un cycle — à confirmer par `python -X importtime`.

Bénéfice : performance startup (les imports cachés s'exécutent à chaque appel de fonction, et coûtent à la première invocation), lisibilité, suppression du pattern `try: from .. import X / except: pass`.

### 5. [P1] FastAPI APIRouter + versioning + préfixes par domaine (4-6h)

Refactor des 81 endpoints :

```python
# api/teams.py
router = APIRouter(prefix="/api/v1/teams", tags=["teams"])

@router.get("/{team_id}/trader/positions")
def get_team_positions(team_id: int): ...

@router.get("/{team_id}/journal/entries")
def get_team_journal(team_id: int): ...

@router.get("/{team_id}/learning/adjustments")
def get_team_learning(team_id: int): ...
```

Élimine les 33 endpoints dupliqués `/api/trader2/positions`, `/api/trader3/positions`, `/api/trader4/positions` → un seul endpoint paramétré. `team_id` dispatché via un registry `{2: agent_trader_2, 3: agent_trader_3, 4: agent_trader_4}`. Frontend `TeamPage.jsx` reçoit déjà `teamId` en prop — il suffit de paramétrer les fetch URLs.

Conserver `/api/v0/...` en alias avec `Deprecated` pendant 2 sessions pour compat frontend, puis supprimer.

## Métriques code

- **main.py** : 3022 lignes (138 fonctions, 81 endpoints HTTP)
- **backend/app/agents/** : 21 fichiers, **22738 lignes total**
  - Top 5 par taille : `agent_auditor.py` (4146), `agent_scoring_3.py` (1895), `agent_performance.py` (1178), `agent_trader_2.py` (1089), `agent_trader_3.py` (1045)
  - 9 agents Team 2/3/4 (traders + journals + learnings) : ~7100 lignes, **~700-900 dupliquées (11%)**
- **frontend/src/components/** : 32 fichiers JSX, 10651 lignes total
  - Plus gros : `TeamPage.jsx` (1266 — bon, factorise 4 équipes), `DashboardPage.jsx` (1043), `PerformancePage.jsx` (900), `Journal.jsx` (764)
  - **Aucune duplication Team1/2/3/4 côté React** — déjà refactoré
- **TODO/FIXME** : **0 occurrence** (suspect pour 22k+ lignes — soit discipline rare, soit absence de marquage de la dette)
- **`getattr(obj, "field", default)` sur Pydantic** : **24 occurrences** (cible 0 — quelques cas légitimes sur SDK Anthropic `response.usage`, mais beaucoup sur des champs internes d'agents)
- **`hasattr(...)`** : **80 occurrences** (la majorité dans `main.py` pour introspecter les agents — symptôme d'un manque d'interface formelle ; idéal : passer par `BaseAgent.get_metrics()` standardisé)
- **API endpoints** : **81** (43 Team 1 / partagés, 8 Team 2, 11 Team 3, 9 Team 4, 10 admin/infra)
- **Lazy imports `from ..` inside functions** : ~50 occurrences dans agents/ (anti-pattern circular workaround)
- **`fallback`** mentionné : 132 occurrences (PG → JSON fallback, normal mais à factoriser)

## Limites de l'audit

- **Lecture limitée** : ai lu intégralement `registry.py` et `base.py`, partiellement `agent_trader_2.py`, `main.py`, et signatures uniquement des autres agents (Read/Grep). Estimations de duplication basées sur signatures + patterns de nommage répétés ; un diff fonction-par-fonction confirmerait le pourcentage exact (typiquement ±5%).
- **Pas de profilage runtime** : les lazy imports ralentissent probablement le startup mais l'impact réel n'est pas mesuré (`python -X importtime` recommandé en next session).
- **Tests non audités** : `backend/tests/` non parcouru. La qualité de la couverture (mentionnée à 200+ tests dans CLAUDE.md) impacte le risque de chaque refacto. Avant de toucher Trader/Journal/Learning factorisation, il faut s'assurer que les tests E2E existants passent en baseline.
- **Frontend** : seul `App.jsx` lu intégralement et listing des composants. Pas vérifié la duplication de fetch hooks, le prop drilling potentiel dans `TeamPage.jsx` (1266L est suspect côté React — mérite son propre micro-audit).
- **CSS / styles** : non couvert.
- **Cyclic imports** : suspectés (lazy imports systémiques) mais pas confirmés par exécution réelle de l'arbre d'import.
- **Score 5.5/10** : reflète une architecture qui **fonctionne en production** (forces réelles : versioning, résilience, frontend factorisé) mais souffre d'une dette technique structurelle backend significative (P0 = ~700 lignes dupliquées + main.py god file). Un projet qui mérite 8/10 après les 5 refactors proposés (~35-50h total).
