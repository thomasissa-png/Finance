# Orchestrator — Référence complète

Ce fichier est le complément de `orchestrator.md`. Il contient les templates, modes spéciaux, métriques et cartes des prompts. Il est consulté à la demande (via Read), pas chargé systématiquement.

---

## Carte de référence — Prompts de la bibliothèque par phase

Phase 0 (Stratégie) :
- @creative-strategy → "Positionnement & plateforme de marque" + "Construire la messaging matrix"
- @product-manager → "Vision produit & roadmap" + "Specs fonctionnelles détaillées" + "Définir le scope V1" + "Stratégie de pricing complète"
- @data-analyst → "KPIs & tracking plan"
- @legal → "Audit juridique & conformité"
- @elon → "Vision long terme et moat" (optionnel, si pertinent)

Phase 1 (Conception) :
- @ux → "Parcours utilisateur & wireframes" + "Onboarding utilisateur gamifié"
- @design → "Définir la direction artistique" → "Design system complet" + "Design responsive complet" + "Design système de notifications" (si pertinent)
- @copywriter → "Brand voice & identité verbale" + "Landing page complète"

Phase 2 (Développement) :
- @infrastructure → "Configurer CI/CD & déploiement"
- @fullstack → "Setup initial du projet" + "Développer une feature" (par feature) + "Intégrer le paiement Stripe" (si pertinent) + "Design de base de données" + "API design" + "Authentification & autorisation"
- @ia → "Ajouter une feature IA" + "Pipeline RAG" + "Fine-tuning et prompt engineering" (si pertinent)
- @ux → revue post-implémentation (comparer wireframes vs code)
- @qa → "Audit qualité & tests complets"

Phase 3 (Visibilité) :
- @seo → "Stratégie SEO technique & éditoriale"
- @geo → "Visibilité sur les IA génératives (GEO)"
- @copywriter → "Stratégie de contenu & calendrier éditorial"

Phase 4 (Acquisition) :
- @growth → "Stratégie d'acquisition complète" + "Plan de lancement"
- @social → "Stratégie social media"
- @copywriter → "Emails onboarding & conversion"
- @growth + @ia → "Automatisation marketing complète"

Phase 5 (Audit & Validation) :
- @reviewer → "Revue croisée GO/NO-GO"
- @qa → "Audit qualité & tests complets"
- @qa + @fullstack + @ux + @design → "Revue finale page par page (dernier kilomètre)" — OBLIGATOIRE
- Checklist jour de lancement (GO/NO-GO final)
- @infrastructure → "Monitoring post-launch"

**Prompts d'audit ciblé (utilisables à tout moment, pas seulement en Phase 5)** :
- @qa + @fullstack + @ux → "Audit réel d'une page ou feature (crash test)" — audit rapide en 7 phases : visuel, UX, résultats réels, workflow, contenu, crash test inputs, garde-fous, sécurité, persona. À utiliser dès qu'une page/feature est codée.
- @qa + @fullstack + @ux → "Audit exhaustif d'une page ou feature (stress test production)" — 32 scénarios avancés : cross-device, interruption/reprise, idempotence, race conditions, timezone, etc. Prérequis : audit rapide d'abord. À utiliser avant mise en production.

**RÈGLE : quand l'utilisateur dit "audite [page/feature]", "vérifie [page]", "teste [feature]", ou équivalent → utiliser le prompt "Audit réel (crash test)" de la bibliothèque. NE PAS improviser un audit code basique. Si l'utilisateur demande un audit approfondi ou pré-production → utiliser "Audit exhaustif (stress test)".**

**Prompts conditionnels par type de projet** :
- SaaS : "Intégrer le paiement Stripe" + "Authentification & autorisation" + "Design système de notifications" + "Stratégie de pricing complète" + "Configurer une motion PLG"
- Site vitrine : "Landing page complète" prioritaire + "SEO + GEO combinés"
- Marketplace : double persona (vendeur + acheteur) dans chaque agent
- Tout projet avec UI : "Spécifier les interactions et états des composants" + "Gestion des erreurs & feedback utilisateur" + "Performance budget & optimisation"
- Tout projet EU/FR : "Gestion cookies & consent (RGPD)"
- Tout projet en production : "Analyse automatisée des feedbacks utilisateurs" + "Monitoring UX"
- Tout projet existant / refonte : "Auditer le funnel existant"
- Phase 5 systématique : "Checklist jour de lancement"

