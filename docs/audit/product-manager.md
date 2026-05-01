# Audit Roadmap & Priorisation — OneShot Finance
Score : 4/10 (PM-readiness)
Date : 2026-05-01
Auditeur : @product-manager

---

## Diagnostic produit en 3 phrases

1. **Le projet est un moteur technique sans boussole produit** : 21 agents, 28 rounds d'audit, 4 équipes de trading — mais zero North Star Metric défini, zero Definition of Done, et 100% des trades se terminent en EXPIRED depuis au moins 30+ jours, ce qui signifie que le système ne produit aucune donnée d'apprentissage réelle.
2. **L'itération est rapide mais sans cap de direction** : la liste CLAUDE.md des 28 améliorations successives ("Audit X v4.X") est une roadmap corrective sans vision forward — on fixe des bugs et on ajoute des agents, mais sans KPI de succès explicite pour savoir quand s'arrêter d'ajouter de la complexité.
3. **Priorité absolue avant toute nouvelle feature** : stopper le saignement (silent failure, EXPIRED 100%, endpoints non sécurisés), mesurer l'edge réel via backtest, et décider sur données si le système mérite une Phase 2 — toute autre décision de roadmap est prématurée.

---

## Vision & métriques manquantes

### Vision claire ?
**Non.** CLAUDE.md formule une philosophie ("edge sur signaux en avance de phase — pas les news que tout le monde commente") qui est pertinente et différenciante. Mais une philosophie n'est pas une vision produit actionnables. Il manque une réponse à : "Dans 6 mois, quel résultat chiffré prouvera que ce système a un edge réel ?" La distinction entre "projet d'apprentissage perso" et "système de trading réellement rentable" n'est pas documentée — or les décisions de priorisation changent radicalement selon la réponse.

### North Star Metric ?
**Absent.** Constaté formellement par @data-analyst (audit score 3.5/10). Aucune mention dans project-context.md ni CLAUDE.md. Proposition (à valider par Thomas) :

> **North Star Metric : Weekly TP_HIT ratio**
> = (TP_HIT) / (TP_HIT + SL_HIT) sur 7 jours glissants, calculé exclusivement sur les trades qui ont eu une issue actionnable (EXPIRED exclu du calcul d'edge).
> **Cible** : > 45% sur une fenêtre de 30 trades actionnés (TP+SL).
> **Alerte CRITICAL** : < 30% sur 10 derniers trades actionnés.

Métrique alternative si TP_HIT trop rares : `Expired Rate` comme métrique de santé inverse — cible < 40%, CRITICAL si > 80% pendant 7 jours.

### Definition of Done ?
**Absente.** Proposition :

Le système OneShot Finance est considéré "validé" (Phase 2 mérite) si, sur une fenêtre de **30 jours consécutifs avec au minimum 50 trades actionnés (TP ou SL)** :
- Win rate (TP_HIT / total_actionnés) ≥ 40%
- Sharpe ratio (annualisé, Équipe 1) ≥ 0.8
- Max drawdown < 5%
- Zero silent failure (aucun scan manqué sans alerte externe détectable dans les 30 min)
- Expired rate < 40%

En dessous de ces seuils = POC technique non validé → stopper les nouvelles features et corriger la calibration.

---

## Backlog priorisé (MoSCoW)

### MUST (Semaine 1-2) — Fondamentaux / Stop the bleeding

**Les items suivants bloquent toute mesure d'edge réelle. Tant qu'ils ne sont pas résolus, toute nouvelle feature est du bruit.**

1. **[P0] Alerte externe sur silent failure** — UptimeRobot (gratuit) sur `/api/health` avec alerting keyword `stale_critical` → email Thomas en < 5 min. Sans ça, le prochain incident de 21 jours est une question de "quand", pas de "si". (@infrastructure, 15 min de config Thomas, zéro code)

2. **[P0] Authentifier les endpoints destructifs** — `/api/admin/fix-entry-price`, `/api/infrastructure/reset`, `/api/infrastructure/reset-team/{id}` exposés sans auth sur une URL Replit publique. Un header `X-Admin-Token` sur ces endpoints suffit pour clore le risque immédiat. (@fullstack, 1-2h)

3. **[P0] Régénérer TWELVE_DATA_API_KEY leakée** — La clé `57627ad733b24fa78ac40652078c18fc` est en clair dans CLAUDE.md committé dans git. Régénération immédiate sur twelvedata.com + mise à jour Replit Secret. (Thomas, 5 min)

4. **[P0] Diagnostic data-driven 100% EXPIRED** — Lancer GET /api/journal, calculer avg(MAE) vs avg(MFE) vs target_pct sur les 30+ trades EXPIRED. Si avg(MFE) < 30% du target → confirme H1 (calibration trop ambitieuse) → réduire targets de 40-50% dans trade_selector.py. C'est le fix le plus impactant sur le North Star Metric. (@fullstack + @data-analyst, 2-4h)

5. **[P0] Activer Replit Hacker tier** — $7/mois pour always-on. Le keepalive interne ne suffit pas contre l'autoscale Replit (prouvé par l'incident 21j). Sans ça, tous les autres fixes de watchdog/recovery sont du papier. (Thomas, 5 min via billing)

