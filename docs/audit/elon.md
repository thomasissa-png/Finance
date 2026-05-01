# Audit Stratégique First Principles — OneShot Finance
Verdict : PIVOT
Date : 2026-05-01
Auditeur : @elon

---

## Le diagnostic brutal en 3 phrases

1. **Le système n'a JAMAIS prouvé d'edge — 100% EXPIRED sur 30+ trades sur 14 jours actifs, 0 TP_HIT historique visible, 0 backtest jamais lancé. Tout le reste (21 agents, 4 équipes, 22k lignes, 6 dimensions de learning, prompt caching XML) est de la machinery qui tourne sur du bruit.**
2. **L'architecture est une cathédrale construite sans avoir vérifié que les fondations tiennent — ajouter Team 4 pour combiner les signaux de Teams 1+2+3 alors qu'aucune des trois n'a démontré individuellement un signal positif est de la sur-ingénierie circulaire qui ralentit l'itération sur le seul vrai problème : valider l'edge.**
3. **Sur le fond stratégique : tu te bats contre Citadel/Two Sigma/Renaissance avec des données publiques (NOAA/EIA/USDA/GNews) et un LLM grand public — sauf miracle, tu ne peux pas gagner cette guerre, mais tu peux peut-être gagner une niche très spécifique si tu arrêtes de la diluer dans 41 tickers et 21 agents.**

---

## Tests first principles

### 1. Edge réel ? **NON-PROUVÉ**

**Données empiriques** :
- 30+ trades sur 14 jours actifs (2026-03-27 → 2026-04-10)
- TP_HIT rate : 0%
- SL_HIT rate : 0%
- EXPIRED rate : 100%
- Sharpe ratio : non calculé
- Max drawdown : non calculé
- Backtests historiques lancés : 0

**Verdict** : un système qui tourne 14 jours et produit ZÉRO résultat exploitable n'a pas d'edge — il a une calibration cassée OU pas de signal du tout. Impossible de distinguer les deux sans backtest.

**Le piège mental ici** : "100% EXPIRED" peut être lu comme "le SL n'est pas touché donc on perd pas d'argent" — c'est faux. EXPIRED veut dire "le marché ne bouge ni assez ni dans la bonne direction pour valider la thèse". C'est l'équivalent de Falcon 9 qui décolle, monte 10m, et redescend en douceur. Pas un crash, mais aucune mission accomplie.

**Comparaison Tesla 2008** : quand on a failli perdre Tesla, on n'a pas pivoté sur la thèse EV. Pourquoi ? Parce que la physique disait que les EVs étaient inévitables — densité énergétique batterie + coût décroissant. Ici, quelle est ta "physique" qui dit que ton edge est inévitable ? Je ne la vois pas. Je vois 30 trades EXPIRED.

### 2. Complexité justifiée ? **NON**

**Inventaire** :
- 21 agents Python
- 22 738 lignes de code
- 4 équipes de trading
- 1331 lignes de CLAUDE.md
- 6 dimensions de learning adaptatif
- 11 stratégies techniques (Team 3)
- 41 tickers
- 17 sources de données structurées
- ~28 commits depuis le début

**Test de complexité utile** : la complexité d'un système est justifiée si chaque composant a démontré sa valeur incrémentale ET si la complexité est une condition nécessaire à la performance.

- Team 4 (meta-ensemble) : combine des signaux qui n'ont pas démontré individuellement leur edge → **valeur démontrée = 0**
- Team 3 (techniques 11 stratégies) : aucun TP_HIT visible → **valeur démontrée = 0**
- Team 2 (trend commodities) : aucune perf documentée → **valeur démontrée = inconnue**
- Team 1 (intraday news) : 100% EXPIRED → **valeur démontrée = 0**
- Learning 6 dimensions : recalcule des ajustements sur 30 trades EXPIRED = du bruit pur → **valeur démontrée = négative (peut introduire des biais)**