---

## Mode autopilot détaillé

### Règles du mode autopilot

1. **Toujours sauvegarder** `docs/orchestration-plan.md` après chaque phase
2. **Toujours scorer** chaque livrable dans le tableau Performance
3. **BLOQUER automatiquement** si :
   - Un agent a >= 1 gate BLOQUANT en FAIL → relancer (max 3 itérations)
   - Contradiction détectée entre livrables → arbitrer selon priorité (persona > objectif > budget)
   - Champ critique manquant pour un agent aval → demander à l'utilisateur
   - Détection de drift (persona/KPI divergent de project-context.md) → BLOQUER
   - Livrable vide ou quasi-vide (< 20 lignes) → relancer avec plus de contexte
   - Pas de checkpoint périodique : bloquer uniquement sur anomalie
4. **Checkpoint obligatoire** après Phase 0 (fondations stratégiques)
5. **Fin de run** : invoquer @reviewer automatiquement
6. **Enrichir** `docs/lessons-learned.md` (format v2 obligatoire, 11 colonnes)
7. **PROPAGATION CHECK** avant clôture : Grep `non-propagé`, appliquer, vérifier
8. **Copier préférences fondateur** dans `docs/founder-preferences.md`
9. **Pousser learnings sur main** pour URLs cross-projets

### Quand passer en mode standard

L'autopilot est le défaut. Mode standard uniquement si :
- Tout premier projet de l'utilisateur sur le framework
- L'utilisateur le demande explicitement

### Profils de rigueur

**Profil V1-Production** (défaut) :
- Toutes les 32 gates G1-G32 (BLOQUANT + REQUIS)
- Gates testeur-persona GP1-GP10 et testeur-client GC1-GC10
- Checkpoint validation specs obligatoire Phase 1 → Phase 2
- Matrice de traçabilité US→tests + Screenshots CI vs baselines (G24)
- Pipeline pre-deploy complet (G26)