6. **[P0] Alerte CRITICAL si expired_rate > 90% pendant 7 jours** — Ajoute le seuil manquant dans `_detect_alerts()` de agent_performance.py. Seuil actuel : 50% WARN seulement, notoirement insuffisant quand l'état réel est 100%. (@fullstack, 1h)

7. **[P0] Lancer le backtest v5.1** — `/api/price-archive/fill` puis `/api/backtest/replay` sur les 100 derniers scan_history. Premier run de validation que la formule de scoring produit un edge > 0 sur données historiques. Jamais lancé depuis la création en v5.1. (@fullstack, 1h de setup + lecture des résultats)

8. **[P1] Fixer les 2 vraies régressions QA noyées dans le bruit de version** — `test_sma_200_computed` (SMA200 absent de Scoring 3.0) et `test_pipeline_trader1_failure_doesnt_block_teams` (graceful degradation cassée). Ces régressions signalent des bugs prod actifs. (@qa + @fullstack, 2-3h)

### SHOULD (Semaine 3-4) — Validation edge

**À lancer une fois le saignement stoppé et les données EXPIRED diagnostiquées.**

1. **[P1] Recalibration TP/SL data-driven** — Si diagnostic MUST#4 confirme H1 (calibration trop ambitieuse), implémenter un `intraday_correction_factor` = ATR_intraday / ATR5j_daily dans trade_selector.py. Tester sur les tickers les plus actifs (NG=F, ZW=F, KC=F). (@fullstack, 4-6h)

2. **[P1] Ajouter Sharpe ratio + max drawdown dans agent_performance.py** — `_compute_trader_1_kpis()` ne calcule ni Sharpe ni max drawdown. Ces métriques sont essentielles pour évaluer si le système mérite d'être poursuivi. Code proposé dans le rapport @data-analyst. (@fullstack, 2h)

3. **[P1] North Star Metric dans le dashboard** — Exposer le Weekly TP_HIT ratio en KPI principal sur DashboardPage.jsx, avec couleur CRITICAL < 30% / OK > 45%. Rend le NSM visible à chaque ouverture du dashboard. (@fullstack, 2-3h)

4. **[P1] Désactiver temporairement le learning (multiplicateurs figés à 1.0)** — Le learning adaptatif sur 100% de données EXPIRED est du bruit pur : toutes les dimensions apprennent P&L ≈ 0%, les ajustements sont aléatoires. Figer jusqu'à avoir ≥ 30 trades TP ou SL. (@fullstack, 30 min)

5. **[P1] Supprimer les 8 test_version_bumped cassés** — Ces 8 assertions hardcodées contaminent le signal QA en permanence, masquant 2 vraies régressions (SMA200, graceful degradation). Remplacer par un test unique qui parse les versions depuis CLAUDE.md. (@qa, 1h)

6. **[P1] Réparer la collection des 2 fichiers test fantômes** — `test_event_scanner.py` et `test_weekend.py` ne tournent jamais (sgmllib manquant). `test_weekend.py` couvre le blocage week-end — un bug non testé ici est un bug non détecté en prod. (@qa, 30 min)

7. **[P1] Audit Team 4 opérationnelle** — Activée 2026-03-16, aucun trade visible. La silent failure de 21 jours a peut-être tué son activité. Vérifier via logs + `/api/trader4/positions` si Team 4 a tradé. Si aucun trade en 45 jours : identifier le blocage (scoring 4 ne reçoit pas de signaux ? confluence level jamais atteint ?). (@fullstack, 2h diagnostic)

8. **[P1] Backup PostgreSQL automatisé** — `pg_backup_to_json()` existe mais aucun cron ne l'appelle. RPO actuel = 0 (perte totale possible). Ajouter backup hebdomadaire dimanche 22h. (@fullstack, 1h)

### COULD (Mois 2) — Refacto / qualité