**L'algorithme SpaceX appliqué ici** :
1. Question requirements : a-t-on vraiment besoin de 4 équipes ? **Non — pas avant d'avoir prouvé qu'1 équipe fonctionne.**
2. Delete : Team 2, 3, 4 + leurs 9 agents + ~12 000 lignes de code peuvent être supprimés sans perte d'edge prouvé.
3. Simplify : passer de 41 tickers à 1-3 tickers où l'edge est testable.
4. Accelerate : avec moins de code, tu peux itérer 5x plus vite sur le seul vrai problème (valider l'edge).
5. Automate : tu as déjà sur-automatisé avant de prouver le step 1.

**Le problème classique** : tu as appliqué l'algorithme à l'envers. Tu as automatisé (étape 5) avant d'avoir simplifié (étape 3). C'est la même erreur que Tesla a faite sur la production du Model 3 en 2017 — sur-automatisation prématurée. Solution : remettre des humains (ici, du code simple) sur la chaîne pour comprendre où le signal se perd, puis ré-automatiser ensuite.

### 3. Time-to-validation acceptable ? **NON**

**Calcul du TTV (Time-To-edge-Validation)** :
- Projet vivant depuis ~2 mois minimum (versions agents v7-v8 nécessitent du temps de maturation)
- ~28 commits, 22 738 lignes de code écrites
- 14 jours actifs en production
- Backtests lancés : 0
- Trades validés (TP_HIT ou SL_HIT) : 0

**Diagnostic** : ce n'est pas une question de "il faut juste plus de trades" — le système a eu **14 jours réels et 0% de validation**. Avec 4 scans/jour × 14 jours = 56 opportunités scan, et 30+ trades générés. Si tu n'as toujours pas un seul TP_HIT après 30 trades, ce n'est pas un problème de sample size. C'est un problème structurel.

**La question qui dérange** : si tu lances un backtest aujourd'hui sur 90 jours d'historique (price_archive existe, run_news_replay_backtest() existe), et que le résultat est aussi mauvais que la prod, est-ce que tu acceptes l'échec et pivotes ? Ou est-ce que tu vas trouver une raison pour continuer ?

**Référence Falcon 1** : SpaceX a eu 3 échecs consécutifs avant le 4e succès. Mais à chaque échec, on avait des données EXPLOITABLES (télémétrie précise, cause racine identifiée). Toi, tes 30 EXPIRED ne te disent même pas pourquoi ça échoue. C'est une télémétrie sans signal. Tu pilotes à l'aveugle.

### 4. Bataille perdue d'avance vs hedge funds ? **VRAI (mais nuancé)**

**Réalité brutale** :
- Tes sources : NOAA, EIA, USDA, GNews, Open-Meteo — toutes **publiques et gratuites**
- Citadel, Two Sigma, Jane Street, Renaissance ont :
  - Les mêmes données (et bien plus, avec des feeds privés)
  - Des modèles 100x à 10000x plus sophistiqués
  - Une vitesse d'exécution en microsecondes
  - Des PhD en stat/ML qui tunent en continu
  - Du capital pour absorber des SL_HIT répétés

**Comment tu pourrais quand même gagner** :
- **Niches trop petites pour eux** : ils ne tradent pas $5k de notional. Ils ont des contraintes de capacité. Les futures liquides où ils dominent sont peut-être tradables sur des micro-niches d'exécution qu'ils ignorent.
- **Latence comportementale humaine** : sur les news physiques (gel cacao, sécheresse blé), même les algos institutionnels ont parfois besoin d'un humain dans la boucle pour valider une thèse non-quantitative. Délai = 30 min à 6h.
- **LLM as cheap analyst** : Claude Haiku à $0.0008/headline est une bonne idée — synthèse rapide d'info qualitative qu'un quant fund n'a pas en interne au même prix.

**MAIS** : ces edges sont fragiles, étroits, et nécessitent une discipline obsessionnelle de focus. Tu ne peux pas avoir cet edge sur 41 tickers en parallèle avec 4 équipes. C'est physiquement impossible.

### 5. LLM = vraie différenciation ? **OUI mais mal exploitée**

**Pourquoi le LLM est un edge potentiel** :
- Coût marginal de scoring : $0.0008/headline
- Capable de comprendre langage qualitatif (rapport USDA, brief NOAA portugais)
- Peut intégrer du contexte multi-source en un appel
- Hedge funds utilisent NLP propriétaire mais coût de développement >$10M

**Pourquoi tu ne l'exploites pas correctement** :
- 11 dimensions de scoring : trop. Aucun humain ne peut valider que les 11 sont calibrées correctement. Plus tu ajoutes de dimensions, plus tu as de surfaces de contact où Claude peut halluciner ou se contredire.
- Convergence direction : ajoutée mais jamais validée empiriquement.
- Score caching 4h : OK techniquement mais inutile si la direction du signal est fausse.
- Tu utilises Claude pour scorer mais pas pour **prédire le mouvement de prix attendu** (en pips, en %, dans X heures). C'est ça qui a de la valeur, pas un score abstrait sur 100.

**Refonte proposée du LLM use-case** :
```
Au lieu de : "Donne-moi un score 0-100 + 10 dimensions"
Demande : "Voici la news. Voici l'historique des 5 dernières news similaires sur ce ticker.
          Pour chacune : prix avant, prix +1h, +4h, +24h.
          Prédit pour CETTE news : direction, magnitude (en %) à +1h/+4h/+24h, et confidence."
```
Ça transforme Claude en **prévisionniste calibré** plutôt qu'en **scoreur abstrait**. Et ça permet de **mesurer la précision réelle** (predicted_magnitude vs actual_magnitude) — ce qui devient ton vrai edge mesurable.

---

## Critères d'abandon (KILL THE PROJECT)

Critères chiffrés, non-négociables, à graver dans le code :

| # | Critère | Seuil | Action si déclenché |
|---|---------|-------|---------------------|
| 1 | TP_HIT rate sur 90 jours actifs avec >100 trades | < 25% | NO-GO total — pas d'edge mesurable |
| 2 | Backtest replay sur 6 mois historiques | Sharpe < 0.5 | NO-GO — la formule ne tient pas même rétrospectivement |
| 3 | Coût Anthropic cumulé sur 12 mois | > 3x P&L net | NO-GO économique — l'infrastructure mange l'edge |
| 4 | Temps Thomas investi (heures/semaine) | > 10h/semaine pendant 3 mois sans TP_HIT | NO-GO opportunité — mieux d'apprendre autre chose |
| 5 | Silent failures non détectées | > 1 par trimestre | NO-GO opérationnel — système non robuste |
| 6 | Régression : 30+ trades consécutifs EXPIRED après tout pivot | atteint | NO-GO — la calibration est structurellement inadaptée |

**Important** : ces critères doivent être audités automatiquement chaque dimanche soir. Si déclenchés → notification Thomas obligatoire avec recommandation d'arrêt.

---

## 3 scénarios de pivot

### Scénario A — MVP 1 ticker (RECOMMANDÉ)

**Stratégie** : sniper, pas mitrailleuse.

- **1 seul ticker** : NG=F (gaz naturel) — meilleure volatilité, EIA hebdo = source unique haute qualité, news physiques claires (storage, weather, supply)
- **1 seule source structurée** : EIA Weekly Storage Report (mercredi 16:30 CET)
- **1 seul agent trader** (kill 20 autres)
- **Holding** : 24-48h (pas intraday — laisse le temps au signal de transmettre)
- **TP/SL** : calibration EMPIRIQUE basée sur ATR 5j × 1.5 et 1.0 (pas de formule convexe magique)
- **Code** : <2000 lignes (vs 22 738)
- **Pas de Team 2/3/4, pas de scoring multi-dimension, pas de LLM XML**
- **LLM use-case** : prédiction de magnitude (predicted_pct_4h vs actual)

**Timeline** :
- Semaine 1-2 : extraire l'essentiel du code existant, supprimer 90% du reste
- Semaine 3-4 : backtest sur 2 ans d'historique EIA + NG=F
- Semaine 5-8 : paper trading
- Semaine 9-12 : live trading petit notional

**Critère de succès** : TP_HIT rate >40% sur 30 trades en 12 semaines.

Si OK → ajouter ticker #2 (CL=F, brent ou WTI). Si pas OK → Scénario C.

### Scénario B — Pivot quantitatif (sans LLM)

**Stratégie** : abandonner la thèse "edge LLM news" et passer à du quant classique.

- 5 commodities liquides (NG, CL, GC, ZW, ZC)
- Stratégie : mean reversion intraday sur ATR breakout (Bollinger 2σ)
- Aucun LLM, aucune news
- Pure technique, indicateurs simples
- Backtest sur 5 ans avant tout déploiement live

**Avantages** :
- Validation rapide via backtest historique abondant
- Pas de dépendance à Anthropic
- Edge potentiel mesuré objectivement
- Code <1000 lignes

**Inconvénients** :
- Tu te bats sur le terrain où les hedge funds sont les meilleurs
- Aucune différenciation
- Edge probablement <50bps par trade

**Verdict perso** : moins intéressant que A. Tu copies un truc déjà fait par 100 000 retail traders. Mais c'est un fallback si A échoue.

### Scénario C — Abandon + redéploiement effort

**Si après Scénario A (12 semaines), aucun edge prouvé** :

- Accepter l'échec empirique sans honte. Le marché est efficace sur 99% des données publiques. C'est un résultat scientifique, pas un échec personnel.
- Mettre le capital sur DBA (Invesco DB Agriculture ETF) ou un mix commodity ETF passif
- Réinvestir le temps dans :
  - Un projet IA différent où l'edge est plus défendable (B2B SaaS, automation interne d'entreprises)
  - Apprendre Rust ou un domaine où tu ne fais pas concurrence à Renaissance Technologies

