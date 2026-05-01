# Audit QA — OneShot Finance

Score : **6.5/10**
Date : 2026-05-01

Justification : volume de tests honorable (889 collectés, 1288 lignes de e2e, 82 scénarios end-to-end) et discipline de mocking présente sur les chemins critiques (Trader 2 `_fetch_current_price` mocké). Mais 9 failures à l'instant T (toutes "version drift" sauf une régression réelle Scoring 3), 15 agents sur 21 sans fichier de tests dédié, 2 fichiers en collection-error (sgmllib manquant) qui masquent l'état réel de la suite, zéro test pour le watchdog scan introduit en commit 8c410a7, et aucune CI visible pour bloquer les régressions. Une suite de tests qui a 9 failures permanentes signale soit un manque de discipline, soit un anti-pattern (assertions sur des constantes qui dérivent — voir Faiblesse #1). Score brut autour de 7 dégradé à 6.5 par la présence d'assertions de versions hardcodées qui contaminent durablement le signal.

## Top 3 forces

1. **Volume et stratification réels** : 889 tests collectés répartis en 21 fichiers, dont 4 fichiers d'intégration épais (`test_workflow_e2e.py` 1288L / 82 tests, `test_full_year_4teams.py` 1389L, `test_one_month_pipeline.py` 1310L, `test_full_system.py` 1139L). Le pipeline scoring → trader → journal → learning est rejoué de bout en bout sur multi-jours et multi-équipes, ce qui est rare sur un projet hérité de cette taille.

2. **Discipline de mocking sur le réseau** : les 4 fichiers d'intégration les plus longs patchent `_fetch_current_price` au niveau de chaque agent (`patch("backend.app.agents.agent_trader_2._fetch_current_price", ...)`) plutôt que de hit yfinance/Twelve Data. 113 patches dans `test_one_month_pipeline.py`, 120 dans `test_full_year_4teams.py`, 121 dans `test_agents.py`. Une vraie ceinture de sécurité.

3. **Tests de non-régression spécifiquement annotés** : la base `test_agents.py` contient des classes nommées par fix (ex. `TestJournalJsonAtomicWrite`, `TestTradeSelectionTimeout`, `TestLearning34WeeklyConfigPersistence`, `TestTrader2Fixes`). Chaque audit "v6.x/v7.x/v8.x" du CLAUDE.md a généré sa famille de tests — le pattern "bug = test" est appliqué, même si imparfaitement (voir #1 des faiblesses).

## Top 5 faiblesses

| # | Sévérité | Problème | Impact |
|---|---|---|---|
| 1 | P0 | **Anti-pattern `test_version_bumped` : 8 failures sur 9 sont des assertions de versions hardcodées déjà périmées.** Les versions du code ont avancé (Scoring3 2.6→3.0, Trader3 2.5→3.1, Journal3 2.2→3.0, Learning3 2.3→3.0, Trader4 2.2→2.2 OK, Journal4 2.1 OK, Learning4 2.2, Performance 8.4 OK, Scoring2 8.2 OK, Trader2 8.0→8.1, Learning2 7.5→7.5 mais regex test échoue) mais les `assert AgentX.version == "X.Y"` sont restées figées dans `test_agents.py` (lignes 1135, 1199, 1255, 1315, 1602 etc.). Ces tests ne testent rien — ils punissent l'auteur du bump de version. | La CI est rouge en permanence → désensibilisation : un vrai bug se noiera dans le bruit. Symptôme d'une suite qui a perdu sa valeur de signal. |
| 2 | P0 | **Le 9ème failure (`test_sma_200_computed`) est une régression réelle non détectée** : l'indicateur SMA 200 n'est plus calculé par `_compute_all_indicators` (assertion `"sma_200" in indicators` échoue). Le test passait avant le bump Scoring3 3.0. Aussi `test_pipeline_trader1_failure_doesnt_block_teams` (TestTradeSelectionTimeout) échoue — graceful degradation cassée. | Les 8 failures cosmétiques de version ont enseveli 2 vraies régressions de prod (un indicateur de Team 3 disparu et un fallback inter-équipes cassé). C'est exactement le scénario que la suite est censée empêcher. |
| 3 | P1 | **2 fichiers en erreur de collection (`test_event_scanner.py`, `test_weekend.py`)** : `ModuleNotFoundError: No module named 'sgmllib'` (feedparser→sgmllib retiré de Python 3.11). Pytest interrompt la collection (`Interrupted: 2 errors during collection`) mais malgré ça collecte 889 tests — ces deux fichiers ne tournent JAMAIS dans cet environnement. Sur Replit la situation est probablement différente, mais aucune CI ne le valide. | Le test critique du blocage week-end (`test_weekend.py`) et de l'event scanner (10min cron) ne tournent ni en local ni vraisemblablement en CI : zéro garantie que les scans ne se déclencheront pas un dimanche. |
| 4 | P1 | **Aucun test dédié pour 4 fonctions critiques de `main.py` ajoutées au commit 8c410a7** : `_run_scan_watchdog` (ligne 714), `_recover_missed_scans_on_startup` (ligne 279), `_get_latest_scan_info` (ligne 383). Seul `_recover_pending_trades_on_startup` a 2 tests, et encore — ce sont des `inspect.getsource()` qui assertent que le texte source contient certains tokens (`recovery_start = source.find("_recover_pending_trades_on_startup")` à test_agents.py:2375). C'est du test de présence de chaîne, pas de comportement. | Le watchdog est le mécanisme qui compense les misfires APScheduler : s'il a un bug, des scans seront silencieusement perdus, et personne ne le saura. Et tester par `inspect.getsource()` est un anti-pattern : un refactor légitime casse le test, un vrai bug logique passe. |
| 5 | P1 | **15 agents sur 21 n'ont aucun fichier de tests dédié**. Seuls `agent_scoring_2`, `agent_scoring_3`, `agent_trader_2`, `agent_journal_2` (via test_journal2_learning2.py), `agent_learning_2` (idem) sont couverts par fichier nominatif. Les 15 autres (incl. Auditor, Performance, Infrastructure, News, Trader 1, tous les Team 3/4) sont uniquement testés via `test_agents.py` (2413L mono-fichier, devenu fourre-tout). Aucun fichier dédié pour `market_data.py` (le module qui contient le mapping `type=commodities` critique — un fix que CLAUDE.md décrit comme évitant de confondre un ETF ~287€ avec le cocoa ~3435$, et personne ne teste ce mapping unitairement). Ni pour `database.py`, `scheduler.py`, `main.py`, `news_collector.py`, `position_monitor.py`. | Cohérence de couverture en dent de scie : les fixes récents (Team 2) ont des tests dédiés bien faits, l'historique (Team 1) est dans le fourre-tout. Quand `test_agents.py` dépasse 2400L, la maintenance devient un anti-pattern à part entière. |

## 5 recommandations chiffrées

1. **[P0] Supprimer ou dériver les 8 `test_version_bumped` cassés** (1h, ~25 lignes touchées). Deux options propres :
   - Option A (rapide) : supprimer purement et simplement les 8 assertions `assert AgentX.version == "..."` — ces tests ne capturent aucun comportement métier, ils sont du papier de toilette. Économie nette de complexité.
   - Option B (préférable) : remplacer par un `test_versions_documented_in_claude_md` unique qui parse la table "Versions actuelles" du CLAUDE.md et compare aux `agent.version` réels en runtime — ça force la mise à jour de la doc à chaque bump et c'est un seul point de maintenance. Bonus : si quelqu'un bump le code sans toucher CLAUDE.md, le test catch.

2. **[P0] Investiguer + fixer les 2 vraies régressions noyées** (2h, ~50 lignes de fix). `test_sma_200_computed` : vérifier si `_compute_all_indicators` doit calculer SMA 200 (CLAUDE.md mentionne "SMA/EMA 20/50/200" dans les indicateurs Team 3) — si oui, c'est un bug Scoring 3.0 à corriger ; si non, mettre à jour le test. `test_pipeline_trader1_failure_doesnt_block_teams` : exécuter localement, lire la trace, fixer la propagation d'erreur dans `run_scan_pipeline()`. Ces deux tests doivent passer ou disparaître — pas rester FAILED en permanence.

3. **[P1] Réparer la collection des 2 fichiers cassés** (30min, 5 lignes). `pip install sgmllib3k` ou remplacer la dep `feedparser` par `feedparser>=6.0.10` dans `requirements.txt` (la 6.0.11 a contourné le problème sgmllib). Sans ça, `test_weekend.py` et `test_event_scanner.py` sont des tests fantômes — collectés "OK" implicitement mais jamais exécutés, signal trompeur. Ajouter une CI GitHub Actions minimale (40 lignes de YAML) qui fait `pytest backend/tests/ --tb=short --maxfail=5` sur push/PR — actuellement aucune CI n'est visible et toute la qualité repose sur l'exécution manuelle.

4. **[P1] Couvrir les 4 fonctions startup/watchdog avec de vrais tests de comportement** (3h, ~120 lignes / 8 tests). Créer `backend/tests/test_main_startup.py` : 
   - 2 tests pour `_run_scan_watchdog` (cas "scan en retard >15min déclenche relance" / cas "scan récent ne déclenche rien") avec freezegun pour le temps
   - 2 tests pour `_recover_missed_scans_on_startup` (cas "redémarrage en milieu de journée trouve un scan manqué" / cas "redémarrage le matin ne fait rien")
   - 2 tests pour `_get_latest_scan_info` (cas "PG renvoie une ligne" / cas "PG vide → fallback JSON")
   - 2 tests pour `_recover_pending_trades_on_startup` qui remplacent les `inspect.getsource()` actuels par un vrai test de comportement avec un trade PENDING simulé
   Bannir tout `inspect.getsource(...).find("nom_de_fonction")` — c'est un test à supprimer le jour du refactor.

5. **[P1] Décomposer `test_agents.py` (2413L) et créer 5 fichiers de test dédiés manquants** (4h, ~600 lignes redistribuées). Découper par agent : `test_agent_news.py`, `test_agent_scoring.py`, `test_agent_trader_4.py`, `test_agent_auditor.py`, `test_agent_performance.py`, `test_market_data.py`. Le dernier est le plus critique : il faut au moins un test par mapping `type=commodities` (CC1, KC1, SB1, HG1, JO1, LC1, LH1) qui valide que le prix retourné est dans `TD_PRICE_RANGES` du ticker — sinon la régression "cocoa ETF ~287€ au lieu de cocoa future ~3435$" peut réapparaître à n'importe quelle update yfinance/Twelve Data sans personne pour la voir. Ce test sauve potentiellement le projet d'une perte sèche le jour où TD change un mapping.

## Limites de l'audit

- **Audit statique principalement** : j'ai exécuté `test_agents.py` une fois (PASS hors 9 FAILED), mais pas l'intégralité des 889 tests par contrainte de temps. Mes conclusions sur les autres fichiers sont basées sur la lecture statique (taille, mocks, noms de tests).
- **Pas d'accès à la CI** : aucun GitHub Actions / Replit CI workflow n'est visible dans le repo (absence de `.github/workflows/`). Je ne peux pas dire si la suite tourne réellement quelque part — elle pourrait passer en local sur la machine du dev mais ne jamais bloquer un déploiement.
- **Coverage % non mesuré** : pas de `pytest-cov` dans cet environnement, donc je ne peux pas chiffrer la couverture par module. Mes estimations de "modules sans tests" sont basées sur l'absence de fichier `test_X.py`, pas sur le coverage réel — un module peut être couvert indirectement par un test e2e.
- **Tests d'intégration multi-jours non rejoués** : `test_full_year_4teams.py` (1389L) et `test_one_month_pipeline.py` (1310L) tournent probablement plusieurs minutes avec leurs 113-120 mocks ; je n'ai pas pu valider qu'ils passent dans cet environnement.
- **Mutation testing absent** : aucun usage de Stryker / mutmut / cosmic-ray dans le repo. Impossible de mesurer la qualité réelle des assertions — un test peut "passer" tout en étant inutile (cf. les `test_version_bumped` qui en sont la démonstration parfaite).
- **`test_data_apis.py` :: `test_collect_structured_data_returns_list` est flaky par design** (CLAUDE.md ligne ~720) — dépend des conditions live de marché (volume SHFE/LME). Non testé ici, mais documenté comme fragile.

## Métriques observées

- **Total tests collectés** : 889 (avec 2 erreurs de collection bloquant la complétion)
- **Tests passants (test_agents.py uniquement)** : 207 / 216
- **Failures (test_agents.py uniquement)** : 9 (8 cosmétiques version + 1 régression réelle SMA 200 + 1 régression pipeline trader1)
  - `TestTeam3V2Scoring3::test_sma_200_computed` — RÉGRESSION RÉELLE
  - `TestTeam3V2Scoring3::test_version_bumped` (assert "2.6", actual "3.0")
  - `TestTeam3V2Trader3::test_version_bumped` (assert "2.5", actual "3.1")
  - `TestTeam3V2Journal3::test_version_bumped` (assert "2.2", actual "3.0")
  - `TestTeam3V2Learning3::test_version_bumped` (assert "2.3", actual "3.0")
  - `TestTradeSelectionTimeout::test_pipeline_trader1_failure_doesnt_block_teams` — RÉGRESSION RÉELLE
  - `TestJournalJsonAtomicWrite::test_journal3_version_bumped` (assert "2.2", actual "3.0")
  - `TestTrader2Fixes::test_version_bumped` (assert "8.0", actual "8.1")
  - `TestLearning34WeeklyConfigPersistence::test_learning3_version_bumped` (assert "2.3", actual "3.0")
- **Erreurs de collection** : 2 fichiers (`test_event_scanner.py`, `test_weekend.py` — `ModuleNotFoundError: sgmllib`)
- **Tests de version cassés au total** : 8 sur 14 assertions `test_version_bumped` détectées dans `test_agents.py`
- **Modules sans test dédié (15 / 21 agents + 6 / 18 modules core)** :
  - Agents : `agent_news`, `agent_scoring`, `agent_scoring_4`, `agent_trader`, `agent_trader_3`, `agent_trader_4`, `agent_journal`, `agent_journal_3`, `agent_journal_4`, `agent_learning`, `agent_learning_3`, `agent_learning_4`, `agent_performance`, `agent_auditor`, `agent_infrastructure` (note : `journal_2`/`learning_2` partagent un fichier `test_journal2_learning2.py` — couverts)
  - Modules core : `market_data.py` (CRITIQUE — mapping commodities), `position_monitor.py`, `news_collector.py`, `database.py`, `scheduler.py`, `scan_history.py`, `main.py`
- **Tests réseau/flaky** : 7 fichiers contiennent des références `yfinance|anthropic|requests.get|fetch_price|httpx` (`test_data_apis.py`, `test_agent_trader_2.py`, `test_full_system.py`, `test_agents.py`, `test_full_year_4teams.py`, `test_one_month_pipeline.py`, `test_workflow_e2e.py`) — la plupart correctement mockés mais à auditer 1 par 1. Flaky documenté : `test_collect_structured_data_returns_list` dans `test_data_apis.py`.
- **Plus gros fichiers de tests (lignes)** : test_agents.py 2413L, test_full_year_4teams.py 1389L, test_one_month_pipeline.py 1310L, test_workflow_e2e.py 1288L, test_full_system.py 1139L — total 14286 lignes de tests pour ~21 agents + 18 modules.
- **Anti-pattern `inspect.getsource(...).find(...)`** détecté à 2 endroits (test_data_persistence.py:636, test_agents.py:2375) — à remplacer par des tests de comportement.
- **CI/CD** : aucun workflow GitHub Actions ni configuration CI Replit détectable (absence de `.github/workflows/`, `.replit` non audité). À confirmer côté repo distant.
