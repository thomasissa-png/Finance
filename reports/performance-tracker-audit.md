# Audit Performance Tracker - Agent Quantitatif

**Date**: 2026-02-09
**Version**: branche `claude/fix-analysis-scheduler-FCanR`
**Couverture**: Lifecycle complet signal -> resultat, 85+ requetes SQL, 12 tables

---

## 1. Architecture du Tracking des Signaux

### 1.1 Lifecycle Complet d'un Trade

```
Signal Emission (trades.py:210)
    |
    v
[INSERT trades_recommandes] -- 55 colonnes renseignees
    |
    |--- Monitoring toutes les 30min (verifier_resultats_trades)
    |    |-- Fetch 5min OHLC via TwelveData
    |    |-- Track prix_max/min_atteint, pnl_max/min
    |    |-- Detecte TP1/TP2/STOP via analyser_cloture_intraday()
    |
    |--- Reevaluation 7x/jour (reevaluer_trades_intraday)
    |    |-- statut_intraday: EN_COURS -> TP50_ATTEINT
    |    |-- Trailing stop ajuste si conviction >= 4
    |    |-- Action: HOLD / RENFORCER / REDUIRE / SORTIR
    |
    |--- Cloture forcee 17:15 EU / 21:45 US (cloturer_trades_jour)
    |    |-- Verifie d'abord si TP/STOP touche dans l'historique intraday
    |    |-- Sinon: WIN_FORCE / LOSS_FORCE / BREAKEVEN
    |
    |--- [NEW] Cloture orphelins 07:30 (cloturer_trades_orphelins)
         |-- Trades date < today avec resultat IS NULL -> EXPIRED
    |
    v
[UPDATE trades_recommandes SET resultat, pnl_pct, prix_sortie]
```

### 1.2 Resultats Possibles (7 valeurs)

| Resultat | Declencheur | PnL | Inclus dans metriques? |
|----------|-------------|-----|----------------------|
| **TP1** | Take Profit 1 touche | > 0 | OUI (win) |
| **TP2** | Take Profit 2 touche | > 0 | OUI (win) |
| **STOP** | Stop Loss touche | < 0 | OUI (loss) |
| **WIN_FORCE** | Cloture forcee PnL > +0.05% | > 0 | OUI (win) |
| **LOSS_FORCE** | Cloture forcee PnL < -0.05% | < 0 | OUI (loss) |
| **BREAKEVEN** | Cloture forcee PnL ~ 0 | ~ 0 | NON (neutre) |
| **EXPIRED** | Trade orphelin non cloture | 0 | NON (exclu) |

---

## 2. Biais de Survivant - Analyse et Corrections

### 2.1 Biais Detectes

| # | Biais | Severite | Code | Impact |
|---|-------|----------|------|--------|
| 1 | **Trades orphelins jamais clotures** | CRITIQUE | `verifier_resultats_trades()` filtre `date = today` (ligne 319) | Trades des jours precedents avec `resultat IS NULL` ne sont JAMAIS re-verifies |
| 2 | **Cloture forcee ignore le passe** | CRITIQUE | `cloturer_trades_jour()` filtre `date = today` (ligne 729) | Meme probleme: seuls les trades du jour sont clotures |
| 3 | **Expiration non implementee** | MAJEUR | `validite_minutes` stocke mais jamais verifie | Champ present dans les INSERT mais aucun code ne verifie l'expiration |
| 4 | **Comptage total biaise** | MOYEN | `get_performances()` inclut trades NULL dans `en_cours` | Gonfle le total, dilue les metriques |

### 2.2 Corrections Appliquees

| # | Correction | Fichier | Detail |
|---|-----------|---------|--------|
| 1 | **`cloturer_trades_orphelins()`** | trades.py:942-989 | Nouvelle fonction: cloture tous les trades `date < today AND resultat IS NULL` comme EXPIRED |
| 2 | **Appel au demarrage** | scheduler.py:319 | Execute avant le rattrapage journal, au lancement |
| 3 | **Schedule quotidien 07:30** | scheduler.py:310 | Nettoyage automatique chaque matin |
| 4 | **Comptage EXPIRED** | trades.py:901,916,933 | `get_performances()` retourne `expires` et soustrait du `en_cours` |

### 2.3 Scenario Avant/Apres

