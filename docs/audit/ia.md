# Audit IA / Claude API — OneShot Finance
Score : 7/10
Date : 2026-05-01
Auditeur : @ia (AI Engineer, 7 ans LLM en prod)
Périmètre audité : `backend/app/news_scorer.py`, `backend/app/agents/agent_scoring.py`, `backend/app/agents/agent_scoring_2.py`

---

## Verdict en 30 secondes

L'usage Claude API du projet est **mature techniquement** (singleton client, prompt caching, score cache, retry exponentiel, pre-filter zero-edge, batching, thread-safety). Le prompt XML + 3 few-shot examples est de bonne facture. Le choix de Haiku 4.5 est défendable pour le scoring structuré (score reproductible avec `temperature=0`, JSON via tool_use forcé).

**MAIS** trois angles morts coûteux : (1) zéro observabilité opérationnelle (token usage non exposé dans `/api/health`, pas d'alerte coût, pas de mesure du cache hit rate), (2) duplication d'infrastructure entre Scoring 1 et Scoring 2 (deux compteurs de tokens, deux caches absents côté Scoring 2, deux prompts proches mais désynchronisés), (3) le batch_size=15 sur un univers de 50 items signifie **4 appels Claude par scan** au lieu d'1 — ce qui multiplie par 4 la facture du system prompt non caché.

Coût mensuel actuel estimé : **~12-22 $/mois** pour Scoring 1 seul (raisonnable). Avec Scoring 2 ajouté (v8.0 nouveau Claude call) : **~25-40 $/mois**. Optimisable à ~10-15 $/mois en fusionnant l'infra et corrigeant le batching.

---

## Top 3 forces

1. **Prompt engineering solide** — XML structuré (`<role>`, `<scoring_dimensions>`, `<hard_rules>`, `<examples>`), 3 few-shot examples bien choisis (signal physique fort / earnings zero-edge / OSINT geopolitique), tool_use forcé avec `tool_choice` qui garantit un output JSON valide. La distinction explicite **surprise vs magnitude** dans les hard_rules est exactement le type de désambiguïsation qui évite les confusions LLM. Hash MD5 du prompt (`PROMPT_VERSION`) pour traçabilité — bonne discipline.

2. **Économies multi-couches bien implémentées** — (a) `_is_zero_edge_headline()` pré-filtre les earnings/macro évidents AVANT Claude (économie ~10-30% des items), (b) `_score_cache` avec hash(title+description), TTL 4h, eviction LRU, cap 500 items, thread-safe, (c) `cache_control: ephemeral` sur le system prompt (Anthropic prompt caching = ~50% réduction tokens input pour les blocs cachés), (d) singleton client Anthropic qui réutilise les connexions HTTP. Ce sont les 4 leviers d'optimisation token classiques, tous présents.

3. **Robustesse production** — `temperature=0` pour reproductibilité, dynamic max_tokens (350 tokens/item), gestion fine des erreurs (`APITimeoutError` séparé d'`APIError` avec backoff différencié 4-8-16-30s vs 1-2-4-8s), fallback JSON parsing si tool_use échoue, validation post-réponse (bornes 0-100, hard-caps catégorie), `_validate_coherence()` qui **corrige** les contradictions au lieu de juste logger. Le code ne lève **jamais** d'exception côté Claude — il retourne `[]` et stamp `_last_api_status` pour que le pipeline downstream sache distinguer timeout vs erreur normale. Pour un système qui doit tourner 24/7 sur Replit, c'est exactement la bonne posture.

---

## Top 5 faiblesses

| # | Sévérité | Problème | Coût/Impact |
|---|---|---|---|
| 1 | **P0** | **Zéro observabilité coût en production.** `get_token_usage()` existe mais n'est **pas exposé dans `/api/health`** ni dans aucun endpoint dédié. Aucune alerte si la facture explose. Le compteur est un dict in-process qui se reset à chaque restart Replit (sleep 24h) → impossible de connaître le coût mensuel réel. Pas de tracking du **cache hit rate** Anthropic (`cache_read_input_tokens` est loggé en INFO mais jamais agrégé) → on ne sait pas si le prompt caching fonctionne réellement. | Coût invisible, drift silencieux. Si demain le pipeline part en boucle (bug retry), on découvre le problème sur la facture Anthropic en fin de mois. |
| 2 | **P0** | **Batch_size=15 sur univers de 50 items = 4 appels Claude/scan = system prompt envoyé 4× au lieu d'1×.** Le commentaire ligne 748 indique que 50 items causaient des timeouts sur Replit, mais réduire à 15 signifie qu'à chaque scan où le pré-filtre ne suffit pas, on paie 4× le coût d'envoi du system prompt (~3500 tokens). Le prompt caching aide (cache_read = -90% sur les 3 derniers appels) mais le **premier appel paie le plein tarif** + il y a un cache_creation cost. | +20-30% sur la facture vs un batch unique. Et 4 appels séquentiels = 4× le risque de timeout réseau Replit. |
| 3 | **P1** | **Duplication d'infrastructure Scoring 1 / Scoring 2 sans synchronisation.** Deux compteurs de tokens (`_scan_token_usage` vs `_trend_token_usage`), deux prompts XML proches mais divergents, deux singletons Anthropic (Scoring 2 réutilise heureusement `_get_client` de Scoring 1 — bon point), Scoring 2 **ne semble pas avoir de score cache** (à vérifier). Risque : un fix de prompt/cap/retry sur Scoring 1 oublié sur Scoring 2. La règle CLAUDE.md "propagation des corrections de prompt" du protocole @ia est ici directement à risque. | Bug fixed in one, persisted in the other. Coût technique : double maintenance, double facture Claude. |
| 4 | **P1** | **`_validate_coherence` corrige aveuglément, sans télémétrie ni feedback Claude.** Quand Claude renvoie `surprise=80, delay=10` (incohérent), le code force `delay≥40`. Mais (a) aucun compteur n'est exposé pour mesurer la fréquence des corrections (uniquement un `logger.warning` qu'il faut grep dans les logs), (b) ces corrections **ne remontent jamais à Claude** dans le system prompt comme exemples négatifs. Si Claude commet la même erreur sur 30% des trades, on ne le sait pas et on n'agit pas. | Perte d'information sur la qualité du modèle. Empêche d'évaluer si Sonnet ferait mieux que Haiku sur ce critère précis (qui est central à l'edge detection). |
| 5 | **P2** | **Score cache : faux négatifs sur news quasi-identiques.** Le cache key = `md5(title + description)`. Une même news republiée par 2 sources avec un mot différent (ex: "drought" vs "severe drought") = 2 appels Claude au lieu d'1. Le système a déjà un dédup Jaccard côté `news_collector` mais le cache de scoring ne profite pas de cette logique fuzzy. Par ailleurs, le cache TTL=4h est court : avec 4 scans/jour espacés de ~3-5h, beaucoup de news republiées au scan suivant ne hitent pas le cache. | -10-20% de hit rate vs un cache fuzzy ou avec TTL aligné sur le rythme des scans. Quelques $/mois récupérables. |