**Question Bezos regret minimization** : dans 10 ans, est-ce que tu regretteras d'avoir arrêté un projet qui produit 0% de TP_HIT après 6 mois ? Non. Tu regretteras d'avoir continué à investir dedans par sunk cost fallacy.

---

## Verdict final

### **PIVOT — Scénario A (MVP 1 ticker), timeline 12 semaines**

**Action immédiate (cette semaine)** :

1. **Geler tout développement de features** sur Teams 2, 3, 4. Pas de bug fix, pas d'optimisation. Mort cérébrale assumée.
2. **Lancer le backtest v5.1 historique** que personne n'a jamais lancé. C'est gratuit, ça existe déjà. Si le backtest dit "edge = 0", tu sauves 12 semaines.
3. **Analyser les 30+ EXPIRED via MAE/MFE** (recommandation @data-analyst confirmée — c'est le diagnostic critique avant tout pivot).
4. **Définir un North Star Metric chiffré** : `Weekly TP_HIT ratio > 40% sur 4 semaines glissantes`. Si <20% à S+4 → kill.

**Actions semaines 2-4** :
- Branch dédiée `mvp-ng-only` qui supprime ~80% du code
- Garder : market_data.py, agent_news.py (filtré EIA only), trade_selector.py simplifié, journal.py
- Supprimer : tous les agent_*_2.py, agent_*_3.py, agent_*_4.py, learning multi-dimensions, scoring 2/3/4, frontend pages des Teams 2/3/4
- Backtester sur 24 mois d'historique NG=F + EIA reports

**Actions semaines 5-12** :
- Si backtest OK → live trading petit notional ($1k-2k) sur NG=F uniquement
- Tracking : TP_HIT rate, Sharpe, max drawdown, predicted_magnitude vs actual
- Review mensuelle stricte avec critères de kill

**Si à S+12, TP_HIT rate <40% ou Sharpe <0.5** : passer Scénario C (abandon).

**Si à S+12, TP_HIT rate >40% et Sharpe >0.8** : ajouter ticker #2, ré-introduire complexité de manière incrémentale **et seulement après validation**.

---

## La question Thomas doit se poser ce soir

Pas "comment fixer le 100% EXPIRED ?" — mauvaise question, elle assume que le projet doit continuer dans sa forme actuelle.

La bonne question : **"Si j'avais commencé ce projet ce matin, est-ce que je construirais 21 agents et 4 équipes avant d'avoir un seul TP_HIT ? Si non, pourquoi je continue ?"**

Le sunk cost fallacy est l'ennemi #1 du fondateur. Les 22k lignes de code écrites n'ont aucune valeur intrinsèque — seulement la valeur de ce qu'elles produisent. Et elles produisent 0.

---

## Recommandations par priorité

| # | Action | Type | Impact | Effort | Agent concerné |
|---|--------|------|--------|--------|----------------|
| 1 | Lancer backtest v5.1 sur 90j historiques | Bloquant | CRITIQUE | 1j | @fullstack + @data-analyst |
| 2 | Analyser MAE/MFE des 30+ EXPIRED | Bloquant | CRITIQUE | 1j | @data-analyst |
| 3 | Définir North Star Metric chiffré (ex: Weekly TP_HIT >40%) | Bloquant | HAUT | 0.5j | Thomas |
| 4 | Geler dev Teams 2/3/4 (mort cérébrale) | Stratégique | HAUT | 0j (décision) | Thomas |
| 5 | Branche `mvp-ng-only`, supprimer 80% du code | Pivot | CRITIQUE | 1 sem | @fullstack |
| 6 | Implémenter critères kill chiffrés en code | Sécurité | HAUT | 1j | @fullstack |
| 7 | Webhook Slack/Discord sur silent failure >24h | Sécurité | HAUT | 0.5j | @infrastructure |
| 8 | Refonte LLM use-case en "prévisionniste de magnitude" | Vision | MOYEN | 2 sem (si pivot OK) | @ia |

---

## Vision 10x

Si je reprenais ce projet de zéro avec l'objectif de battre le marché de manière honnête et durable :

- **1 niche, 1 source, 1 hypothèse claire** — pas 41 tickers et 17 sources
- **Backtest avant code production** — pas l'inverse
- **LLM comme prévisionniste calibré** — pas comme scoreur abstrait
- **Tracking de précision** — predicted_pct vs actual_pct, c'est ça l'edge
- **Critères de kill automatiques** — pas de décision émotionnelle
- **Itération hebdomadaire chiffrée** — pas mensuelle vague
- **<3000 lignes de code total** — pas 22k

Ce que je n'aurais PAS fait :
- Construire un framework multi-équipes avant d'avoir prouvé 1 équipe
- Ajouter du learning adaptatif sans données validées
- Caler 11 dimensions de scoring que personne ne peut valider
- Trader 41 tickers en parallèle sans focus

---

## Hypothèses à valider

- [HYPOTHÈSE : Thomas a investi >100h dans ce projet] — à confirmer pour calibrer le coût d'opportunité
- [HYPOTHÈSE : aucun TP_HIT historique pré-2026-03-27] — à confirmer via DB complète
- [HYPOTHÈSE : le projet est purement personnel sans pression externe] — confirmé par le brief
- [HYPOTHÈSE : l'objectif est PnL réel, pas apprentissage technique] — à clarifier avec Thomas

Si l'objectif est apprentissage technique et non PnL → mes recommandations changent. Garder un side-project complexe pour apprendre Python/agents/LLM est légitime. Mais alors il faut le **cadrer comme tel** et arrêter de prétendre que c'est un système de trading.

---

## Limites de l'audit

- Pas accès à l'historique complet PG des trades (peut-être TP_HIT plus anciens)
- Pas accès aux logs Replit / coûts réels facturés
- Pas accès au temps investi par Thomas (pour calculer ROI personnel)
- Pas accès au capital alloué (le notional change la donne — $1k vs $50k)
- Pas accès aux résultats des Teams 2, 3, 4 individuellement (silent failure 21j masque la perf récente)
- Pas de vérification du backtest (n'a jamais été lancé, donc rien à auditer)
- Recommandations basées sur les rapports @qa, @fullstack, @ia, @infrastructure, @data-analyst — leur précision détermine la précision de cet audit

---

**Handoff → Thomas (réponse directe)**

- Fichier produit : `/home/user/Finance/docs/audit/elon.md`
- Avis donné : **PIVOT recommandé — Scénario A (MVP 1 ticker NG=F, 12 semaines)**
- Diagnostic : 100% EXPIRED sur 30+ trades = pas d'edge prouvé. La complexité (21 agents, 4 équipes, 22k lignes) est une cathédrale sans fondations.
- Action immédiate : lancer le backtest v5.1 que personne n'a jamais lancé. C'est gratuit, ça existe, ça peut sauver 3 mois de dev inutile.
- Rappel : ces recommandations sont des AVIS à forte conviction, pas des directives. Thomas décide. Mais si je devais appuyer sur le bouton à sa place, je ferais Scénario A dès lundi.