**À faire si l'edge est validé (Phase 2 confirmée) — pas avant.**

1. **[P2] BasePersistedAgent + BaseTradingAgent** — Factoriser ~700 lignes dupliquées dans Traders/Journals/Learnings 2-3-4. ROI élevé mais impacte ~9 agents simultanément, risque de régression. (@fullstack, 12-16h)

2. **[P2] Découper main.py 3022L → 6 modules** — God file avec 81 endpoints. Refacto propre en 6 fichiers + APIRouter. Aucun impact fonctionnel mais réduit drastiquement le risque lors d'ajouts futurs. (@fullstack, 8-12h)

3. **[P2] Exposer `/api/ai/token-usage`** — Visibilité sur le coût Claude réel (estimé 12-40$/mois selon @ia). Nécessite une table PG `claude_usage` pour survivre aux restarts. (@fullstack, 2-3h)

4. **[P2] CI GitHub Actions minimale** — 40 lignes de YAML pour que `pytest backend/tests/ --tb=short --maxfail=5` tourne sur push/PR. Actuellement zéro CI — toute qualité repose sur l'exécution manuelle. (@qa, 2h)

5. **[P2] Créer 5 fichiers de tests dédiés manquants** — `test_market_data.py` (critique : mapping type=commodities), `test_agent_news.py`, `test_agent_auditor.py`, `test_agent_performance.py`, `test_agent_trader_4.py`. (@qa, 4h)

### WON'T (this iteration) — Reportable sans justification business

1. **Team 5** — Aucune équipe supplémentaire avant que les équipes 1-4 produisent un edge mesuré. Ajouter de la complexité sur un système non-validé = amplification du bruit.

2. **Nouvelles sources de données** — 20 sources actives, 41 tickers, 28 rounds d'amélioration. Le problème n'est pas la couverture data — c'est la calibration TP/SL et la définition de l'edge.

3. **Frontend redesign** — La navigation centrée agents (sidebar, hash routing, TeamPage.jsx) fonctionne. Aucune valeur utilisateur à améliorer le frontend avant que le backend produise des TP_HIT à afficher.

4. **Nouveaux indicateurs techniques (Équipe 3)** — 11 stratégies (6 simples + 5 combos) déjà actives. Ajouter des stratégies avant d'avoir ≥ 30 trades par stratégie existante est statistiquement inutile (learning.py nécessite min 5-15 trades par dimension pour un signal significatif).

5. **Split agent_auditor.py 4146L** — Refacto légitime mais aucun impact fonctionnel. À faire en phase de qualité si le projet continue.

6. **Optimisation coût Claude (-$3-5/mois)** — À $4-6/mois actuel (ou $12-40/mois selon les estimations @ia), le gain absolu est négligeable par rapport aux enjeux de calibration.

---

## Roadmap 3 phases proposée

### Phase 1 : STOP THE BLEEDING (Semaine 1-2)

**Objectif** : zéro silent failure future + endpoints sécurisés + premier signal sur l'edge réel.

Actions clés :
- UptimeRobot configuré sur `/api/health` (alerte < 5 min)
- Replit Hacker tier activé ($7/mois)
- Endpoints admin authentifiés
- TWELVE_DATA_API_KEY régénérée
- Diagnostic MAE/MFE des EXPIRED lancé → calibration recalibrée si H1 confirmée
- Backtest v5.1 lancé pour la première fois
- Régressions QA critiques fixées (SMA200, graceful degradation)

**Critère de sortie Phase 1** :
- 7 jours consécutifs sans silent failure (4 scans/jour reçus et loggués)
- Au moins 1 trade TP_HIT ou SL_HIT observé (sortie du désert EXPIRED)
- Endpoints `/api/admin/*` protégés (test manuel)

### Phase 2 : VALIDATE EDGE (Semaine 3-4)

**Objectif** : répondre à la question "le système a-t-il un edge réel ?" avec des données, pas avec des hypothèses.

Actions clés :
- Analyse backtest : la formule (score/100)^1.5 produit-elle un edge > 0 sur 90j historiques ?
- Ranking des 4 équipes par WR réel (sur trades TP+SL uniquement)
- Sharpe ratio calculé pour Équipe 1
- North Star Metric (Weekly TP_HIT ratio) affiché en dashboard
- Learning défigé si ≥ 30 trades TP/SL disponibles

**Critère de sortie Phase 2 (go/no-go Phase 3)** :