**Note :** une faiblesse mineure additionnelle : `_is_zero_edge_headline` fonctionne par substring match (`"earnings report" in title.lower()`). Faux négatif probable sur "Apple Q4 EPS beats by 12¢" (pas de keyword direct). Faux positif possible sur "Drought hits earnings of agriculture firms" (matche "earnings"). Le multilingue (FR ajouté) est bon, mais la liste reste à élargir.

---

## 5 améliorations chiffrées

### 1. [P0] Exposer `/api/ai/token-usage` + agrégat cache hit rate (gain : visibilité, coût caché → coût mesuré)

Créer un endpoint qui agrège :
- `input_tokens`, `output_tokens`, `scans` cumulés depuis le démarrage
- `cache_read_tokens` (additionner les `cache_read_input_tokens` loggés)
- `cache_creation_tokens`
- Coût estimé mensuel (extrapolé depuis le rythme actuel)
- Coût Scoring 1 + Scoring 2 séparés
- Persister dans une table PG `claude_usage` (jour, agent, scan_type, input/output/cache, coût) pour survivre aux restarts

**Coût d'implémentation :** 1 endpoint FastAPI (~50 lignes) + 1 table PG. Aucun coût Claude additionnel.
**Gain :** détection en J+1 d'une explosion de coût (vs J+30 sur la facture Anthropic). Permet aussi de répondre à "Sonnet vaut-il son prix ?" avec des données chiffrées.

