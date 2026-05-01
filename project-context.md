# OneShot Finance — Project Context

## Projet
Systeme de news trading multi-agent (4 equipes) sur commodities, forex, indices, actions. Backend FastAPI + Frontend React. Deploy Replit.

## Branche active
`claude/review-indicators-timeframe-0XR8K`

## Historique des interventions agents

| Session | Date | Agent/Outil | Livrable | Statut | Pourquoi / Alternatives ecartees |
|---------|------|-------------|----------|--------|----------------------------------|
| 1 | 2026-05-01 | Claude Code direct | Post-reset scan cooldown (5 min) | complet | Apres reset, scans recreaient des trades immediatement. Alternative: bloquer scans dans le reset endpoint — ecartee car le scheduler est independant |
| 1 | 2026-05-01 | Claude Code + Explore | Fix scoring API timeout pipeline | complet | Scoring timeout retournait [] silencieusement, pipeline retournait early, Teams 2-4 ne tournaient pas. Alternative: propager exception — ecartee car le pipeline doit toujours retourner un dict |
| 1 | 2026-05-01 | Claude Code + Explore | Fix 4 bugs prod (batch size, fresh start T2, stale count T3, Open-Meteo) | complet | Batch 50→15 (timeout Claude), fresh start detection (DB vs new), stale active_count (silent except), weather workers 6→12 + timeout 45→90s |
| 1 | 2026-05-01 | Claude Code + Explore | Fix closed positions not showing in dashboard | complet | Positions fermees par position monitor disparaissaient entre pending et journal (22h). Fix: fetch /api/trades au lieu de /api/trades/pending |
| 1 | 2026-05-01 | Claude Code + Explore | Calendrier economique per-ticker | complet | BOE bloquait KC=F (cafe) — zero correlation avec taux GBP. Fix: MACRO_EXEMPT_TICKERS + CURRENCY_SENSITIVE_TICKERS. Alternative: desactiver le calendrier pour commodities — ecartee car USDA WASDE doit bloquer les agri |
| 1 | 2026-05-01 | Claude Code | Auditor calendar_blocking check enrichi | complet | Spot-check live: KC=F exempt, forex sensible. Score 9 si per-ticker filter OK |
| 1 | 2026-05-01 | Claude Code + Explore | Fix PG SSL connection drop (pool reset + retry) | complet | SSL drop a 07:45 tuait le scan 07:50 silencieusement. Fix: reset_pool(), try/except top-level dans pipeline, retry 3x |
| 1 | 2026-05-01 | Claude Code + Explore | Scan watchdog + misfire_grace_time 900s | complet | Scan 17:00 saute par Replit container sleep. misfire 300→900s + watchdog toutes les 30min retrigger les scans manques |

## Memo de reprise — derniere session

- **Date et heure de cloture** : 2026-05-01 ~20:00 CET
- **Numero de session** : 1

### Resume de la session
Session inaugurale focalisee sur la stabilite production. 8 commits correcting critical production bugs: scoring API timeout killing entire pipeline, PG SSL drops causing silent scan failures, Replit container sleep dropping scheduled scans, macro calendar blocking agricultural commodities incorrectly, closed positions disappearing from dashboard before journal runs. Infrastructure renforced with pool recovery, scan watchdog, and per-ticker macro filtering.

### Travaux en cours
- Aucun travail en cours — tous les commits sont complets et pushes

### Prochaines actions recommandees
1. **Monitoring post-deploy** : verifier que les 4 scans du jour suivant tournent tous (watchdog actif). Verifier les logs pour "WATCHDOG: scan X was NOT dispatched" — ne devrait plus apparaitre
2. **Audit Trader 2 fresh start** : verifier en production que le fix `freshly_created` fonctionne (pas de "Fresh start detected" dans les logs sauf apres un vrai reset)
3. **Performance scoring batches** : observer le temps de scoring avec batch size 15 (vs 50 avant). Si trop lent (>3 batches = >3 appels Claude), ajuster a 20-25

### Blockers eventuels
- Aucun blocker technique
- feedparser ne s'installe pas dans l'env Claude Code (sgmllib3k build failure) — n'impacte que les tests locaux, pas la prod

### Nom de branche recommande prochaine session
`claude/finance-s2-monitoring-post-deploy-`

### Commande de reprise suggeree
```
Reprise session 2 — Finance. Verifier que les 8 commits de la session 1 (branche claude/review-indicators-timeframe-0XR8K) fonctionnent en production. Checker les logs des scans du jour : watchdog, scoring batch timing, Trader 2 fresh start. Si tout OK, merger dans main. Sinon, corriger les regressions detectees.
```