| Métrique | GO | NO-GO |
|---|---|---|
| TP_HIT ratio (7j glissants) | ≥ 35% | < 25% après recalibration |
| Expired rate | < 60% | ≥ 80% après recalibration |
| Backtest edge | P&L simulé > 0 sur 90j | P&L simulé ≤ 0 sur 90j |
| Silent failures | 0 sur 14j | ≥ 1 sur 14j |

**Si NO-GO** : stopper le développement de nouvelles features. Revoir fondamentalement la formule de scoring et/ou la sélection d'actifs. Considérer un pivot vers un mode "signal only" sans exécution de trades.

### Phase 3 : DOUBLE DOWN ou KILL (Mois 2)

**Décision binaire basée sur les données de Phase 2.**

**Si GO** :
- Identifier l'équipe avec le meilleur Sharpe et doubler le budget de développement dessus
- Lancer le refacto BasePersistedAgent (COULD→SHOULD)
- Ajouter Sharpe + max drawdown à toutes les équipes
- Envisager Team 5 si Teams 1-3 saturent leur edge

**Si NO-GO** :
- Suspendre le mode trading actif (trades réels)
- Pivoter vers signal-only + backtest intensif pour valider la thèse AVANT de remettre du capital
- Ou: stopper le projet et documenter les apprentissages (objectif d'apprentissage atteint)

---

## Top 5 user stories prioritaires

| # | User story | Sévérité | Phase |
|---|---|---|---|
| 1 | En tant que Thomas, je veux recevoir une alerte email en < 5 min si le système n'a pas scanné depuis 2h, afin de ne plus découvrir un incident de 21 jours après coup | P0 | S1 |
| 2 | En tant que Thomas, je veux voir le Weekly TP_HIT ratio en KPI principal sur le dashboard, afin de savoir en un coup d'oeil si mon système a un edge réel | P0 | S1 |
| 3 | En tant que Thomas, je veux que mes endpoints `/api/admin/fix-entry-price` et `/api/infrastructure/reset` nécessitent un token secret, afin qu'un scanner web ne puisse pas corrompre ma base de données | P0 | S1 |
| 4 | En tant que Thomas, je veux voir le résultat du backtest v5.1 sur 90 jours (combien de trades auraient été TP_HIT avec la calibration actuelle), afin de décider si la formule de scoring mérite d'être poursuivie ou repensée | P1 | S2 |
| 5 | En tant que Thomas, je veux que le système touche au moins 1 TP ou SL par semaine (sortir du 100% EXPIRED), afin que le learning adaptatif dispose de signal réel pour s'améliorer | P1 | S2 |

---

## Definition of Done proposée

Le système OneShot Finance est considéré "validé — Phase 2 mérite" si **toutes** les conditions suivantes sont vraies simultanément sur une fenêtre de 30 jours consécutifs :

- [ ] **Edge mesuré** : TP_HIT / (TP_HIT + SL_HIT) ≥ 40% sur minimum 50 trades actionnés
- [ ] **Risk-adjusted return** : Sharpe ratio Équipe 1 ≥ 0.8 (annualisé)
- [ ] **Contrôle du risque** : Max drawdown < 5% sur la période
- [ ] **Fiabilité opérationnelle** : Zero silent failure — aucun scan manqué sans alerte externe dans les 30 min
- [ ] **Calibration saine** : Expired rate < 40% sur les 30 derniers trades de chaque équipe active
- [ ] **Learning utile** : ≥ 30 trades TP_HIT ou SL_HIT dans les données de learning (le minimum statistique pour une dimension avec t-stat > 1.5)

En dessous de ce seuil : le projet est un POC technique, pas un système de trading validé. Les décisions de roadmap (nouvelles features, nouvelles équipes, nouvelles sources data) sont prématurées.

---

## Analyse : complexité 21 agents vs valeur produite

### Tableau de rentabilité par équipe (état actuel)

| Équipe | Agents | Trades visibles | TP/SL observés | Status |
|--------|--------|----------------|----------------|--------|
| Équipe 1 (Day trading) | Trader 1, Journal 1, Learning 1, Scoring 1 | 30+ | 0 (100% EXPIRED) | Calibration cassée |
| Équipe 2 (Trend) | Trader 2, Journal 2, Learning 2, Scoring 2 | Inconnu (silent failure 21j) | Inconnu | À auditer |
| Équipe 3 (Technique) | Trader 3, Journal 3, Learning 3, Scoring 3 | Inconnu (silent failure 21j) | Inconnu | À auditer |
| Équipe 4 (Meta) | Trader 4, Journal 4, Learning 4, Scoring 4 | 0 visible depuis 2026-03-16 | 0 | Probablement inactive |

**Constat PM** : sur 21 agents déployés, l'edge réel de chacun est inconnu. La complexité a été ajoutée itérativement sans validation que l'équipe précédente performait. C'est un pattern de feature creep technique : "ajoutons une 4e équipe" avant d'avoir validé que les 3 premières ont un edge.

### Justification de la complexité (challenger)

La question PM à poser : **pourquoi 4 équipes simultanées plutôt que 1 équipe bien calibrée ?**

Arguments POUR la complexité actuelle :
- Diversification des stratégies (news / trend / technique / ensemble) = réduction de la variance
- Team 4 (meta-ensemble) est architecturalement solide : cherche les confirmations multi-équipes
- Le versioning par agent évite la contamination des données de learning

Arguments CONTRE (PM perspective) :
- Un système à 1 équipe performante > 4 équipes à edge inconnu
- Chaque agent ajouté = surface de bug supplémentaire (prouvé : bugs dans Trader 2, 3, 4 pendant la silent failure)
- Le learning de toutes les équipes est basé sur des données EXPIRED = zéro signal utile pour toutes

**Recommandation PM** : après Phase 2, si une seule équipe valide son edge, désactiver les autres temporairement et concentrer le développement sur la gagnante. La complexité multi-équipe n'a de valeur que si chaque équipe individuelle performe.

---

## Coût d'opportunité — estimation

Sur la base de 28 sessions d'amélioration documentées dans CLAUDE.md, en estimant **2-4h par session** (les audits complexes comme v4.0/v4.1 étant à 8-12h) :

- **Temps total estimé** : 100-200h de développement depuis le début du projet
- **État actuel** : 100% EXPIRED, 0 edge mesuré, 4 équipes dont l'état réel est inconnu
- **Coût d'opportunité** : chaque heure additionnelle sur de nouvelles features avant de valider l'edge = investissement potentiellement nul

Ce n'est pas un jugement négatif sur la qualité du travail technique (qui est réelle). C'est un signal PM : **le prochain investissement de temps doit répondre à "quel edge ?" avant de répondre à "quelle feature ?"**

---

## Limites de cet audit

- Pas d'accès aux objectifs personnels de Thomas (apprentissage technique vs rentabilité financière) — les priorités ci-dessus supposent que l'objectif est un edge de trading réel. Si l'objectif principal est l'apprentissage du ML/architecture multi-agents, la priorisation change (moins urgent de fixer EXPIRED, plus important de documenter les patterns appris).
- Pas d'accès aux journal entries avec MAE/MFE réels (diagnostic basé sur l'analyse @data-analyst)
- Pas d'accès aux résultats des Équipes 2-3-4 (données potentiellement disponibles en DB mais non consultées dans cet audit)
- L'estimation de temps Thomas est approximative (28 commits documentés ≠ effort réel — certains commits peuvent représenter plusieurs sessions)
- Le score PM-readiness (4/10) reflète l'absence de North Star Metric et de Definition of Done, non la qualité technique du projet qui est supérieure à ce score