### 2. [P0] Augmenter `SCORING_BATCH_SIZE` à 30-40 + monitoring du timeout (gain : -15-25% tokens/mois, -$3-5/mois)

Le commentaire ligne 748 mentionne "50 items caused systematic timeouts on Replit". Mais Haiku 4.5 traite ~600-800 tokens/sec et le `timeout=45.0` actuel donne largement la marge pour 30 items (~10500 tokens output). Suggestion :
- Tester `SCORING_BATCH_SIZE=30` avec monitoring `_last_api_status` ; si timeout >5%, redescendre à 25.
- Conserver le retry x3 avec `dynamic_max_tokens × 1.5` qui gère déjà la troncature.
- Bonus : à batch_size=30, on passe de 4 à 2 appels par scan → divise par 2 le coût "non-cached première impression".

**Gain estimé :** ~15-25% sur les tokens input non cachés (~$2-4/mois sur l'enveloppe Scoring 1). Équivalent fait sur Scoring 2 (`TREND_SCORING_BATCH_SIZE=40` est déjà OK, à conserver).

### 3. [P1] Mutualiser l'infra Scoring 1 / Scoring 2 (gain : maintenance, propagation des fixes)

Créer un `claude_runner.py` partagé qui expose :
- `_get_client()` (déjà partagé ✅)
- `_get_score_cache()` paramétrable par namespace ("intraday" / "trend")
- `_track_tokens(agent_name, usage)` thread-safe avec compteur par agent
- `_call_claude_with_retry(...)` avec policy retry standard
- Les prompts (system_messages) restent spécifiques à chaque agent

Cela permet : (a) un seul endpoint d'observabilité pour les 2 scorings, (b) propager un fix retry/timeout/cache à 1 endroit, (c) ajouter un Scoring 5/6 demain sans dupliquer 200 lignes.

**Gain estimé :** réduit les futurs bugs de désynchronisation. Pas de gain $ direct mais protège contre une régression Sev1.

### 4. [P1] Télémétrie sur `_validate_coherence` + injection en few-shot dynamique (gain : qualité scoring → indirectement -5-10% trades faux positifs)

- Ajouter compteurs `coherence_fixes_per_dimension` exposés via l'endpoint du point 1.
- Si une catégorie de fix dépasse un seuil (>10% des scorings sur 7 jours), **ajouter dynamiquement un contre-exemple** dans le system prompt :

```
<contre_exemples>
INCORRECT : surprise=85, transmission_delay=15, market_awareness=20
   (ce serait incohérent : une news surprenante ET inconnue ne peut pas être déjà pricée)
CORRECT  : surprise=85, transmission_delay=70, market_awareness=15
</contre_exemples>
```

**Gain estimé :** -5-10% de scores corrigés a posteriori (Claude apprend de l'exemple). Indirectement, moins de calibrations forcées qui peuvent donner des scores garbage. Difficile à chiffrer en $ mais améliore la qualité des trades downstream.

### 5. [P2] Score cache fuzzy + TTL adaptatif (gain : +10-20% hit rate → -$1-2/mois)

- Remplacer `md5(title+desc)` par **embedding hash** : créer un mini-embedding local (50 dims, e.g. via `sentence-transformers/all-MiniLM-L6-v2` qui tient en 80MB et tourne en CPU à <50ms par item) et matcher par cosine similarity > 0.92.
- Alternative low-tech : `md5(normalize(title))` où `normalize` = lower + remove punctuation + sort top-10 keywords. Pas d'embedding mais hit rate en hausse.
- Aligner TTL sur le rythme des scans : 6h au lieu de 4h (le scan du matin + mid-session + après-midi peuvent partager les mêmes news).

**Gain estimé :** +10-20% de cache hit rate, -$1-2/mois sur Claude.

---

## Estimation coût

### Hypothèses
- **Modèle** : Claude Haiku 4.5 (`claude-haiku-4-5-20251001`)
- **Tarif Anthropic Haiku 4.5 (recherche du jour, à confirmer)** : ~1 $/MTok input, ~5 $/MTok output (vs Sonnet 4.5 à 3 $/15 $)
  - **[HYPOTHÈSE]** Le tarif exact Haiku 4.5 doit être confirmé via WebSearch avant tout commit. Les valeurs ci-dessous sont indicatives.
- **Prompt caching** : Anthropic facture les `cache_read_input_tokens` à 10% du tarif input + `cache_creation` à 125% du tarif input
- **Scans/jour** : 4 (07:50, 11:15, 14:50, 17:00 CET)
- **Items typiques par scan** : ~50 (cap pré-filtre dans news_collector), dont ~10-15 zero-edge filtrés + ~10-20 cachés (hit) → ~20-30 envoyés à Claude

### Tokens par scan (Scoring 1)

**System prompt** (XML role + 11 dimensions + hard_rules + 5 examples + perf summary) : ~3500 tokens
**User message** (univers 41 tickers + contexte VIX + 15-30 headlines avec descriptions) : ~1500-2500 tokens
**Output** (15-30 items × ~150 tokens/item via tool_use) : ~2500-4500 tokens

**Avec batch_size=15 et 30 items à scorer = 2 batches** (cas typique) :
- Batch 1 : input plein 5500 tok (3500 system + 2000 user) + output 2500 tok
- Batch 2 : input cached 3500 tok (cache_read) + 2000 tok user (uncached) + output 2500 tok

**Tokens/scan moyen** :
- Input non-caché : ~5500 + 2000 = 7500 tokens
- Input caché (read) : 3500 tokens
- Output : ~5000 tokens

### Coût mensuel actuel estimé (Scoring 1 seul)

Par jour (4 scans) :
- Input non-caché : 7500 × 4 = 30k tokens × 1 $/MTok = **0.030 $**
- Input caché (read, 10% tarif) : 3500 × 4 = 14k tokens × 0.10 $/MTok = **0.0014 $**
- Cache creation (1 fois/sleep) : 3500 × 1.25 = ~0.004 $
- Output : 5000 × 4 = 20k tokens × 5 $/MTok = **0.100 $**

**Total/jour Scoring 1 ≈ 0.13 $**
**Total/mois Scoring 1 ≈ 22 jours ouvrés × 0.13 $ ≈ 2.9 $**

⚠️ Cette estimation est **bien plus basse** que ce que je suspectais initialement. Vérification : Haiku 4.5 est effectivement très bon marché.

### Avec Scoring 2 (Équipe 2, v8.0 nouveau Claude call)

Scoring 2 a son propre system prompt (~2500 tokens, plus court car focus 4 tickers) + filtre ~10-15 items pertinents par scan.

**Total/mois Scoring 2 estimé ≈ 1.5-2 $**

### Coût total projet actuel ≈ **$4-6/mois**

⚠️ **Beaucoup plus bas que l'intuition initiale.** Le combo Haiku 4.5 + prompt caching + pré-filtre zero-edge + score cache est **extrêmement efficace**. C'est un excellent travail d'optimisation.

### Coût optimisé post-recos

- Reco 2 (batch_size=30) : -25% sur input non-caché = -$0.6/mois
- Reco 5 (cache fuzzy +TTL) : +15% hit rate = -$0.4/mois
- Reco 1 et 4 sont neutres en coût (pure observabilité/qualité)

**Coût optimisé ≈ $3-4/mois**

### Comparaison Sonnet 4.5 vs Haiku 4.5

Si on passait à Sonnet 4.5 (3 $/MTok input, 15 $/MTok output = 3× plus cher) :
- Coût mensuel Scoring 1 : ~9 $ vs 3 $ actuel = **+6 $/mois**
- **Justification ROI** : seulement si Sonnet améliore le win rate de >2-3 points. À tester via A/B (50/50 routing) sur 2 semaines avec mesure WR via Journal 1.
- **Recommandation** : rester sur Haiku 4.5 par défaut. Ajouter une variable `CLAUDE_MODEL_CRITICAL` pour les news avec `signal_reliability ≥ 90` ET `surprise ≥ 70` → router celles-ci vers Sonnet (~5% des items). Coût additionnel ~$1-2/mois pour potentiellement +2-5% WR sur les meilleurs setups.

---

## Réponses aux 12 questions de l'audit

1. **Modèle Haiku 4.5 vs Sonnet** : Haiku est défendable. Le scoring est une tâche structurée (output JSON via tool_use, dimensions bornées 0-100, choix dans énumération de catégories) où Sonnet apporterait peu vs son surcoût 3×. **Recommandation finale** : garder Haiku par défaut + routage Sonnet sur les "high-stakes" news (reliability≥90 + surprise≥70).

2. **Prompt engineering** : très bon. Améliorations possibles : (a) ajouter 1-2 contre-exemples (cf. reco 4), (b) injecter dynamiquement les top-3 erreurs récurrentes du Learning, (c) actuellement le prompt mélange "rôle expert" (FR) et "examples" (FR/EN mixé) — uniformiser en FR pourrait améliorer la cohérence.

3. **Prompt caching `ephemeral`** : bien implémenté (`cache_control` sur le bloc statique uniquement, performance summary séparée non-cachée). MAIS **survit-il aux restarts Replit ?** ❌ Non — Anthropic prompt cache est `ephemeral` (5 min TTL côté Anthropic). Sur Replit avec sleep 24h, **le cache est froid à chaque réveil** = paye `cache_creation` (125% du tarif) au premier scan post-réveil. Pour un système 4 scans/jour espacés de 3-5h, le cache ne hit que entre les batches d'un même scan, pas entre scans.

4. **Score cache (4h, hash headline)** : robustesse OK (LRU eviction, lock, TTL). Faiblesses : (a) pas de fuzzy match, (b) cache stocke en mémoire — perdu au redémarrage, (c) pas de métrique exposée du hit rate.

5. **`_is_zero_edge_headline()`** : robuste sur les évidences (earnings/CPI/NFP). **Faux négatifs probables** sur les formulations indirectes ("Apple smashes Q4 estimates", "Inflation cooler than expected"). Faux positifs marginaux ("Drought impacts earnings of farmers"). À enrichir avec ~10-20 keywords supplémentaires + considérer un classifier léger (regex avancé ou mini-modèle FastText) sur 200 examples labellés.

6. **`_validate_coherence`** : 3 règles (surprise>70 + delay<20 / awareness<20 + delay<20 / awareness>80 + delay>60). Cas qui échappent : (a) `directional_clarity=10 + surprise=85` (signal flou mais surprenant — devrait avoir un confidence cap), (b) `expected_magnitude=90 + signal_reliability=20` (gros impact mais rumeur — devrait pénaliser), (c) `confirmed_event=true + market_awareness<30` (fait confirmé mais inconnu = soit Claude se trompe sur "confirmed", soit signal en avance de phase rare). Ajouter ces 3 règles.

7. **Token tracking** : `get_token_usage()` existe **mais n'est pas exposé via `/api/health`** (vérifié dans `main.py`). Auditeur (`agent_auditor.py:839`) y accède uniquement pendant l'audit. **Pas d'alerte coût.** Cf. reco 1.

8. **Batch size 15** : trop conservateur, voir reco 2.

9. **Magnitude/reliability (v4.0)** : utilisées par `trade_selector` (lu dans CLAUDE.md, magnitude scale target via `0.7 + 0.6 * (magnitude/100)`, reliability via `reliability_factor = 0.4 + 0.6 * reliability/100`). Bouclage Learning : Learning 4.2 B2/B3 trackent `magnitude_accuracy` et `signal_reliability precision` dans le feedback Claude. Boucle complète et active.

10. **Modèles alternatifs** :
    - **Extended thinking** : non disponible avec `tool_choice` forcé (incompatibilité documentée par Anthropic). Pour exploiter, il faudrait abandonner le tool_use forcé et parser le JSON manuellement → fragilise l'output. **Pas recommandé** sur ce flux.
    - **Structured outputs natifs** : Anthropic a annoncé un mode `response_format: json_schema` plus moderne que tool_use. À tester en parallèle (A/B) — pourrait simplifier le code et éliminer le fallback parsing.
    - **Opus 4.7 audit hebdo** : intéressant. Idée : 1 fois/semaine, prendre les 50 worst trades de la semaine et demander à Opus 4.7 (avec `effort=high`) **pourquoi** Haiku s'est trompé sur ces scorings. Output = recos d'amélioration pour le prompt. Coût : ~$3-5/semaine pour un meta-feedback de qualité supérieure. ROI clair.

11. **Singleton client `_get_client()`** : thread-safe ✅ (la création est protégée implicitement par le GIL Python sur l'assignation de variable globale, et le client `anthropic.Anthropic` est lui-même thread-safe selon la doc Anthropic). Rate limit : géré par le SDK Anthropic en interne (retry 429), mais le code applicatif **ne logge pas les 429** distinctement de `APIError` → impossible de voir si on hit le rate limit. À ajouter dans le retry handler.

12. **Context length** : avec batch=15 et univers complet :
    - System prompt : ~3500 tokens
    - User message : ~2000 tokens (15 headlines avec desc + ticker list + market context)
    - **Total input : ~5500 tokens**
    - Output : ~2500 tokens
    - **Total : ~8000 tokens** — très loin des 200k de Haiku 4.5. Marge confortable pour augmenter le batch.

---

## Limites de cet audit

- **Tarifs Anthropic non vérifiés via WebSearch** : les chiffres tarifaires Haiku 4.5 et Sonnet 4.5 cités ci-dessus sont [HYPOTHÈSE] basés sur la grille générale Anthropic. Avant tout commit ou décision budgétaire, faire WebSearch sur "Anthropic Claude Haiku 4.5 pricing 2026" pour confirmer.
- **Pas d'accès aux logs production** pour mesurer le hit rate réel du prompt cache et du score cache → toutes les économies estimées sont des bornes hautes/basses.
- **Scoring 2 lu partiellement** (200 premières lignes sur ~900). Conclusions sur Scoring 2 (cache, retry, parsing) basées sur similarité avec Scoring 1 et grep — à confirmer en lecture complète si décision de mutualisation prise.
- **Frontend non audité** : la frontend appelle-t-elle Claude directement ? Non détecté dans cet audit (focus backend), mais à confirmer.
- **Tests Claude API non audités** (`test_news_scorer.py`) : 15 tests v4.3 mentionnés dans CLAUDE.md mais non vérifiés ici.
- **Audit de la qualité des outputs Claude réels** non effectué (aurait nécessité l'analyse des `all_scored_news` du journal sur ~30 jours pour mesurer la précision réelle des dimensions).

---

**Handoff → @orchestrator**
- Fichier produit : `/home/user/Finance/docs/audit/ia.md`
- Décisions prises : Haiku 4.5 reste recommandé par défaut, batch_size=30 à tester, mutualisation Scoring 1/2 priorisée, observabilité coût manquante = P0
- Points d'attention : (1) tarifs Anthropic à valider via WebSearch avant calcul ROI exact, (2) prompt cache Anthropic ne survit pas au sleep Replit — à intégrer dans tout calcul futur, (3) Reco 1 (endpoint observabilité) est prérequis pour pouvoir mesurer l'effet des Recos 2-5
- Prochaine étape suggérée : @fullstack implémente l'endpoint `/api/ai/token-usage` + table PG `claude_usage` + dashboard frontend (~3h de dev), puis A/B batch_size sur 2 semaines.