**Profil Exploration** (prototype, validation d'idée) :
- Gates BLOQUANT uniquement
- Pas de gates GP/GC
- Tests E2E happy path uniquement
- Template user story allégé

Un projet Exploration → V1-Production DOIT passer par un audit complet @reviewer.

---

## Templates

### Template orchestration-plan.md

```markdown
# Plan d'orchestration — [Nom du projet]

## Demande utilisateur
[Reformulation clarifiée de la demande]

## Mode détecté
[Nouveau projet / Projet existant] — [Justification]

## Profil utilisateur
- Niveau technique : [Non-technique / Technique / Expert]
- Ton de communication : [Métier / Technique / Mixte]

## Complexité estimée
[Légère / Moyenne / Lourde] — [Nb agents] agents, [Nb phases] phases

## Plan par phase

### Phase 0 — Fondations
- Agents : @creative-strategy, @product-manager, @data-analyst, @legal
- Parallélisation : @legal en parallèle
- Statut : [En attente / En cours / Terminé]
- Livrables attendus / reçus : [listes]
- Verdict vérification : [OK / RELANCE / ÉCHEC par agent]

## Feedbacks remontants
| # | Sévérité | Agent source | Agent cible | Problème | Statut |

## Décisions d'arbitrage
| # | Sujet | Décision | Justification | Agents impactés |
```

### Template project-synthesis.md

```markdown
# Synthèse projet — [Nom du projet] — [Date]

## Vue d'ensemble
[3-5 lignes : produit, état global, prochaine étape]

## Livrables produits
| Phase | Agent | Livrable | Chemin | Statut |

## Décisions structurantes
[Choix qui engagent l'aval]

## Contradictions résolues
| Contradiction | Arbitrage | Justification |

## Points ouverts
[À trancher, valider ou produire]

## Métriques d'orchestration
[Bloc métriques]

## Scores qualité
- Gates : X/X BLOQUANT PASS, Y/Y REQUIS PASS
- Score persona : X/10 (seuil : 9/10)
- Score B2B : X/10 ou N/A

## Recommandations pour la suite
[Prochains agents, prochaine phase, itérations]
```

---

## Cycle d'itération qualité @reviewer (fin de run)

1. Lancer @reviewer → exécute les 32 gates binaires (G1-G32) sur chaque livrable
2. Si >= 1 gate BLOQUANT FAIL → rapport + correction requise
3. Relancer l'agent responsable avec le rapport
4. Agent corrige → @reviewer re-vérifie uniquement les gates FAIL
5. Répéter (max 3 itérations)
6. Si après 3 itérations des BLOQUANT restent FAIL → escalader à l'utilisateur
7. Score dérivé inscrit dans "Performance des agents"
8. Gates persona et B2B vérifiées (pré-requis binaires)
9. **Condition GO finale** : 100% BLOQUANT PASS + 100% REQUIS PASS + persona PASS + B2B PASS

En autopilot : exécuté automatiquement, pas de synthèse tant que les conditions ne sont pas remplies.

---

## Estimation de coût par phase

- Task producteur Opus : ~$3-5
- Task producteur Sonnet : ~$0.75-1.50
- Task consultation : ~$1-2

Format en début de run : `Estimation : [N] Opus × ~$4 + [N] Sonnet × ~$1 = ~$XX-YY`

---

## Circuit breaker — Agents fragiles

En Étape 1, lire `docs/lessons-learned.md` section "Agents fragiles". Pour chaque agent fragile :
- Prompt enrichi avec contexte de l'échec passé
- Tentative unique au lieu de 2
- Fallback direct si même type d'échec

```markdown
## Agents fragiles
| Agent | Type d'échec | Fréquence | Dernière occurrence | Contournement |
```

---

## Métriques live dans orchestration-plan.md

```markdown
## Métriques live
| Phase | Agents | Parallèles | Relances | P0 | Coût estimé | Statut |
```

---

## Compression de contexte entre phases

Après chaque phase, résumer en 5-10 bullet points :
```
### Résumé Phase [X]
- Persona : [nom] — validé
- Positionnement : [1 phrase]
- KPI North Star : [métrique]
- Décisions clés : [2-3 bullets]
- Gates FAIL corrigées : [liste ou "aucune"]
```

---

## Mode hotfix (production)

1. Skip phases stratégiques
2. Binôme @fullstack + @qa uniquement
3. Gate G26 obligatoire avant deploy
4. Scope minimal — corriger UNIQUEMENT le bug
5. Documenter dans project-context.md
6. Learning automatique si trou dans les tests/gates

Déclencheur : "hotfix", "bug en prod", "urgence production"

---

## Gestion budget et complexité

| Complexité | Nb agents | Nb phases | Risque timeout |
|---|---|---|---|
| Légère | 1-3 | 1 | Faible |
| Moyenne | 4-8 | 2-3 | Moyen |
| Lourde | 10-17 | 4-5 | Élevé |

---

## Protocole de reprise après interruption

1. Détecter : lire `docs/orchestration-plan.md` avec phases incomplètes
2. Inventorier : Glob docs/ + src/
3. Comparer plan vs réalité
4. Ne JAMAIS relancer un agent dont le livrable existe (sauf < 20 lignes)
5. Signaler à l'utilisateur : "Reprise détectée. Phase [X] terminée. Je reprends à @[agent]."
6. Reprendre à la phase non complétée

---

## Métriques d'orchestration obligatoires

```markdown
## Métriques d'orchestration
- Agents invoqués : X/19
- Task lancés : X (parallèle / séquentiels)
- Échecs / Relances / Feedbacks (P0/P1/P2)
- Phases complétées : X/5
- Drift détecté : OUI/NON
- Livrables produits : X fichiers
- Verdict gates : X PASS / Y FAIL
```

### Seuils de succès

| Métrique | Acceptable | Critique |
|---|---|---|
| Échecs Task (après 2 tentatives) | <= 1 | >= 3 |
| Gates BLOQUANT | 100% PASS | >= 1 FAIL |
| Drift | 0 | >= 2 |
| P0 non résolus fin de run | 0 | >= 1 |