---

## Agents spécialisés recommandés

Pas de recommandation d'agent spécialisé pour cette phase. Les 19 agents de base couvrent les besoins. Le vrai besoin n'est pas plus d'agents — c'est moins de complexité et plus de focus sur la validation de l'edge.

---

**Handoff → @orchestrator**

Fichiers produits :
- `/home/user/Finance/docs/audit/product-manager.md`

Décisions prises :
- North Star Metric proposé : Weekly TP_HIT ratio ≥ 45%, CRITICAL < 30%
- Roadmap 3 phases : Stop Bleeding → Validate Edge → Double Down ou Kill
- Phase 3 est une décision binaire GO/NO-GO basée sur des critères chiffrés
- MUST (semaine 1-2) : 8 items P0/P1, 0 nouvelle feature
- WON'T : Team 5, nouvelles sources data, frontend redesign, nouveaux indicateurs

Points d'attention :
- 100% EXPIRED est un arrêt d'urgence produit — aucune roadmap feature n'a de sens tant que ce signal persiste
- Les endpoints destructifs sans auth sont un risque de sécurité actif, pas hypothétique
- La TWELVE_DATA_API_KEY est en clair dans git committé — action immédiate de Thomas requise
- Team 4 activée 2026-03-16 : zéro trade visible → diagnostic requis avant de la considérer comme une équipe active dans les KPIs
- Le score PM-readiness remontera à 7/10 dès que North Star Metric + alerte externe + premiers TP/SL post-calibration seront en place
