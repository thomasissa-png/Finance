# Gates binaires — Référence qualité

Source de vérité pour les 32 gates G1-G32 + gates testeur-persona GP/GC.
Consommateurs : @reviewer (exécute), @orchestrator (vérifie BLOQUANT après chaque phase).

## Système de contrôle qualité

1. **Vérification rapide par l'orchestrateur** (après chaque phase) : gates BLOQUANT uniquement. Si 1+ FAIL → relance corrective immédiate.
2. **Audit complet par @reviewer** (fin de run) : 32 gates via Grep/Read/comparaison. Boucle max 3 passes.

## Classification

- **BLOQUANT** : 1 FAIL = NO-GO immédiat, relance obligatoire
- **REQUIS** : 1 FAIL = GO conditionnel (corriger dans la session)
- **CONDITIONNEL** : s'applique uniquement si le livrable amont existe

## Les 32 gates (PASS/FAIL)

### COMPLÉTUDE

| # | Gate | Classe | Vérification |
|---|---|---|---|
| G1 | Toutes les sections du template présentes (0 TODO) | BLOQUANT | Grep `[TODO]`, `[À REMPLIR]`, sections < 2 lignes |
| G2 | Livrables amont référencés existent | REQUIS | Glob les chemins cités |
| G3 | Bloc Handoff structuré présent | BLOQUANT | Grep `Handoff` |
| G4 | Chaque donnée chiffrée a une source | REQUIS | Grep nombres, vérifier source |

### COHÉRENCE

| # | Gate | Classe | Vérification |
|---|---|---|---|
| G5 | Persona identique à project-context.md | BLOQUANT | Grep nom persona, adresser frustrations/objections |
| G6 | KPI North Star identique | BLOQUANT | Grep KPI |
| G7 | 0 contradiction avec livrables amont | BLOQUANT | Read livrables amont, comparer décisions clés |
| G8 | Ton cohérent avec brand-voice.md | CONDITIONNEL | Grep registre tu/vous |

### ACTIONNABILITÉ

| # | Gate | Classe | Vérification |
|---|---|---|---|
| G9 | Chaque recommandation a un owner + action + cible | REQUIS | Grep `→ @` |
| G10 | 0 langage vague ("envisager", "pourrait") | REQUIS | Grep mots vagues |
| G11 | Critères de validation binaires | REQUIS | Read section validation |
| G12 | Implémentable sans question | BLOQUANT | Verbe d'action + objet + inputs/outputs + critère de done |

### MESSAGES

| # | Gate | Classe | Vérification |
|---|---|---|---|
| G13 | 0 donnée inventée | BLOQUANT | Grep chiffres sans source |
| G14 | Livrables absents signalés | REQUIS | Grep chemins docs/ → Glob existence |
| G15 | 0 placeholder résiduel | BLOQUANT | Grep `[À REMPLIR`, `[À COMPLÉTER`, `[À compléter`, `[PLACEHOLDER`, `[TODO`, `[NOM`, `[EXEMPLE`, `[XX`, `[VOTRE`, `[INSÉRER`, `[REMPLACER` |

### SPÉCIFICITÉ