**Avant** (si un trade de lundi n'est pas cloture):
- Lundi: signal AAPL LONG, pas de TP/STOP touche
- Lundi 22h: cloture forcee ne le voit pas (filtre date = lundi OK)
- Mardi 08h: `verifier_resultats_trades()` ne le voit pas (filtre date = mardi)
- Le trade reste `resultat IS NULL` indefiniment -> **biais de survivant**

**Apres**:
- Mardi 07h30: `cloturer_trades_orphelins()` detecte le trade (date < today)
- Le trade est marque `EXPIRED`, `pnl_pct = 0`, `prix_sortie = prix_entree`
- Il est exclu des metriques de precision mais visible dans les stats

---

## 3. KPIs Existants vs Manquants

### 3.1 KPIs Deja Implementes

| KPI | Source | Endpoint |
|-----|--------|----------|
| Win rate | metrics.py | `/api/metrics` |
| Profit factor | metrics.py | `/api/metrics` |
| Win rate par grade (A/B/C/D) | metrics.py | `/api/metrics` |
| Calibration conviction (1-5) | metrics.py | `/api/metrics` |
| PnL par regime marche | metrics.py | `/api/metrics` |
| Equity curve + drawdown | api_analytics.py | `/api/equity-curve` |
| Expectancy par trade | api_analytics.py | `/api/equity-curve` |
| Recovery factor | api_analytics.py | `/api/equity-curve` |
| Max consecutive wins/losses | api_analytics.py | `/api/equity-curve` |
| Stats par heure/categorie/type | api_analytics.py | `/api/stats-detaillees` |
| A/B testing chi-squared | ab_testing.py | `/api/journal/ab-tests` |

### 3.2 KPIs Manquants (Ajoutes)

| KPI | Formule | Script |
|-----|---------|--------|
| **Sharpe Ratio** | mean(PnL) / std(PnL) * sqrt(N_annuel) | `kpi_report.py` |
| **Sortino Ratio** | mean(PnL) / downside_std * sqrt(N_annuel) | `kpi_report.py` |
| **Calmar Ratio** | total_return / max_drawdown | `kpi_report.py` |
| **Kelly Criterion** | (p*b - q) / b | `kpi_report.py` |
| **Kelly/2** | Kelly / 2 (conservateur) | `kpi_report.py` |
| **Ulcer Index** | sqrt(mean(drawdown%^2)) | `kpi_report.py` |
| **Avg Win/Loss Ratio** | avg_win / avg_loss | `kpi_report.py` |
| **Completude tracking** | avec_resultat / emis * 100 | `kpi_report.py` |

### 3.3 Nouvel Endpoint

```
GET /api/kpis-avances?jours=90
```

Retourne:
```json
{
  "success": true,
  "kpis": {
    "basiques": { "win_rate", "profit_factor", "expectancy", "avg_win", "avg_loss" },
    "risque": { "sharpe_ratio", "sortino_ratio", "calmar_ratio", "max_drawdown_pct", "ulcer_index" },
    "sizing": { "kelly_criterion", "kelly_half" },
    "streaks": { "max_consecutive_wins", "max_consecutive_losses", "current_streak" },
    "completude": { "signaux_emis", "avec_resultat", "sans_resultat", "expires", "taux_completion" },
    "par_resultat": { "TP1": N, "TP2": N, "STOP": N, ... },
    "par_categorie": { ... },
    "par_direction": { ... }
  }
}
```

---

## 4. Analyse de la Qualite du Tracking

### 4.1 Points Forts

| Aspect | Detail | Note |
|--------|--------|------|
| **Granularite** | 5-minute OHLC pour detection TP/SL | Excellente precision |
| **Intra-candle** | Si TP ET SL touchees meme bougie, utilise direction Open->Close | Evite faux resultats |
| **Multi-source** | 76 colonnes d'indicateurs enregistrees a la reco | Permet analyse post-hoc |
| **Reevaluation** | 7x/jour avec recommandations (HOLD/SORTIR) | Tracking actif |
| **Cloture forcee** | Verifie d'abord l'historique intraday avant de forcer | Evite faux WIN_FORCE |
| **Distinction resultats** | TP1/TP2 vs WIN_FORCE (naturel vs force) | Statistiques transparentes |

### 4.2 Points Faibles Corriges

| Aspect | Probleme | Correction |
|--------|----------|------------|
| **Orphelins** | Trades non clotures des jours precedents | `cloturer_trades_orphelins()` |
| **Metriques risque** | Pas de Sharpe/Sortino/Calmar | `kpi_report.py` + `/api/kpis-avances` |
| **Sizing** | Pas de Kelly criterion | `kpi_report.py` |
| **Completude** | Pas de mesure du taux de completion | `completude` dans kpi_report |

### 4.3 Limitations Residuelles

| # | Limitation | Impact | Recommandation |
|---|-----------|--------|----------------|
| 1 | PnL base sur % et non sur capital reel | Ne reflete pas la taille des positions | Ajouter `montant_investi` au schema |
| 2 | Pas de slippage modelise | Surestimation possible des gains | Integrer un modele de slippage (0.05-0.1%) |
| 3 | `validite_minutes` non enforce | Trades potentiellement invalides restes ouverts | Implenter verification expiration dans le monitoring |
| 4 | Pas de benchmark (S&P500) | Impossible de calculer l'alpha | Ajouter tracking d'un benchmark parallele |
| 5 | Pas de correlation inter-trades | Risque de concentration non mesure | Calculer correlation PnL entre trades simultanes |

---

## 5. Verification: Signaux Emis vs Resultats

### 5.1 Chemin de Verification

Pour chaque trade, on peut verifier:

```sql
-- Trades sans resultat (orphelins potentiels)
SELECT id, date, actif, symbole, timestamp_reco
FROM trades_recommandes
WHERE resultat IS NULL
ORDER BY date DESC;

-- Taux de completion par semaine
SELECT strftime('%Y-W%W', date) as semaine,
       COUNT(*) as emis,
       SUM(CASE WHEN resultat IS NOT NULL THEN 1 ELSE 0 END) as conclus,
       ROUND(SUM(CASE WHEN resultat IS NOT NULL THEN 1.0 ELSE 0 END) / COUNT(*) * 100, 1) as pct
FROM trades_recommandes
GROUP BY semaine ORDER BY semaine DESC;

-- Distribution des resultats
SELECT resultat, COUNT(*) as nb,
       ROUND(AVG(pnl_pct), 2) as pnl_moyen,
       SUM(pnl_pct) as pnl_total
FROM trades_recommandes
WHERE resultat IS NOT NULL
GROUP BY resultat;
```

### 5.2 Anti-Survivorship Check

Le script `kpi_report.py` inclut automatiquement:
- `completude.signaux_emis`: tous les trades INSERT
- `completude.avec_resultat`: ceux avec un resultat
- `completude.sans_resultat`: les orphelins restants
- `completude.expires`: les trades auto-expires
- `completude.taux_completion`: % de tracking complet
- `completude.orphelins_ids`: IDs des trades non resolus

**Seuil d'alerte**: si `taux_completion < 95%`, il y a un probleme de tracking.

---

## 6. Corrections Appliquees

### 6.1 Fichiers Modifies

| Fichier | Modification |
|---------|-------------|
| `trading_app/trades.py` | + `cloturer_trades_orphelins()` (50 lignes), + comptage EXPIRED dans `get_performances()` |
| `trading_app/scheduler.py` | + import `cloturer_trades_orphelins`, + appel au demarrage, + schedule 07:30 quotidien |
| `trading_app/scripts/kpi_report.py` | Nouveau script KPI avance (280 lignes) |
| `trading_app/scripts/__init__.py` | Nouveau (package init) |
| `trading_app/routes/api_analytics.py` | + endpoint `/api/kpis-avances` |

### 6.2 Nouveaux KPIs Disponibles

```
Sharpe Ratio         - Rendement ajuste au risque (annualise)
Sortino Ratio        - Comme Sharpe mais uniquement volatilite negative
Calmar Ratio         - Rendement / max drawdown
Kelly Criterion      - Fraction optimale du capital par trade
Kelly/2              - Version conservatrice (recommandee)
Ulcer Index          - Profondeur + duree des drawdowns
Avg Win/Loss Ratio   - Ratio gain moyen / perte moyenne
Taux de Completion   - % des signaux avec resultat enregistre
```

### 6.3 Commandes de Verification

```bash
# Rapport KPI console
python -m trading_app.scripts.kpi_report --jours 90

# Rapport KPI JSON
python -m trading_app.scripts.kpi_report --jours 90 --json

# API endpoint
curl http://localhost:5000/api/kpis-avances?jours=90
```

---

## 7. Resume Executif

### Verdict Global

Le systeme de tracking est **solide** dans son architecture (detection intraday 5min, distinction naturel/force, reevaluation 7x/jour, 76 colonnes d'indicateurs). Le principal probleme etait le **biais de survivant** cause par le filtrage `date = today` qui laissait les trades des jours precedents indefiniment ouverts.

### Corrections Cles

1. **Anti biais**: `cloturer_trades_orphelins()` execute au demarrage + 07:30 quotidien
2. **KPIs pro**: Sharpe, Sortino, Calmar, Kelly, Ulcer Index via script + API
3. **Completude**: Mesure automatique du taux de completion des signaux

### Risques Residuels (Non Critiques)

- PnL en % sans capital reel (pas de sizing)
- Pas de benchmark pour mesurer l'alpha
- Pas de modele de slippage
- `validite_minutes` toujours non enforce (expiration des signaux)