| # | Gate | Classe | Vérification |
|---|---|---|---|
| G16 | Nom projet >= 3 fois ET persona >= 2 fois ET >= 2 livrables amont référencés | REQUIS | Grep count |
| G17 | Pas copiable pour un concurrent (test d'inversion) | BLOQUANT | Si > 50% applicable sans modif → FAIL |
| G18 | >= 1 exemple concret spécifique au projet | REQUIS | Vérification sectorielle |

### QUALITÉ MÉTIER (conditionnelles selon type)

| # | Gate | Classe | Vérification |
|---|---|---|---|
| G19 | 5 états UI par écran (défaut, loading, vide, erreur, succès) | BLOQUANT | Grep par écran |
| G20 | WCAG 2.2 AA (contrastes + focus-visible + touch 44px + reduced-motion) | BLOQUANT | Vérifier combinaisons couleurs |
| G21 | 0 valeur hardcodée (tout via tokens) | REQUIS | Grep hex en dur |
| G22 | Registre tu/vous uniforme | REQUIS | Grep cohérence |
| G23 | Chaque KPI a formule + seuil d'alerte | REQUIS | Grep formule/seuil |

### PIPELINE & CONFORMITÉ (si src/ existe)

| # | Gate | Classe | Vérification |
|---|---|---|---|
| G24 | Screenshots CI vs baselines (< 0.5% diff, 3 devices) | BLOQUANT | Playwright iPhone 13 / iPad / Desktop |
| G25 | 100% user stories ont un test | REQUIS | Tableau US-XX → test dans TESTING.md |
| G26 | Pipeline pre-deploy PASS (tsc + lint + tests) | BLOQUANT | 0 erreur TypeScript + ESLint + tests |

### DESIGN & COMPOSITION (si frontend)

| # | Gate | Classe | Vérification |
|---|---|---|---|
| G27 | Pattern de layout explicite par section | REQUIS | Vérifier page-compositions.md |
| G28 | >= 1 image spécifiée par page client-facing | REQUIS | Vérifier specs images |
| G29 | Architecture tokens 3 tiers (primitive → semantic → component) | REQUIS | Grep références directes tokens primitifs |
| G30 | 6 états par composant interactif (default, hover, active, focus-visible, disabled, loading) | REQUIS | Grep 6 états |
| G31 | Favicon Coverage (12 items 2026, voir `docs/checklists/favicon-checklist.md`) | REQUIS | Bash script §3 du checklist : 12/12 fichiers présents + 7 balises HTML head |
| G32 | Typographie FR (m², …, œ, guillemets « » + apostrophes typo ’ + espaces insécables avant : ; ! ? %) | CONDITIONNEL | Bash : `grep -E "m2 \|\.\.\.\| oe\| :\| !\| ?\| %\|\"[^\"]+\"\|'[A-Za-zé]"` ne doit rien retourner sur livrables FR. Cap : 0 occurrence ASCII résiduelle dans docs/ et src/ pour livrables client-facing |

## Gates testeur-persona (si agents testeurs créés)

**Limitation importante** : un LLM qui simule un persona reste structurellement trop indulgent. Les gates GP/GC sont un pré-filtre, pas une validation finale. Validation humaine obligatoire sur les 3 parcours critiques avant deploy.

### GP — Testeur-persona

| GP1 | Compréhension immédiate | BLOQUANT | GP2 | Valeur perçue | BLOQUANT |
| GP3 | Crédibilité | BLOQUANT | GP4 | Parcours fluide | BLOQUANT |
| GP5 | Pricing acceptable | REQUIS | GP6 | Recommandation | REQUIS |
| GP7 | Conviction | BLOQUANT | GP8 | Look & feel | REQUIS |
| GP9 | Outputs utiles | BLOQUANT | GP10 | Fidélisation | REQUIS |

### GC — Testeur-client

| GC1 | Professionnalisme | BLOQUANT | GC2 | Pertinence | BLOQUANT |
| GC3 | Confiance | BLOQUANT | GC4 | Action | BLOQUANT |
| GC5 | Complétude | REQUIS | GC6 | Différenciation | REQUIS |
| GC7 | Ton et registre | REQUIS | GC8 | Zéro erreur factuelle | BLOQUANT |
| GC9 | Copy convaincant | REQUIS | GC10 | Design/mise en page | REQUIS |

**Conditions** : GP/GC si agents testeurs créés (Phase 0b), sinon N/A. Marketplace = 1 testeur par persona. B2C direct = GC N/A.

## Verdicts

- **GO** : 100% BLOQUANT PASS + 100% REQUIS PASS
- **GO CONDITIONNEL** : 100% BLOQUANT PASS + >= 1 REQUIS FAIL (corriger dans la session)
- **NO-GO** : >= 1 BLOQUANT FAIL → relance immédiate

**Score dérivé** (tracking) : `(gates PASS / applicables) × 10`

**Condition GO finale** : 100% BLOQUANT PASS + 100% REQUIS PASS + persona >= 9/10 + B2B >= 9/10 (si applicable)
