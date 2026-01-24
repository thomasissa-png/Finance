# 📈 Application de Trading Multi-Marchés

Application de trading **multi-actifs** avec développement **fonction par fonction**.

## 🎯 Objectif Global

Sélectionner chaque matin **2-3 valeurs** pour du **day trading**:
- **Stratégie**: Acheter à l'ouverture, vendre rapidement
- **Gain cible**: 1%
- **Marchés**: Actions, Forex, Métaux Précieux, Commodities, Indices

---

## 📊 FONCTION 1: Backtesting & Analyse Multi-Marchés

### Description

Cette première fonction vous permet de:
1. ✅ Récupérer les données des **20 derniers jours** de trading
2. ✅ Calculer **tous les indicateurs techniques** clés
3. ✅ **Interroger les données** sur **60+ actifs**
4. ✅ Obtenir les **TOP 3 opportunités** par catégorie

### 🌍 Marchés Couverts

#### 🏢 Actions Euronext Paris (15 valeurs)
Actions très liquides du CAC40:
- LVMH (MC.PA)
- L'Oréal (OR.PA)
- Air Liquide (AI.PA)
- Sanofi (SAN.PA)
- TotalEnergies (TTE.PA)
- BNP Paribas (BNP.PA)
- Schneider Electric (SU.PA)
- Safran (SAF.PA)
- Hermès (RMS.PA)
- AXA (CS.PA)
- Capgemini (CAP.PA)
- Veolia (VIE.PA)
- Vinci (DG.PA)
- Bouygues (EN.PA)
- Pernod Ricard (RI.PA)

#### 🥇 Métaux Précieux (4 actifs)
- Or (GC=F)
- Argent (SI=F)
- Platine (PL=F)
- Palladium (PA=F)

#### 💱 Forex - Devises (9 paires majeures)
- EUR/USD (EURUSD=X)
- GBP/USD (GBPUSD=X)
- USD/JPY (USDJPY=X)
- AUD/USD (AUDUSD=X)
- USD/CHF (USDCHF=X)
- USD/CAD (USDCAD=X)
- NZD/USD (NZDUSD=X)
- EUR/GBP (EURGBP=X)
- EUR/JPY (EURJPY=X)

#### 🛢️ Commodities - Matières Premières (9 actifs)
**Énergie:**
- Pétrole WTI (CL=F)
- Pétrole Brent (BZ=F)
- Gaz Naturel (NG=F)

**Agriculture:**
- Maïs (ZC=F)
- Blé (ZW=F)
- Soja (ZS=F)
- Café (KC=F)
- Sucre (SB=F)

**Métaux Industriels:**
- Cuivre (HG=F)

#### 📊 Indices Boursiers Mondiaux (12 indices)
**France:**
- CAC 40 (^FCHI)

**États-Unis:**
- S&P 500 (^GSPC)
- Dow Jones (^DJI)
- Nasdaq (^IXIC)
- Russell 2000 (^RUT)

**Europe:**
- DAX - Allemagne (^GDAXI)
- FTSE 100 - UK (^FTSE)
- IBEX 35 - Espagne (^IBEX)
- FTSE MIB - Italie (^FTSEMIB)

**Asie-Pacifique:**
- Nikkei 225 - Japon (^N225)
- Hang Seng - Hong Kong (^HSI)
- ASX 200 - Australie (^AXJO)

**TOTAL: 60+ actifs surveillés** 🎯

---

### 🎯 Sélection Automatique des Meilleures Opportunités

L'application analyse tous les actifs et vous donne les TOP 3 basés sur:
- **Volatilité optimale** (0.5-4.0%) - essentielle pour atteindre +1%
- **Volume supérieur à la moyenne** - garantit la liquidité
- **Potentiel de gain de 1%** - backtest sur les 10 derniers jours
- **Score de trading** (0-100) - agrège tous les critères

#### Filtrage par Catégorie
- TOP 3 TOUS marchés confondus
- TOP 3 Actions Euronext
- TOP 3 Forex
- TOP 3 Métaux Précieux
- TOP 3 Commodities
- TOP 3 Indices

---

### 📊 Indicateurs Techniques Calculés

Pour **chaque actif**, l'application calcule:

**Tendance:**
- SMA 5, 10, 20 jours
- EMA 5, 10, 20 jours
- RSI (14 jours)
- MACD + Signal + Histogramme

**Volatilité:**
- ATR (Average True Range)
- Bandes de Bollinger
- Volatilité intrajournalière (5 jours)

**Volume:**
- Volume moyen 20 jours
- Ratio volume actuel / moyenne

**Momentum:**
- Momentum 10 jours
- ROC (Rate of Change)
- Stochastic K & D

**Support/Résistance:**
- Support 5 jours
- Résistance 5 jours
- Distance prix/moyennes mobiles

---

### 🔍 Questions que Vous Pouvez Poser

L'application peut répondre à:
1. **Quels sont les actifs les plus volatiles?** - Maximiser le potentiel de gain
2. **Quels actifs ont le meilleur momentum?** - Tendance haussière forte
3. **Quels actifs ont un volume anormal?** - Possible breakout
4. **Quels actifs sont proches de leur support?** - Point d'entrée optimal
5. **Quels actifs ont eu un gap à l'ouverture?** - Opportunité de comblement

---

## 🚀 Installation

### Prérequis
- Python 3.8+
- pip

### Installation des dépendances

```bash
pip install -r requirements.txt
```

Dépendances:
- `yfinance` - Données boursières multi-marchés
- `pandas` - Manipulation de données
- `numpy` - Calculs numériques
- `pyyaml` - Configuration
- `colorama` - Affichage coloré
- `tabulate` - Tableaux formatés

---

## 💻 Utilisation

### Lancer l'application

```bash
python main.py
```

### Menu Principal

```
OPPORTUNITÉS:
  1. 🎯 TOP 3 opportunités (TOUS marchés)
  2. 🏢 TOP 3 Actions Euronext
  3. 💱 TOP 3 Forex
  4. 🥇 TOP 3 Métaux Précieux
  5. 🛢️  TOP 3 Commodities
  6. 📊 TOP 3 Indices

ANALYSE:
  10. 📈 Analyser un actif spécifique
  11. 🔥 Les plus volatiles
  12. 🚀 Meilleur momentum
  13. 📦 Volume anormal
  14. 📉 Proches du support
  15. ⚡ Gaps à l'ouverture

AUTRES:
  20. 📋 Liste de tous les actifs
  21. 🔄 Recharger les données
  0. ❌ Quitter
```

### Exemple d'Utilisation

**Scénario 1: Trouver les meilleures opportunités tous marchés**

1. Lancez: `python main.py`
2. Choisissez **1** - "TOP 3 opportunités (TOUS marchés)"
3. L'application affiche les 3 meilleurs actifs **tous marchés confondus**

**Scénario 2: Concentrer sur le Forex**

1. Choisissez **3** - "TOP 3 Forex"
2. L'application affiche les 3 meilleures paires de devises

**Scénario 3: Analyser un actif spécifique**

1. Choisissez **10** - "Analyser un actif spécifique"
2. Entrez: `EURUSD=X` ou `GC=F` ou `MC.PA`
3. Analyse complète avec tous les indicateurs

**Exemple de sortie:**

```
======================================================================
#1 - EURUSD=X | EUR/USD
💱 Forex | Score: 88/100 ⭐
======================================================================

  💰 Prix actuel: 1.0875
  📊 Tendance: Haussière
  📈 Volatilité 5j: 1.82%
  📦 Ratio Volume: 2.15x
  🎯 RSI: 48.7

  🎲 POTENTIEL GAIN 1%:
     Taux de réussite (10 derniers jours): 90.0%
     Jours où 1% atteint: 9/10
     Gain intraday moyen: 1.45%

  📊 INDICATEURS CLÉS:
     SMA 5: 1.0852 | Distance: +0.21%
     MACD: 0.0012 | Signal: 0.0008
     Stochastic K: 62.3
```

---

## ⚙️ Configuration

Le fichier `config.yaml` permet de personnaliser:

### Actifs surveillés
```yaml
stocks_euronext: [MC.PA, OR.PA, ...]
metals: [GC=F, SI=F, ...]
forex: [EURUSD=X, GBPUSD=X, ...]
commodities: [CL=F, BZ=F, ...]
indices: [^FCHI, ^GSPC, ...]
```

### Critères de sélection
```yaml
selection_criteria:
  rsi_range: [35, 65]
  volatility_min: 0.5
  volatility_max: 4.0
  volume_ratio_min: 0.8
```

---

## 📁 Structure du Projet

```
Finance/
├── main.py              # Application CLI avec menu multi-marchés
├── analyzer.py          # Moteur d'analyse multi-actifs
├── data_fetcher.py      # Récupération données (yfinance)
├── indicators.py        # Calcul des indicateurs techniques
├── config.yaml          # Configuration multi-marchés
├── requirements.txt     # Dépendances
└── README.md            # Documentation
```

---

## 🎯 Avantages du Multi-Marchés

### Pourquoi Forex, Métaux et Commodities ?

**💱 Forex:**
- Très volatile (parfait pour +1%)
- Très liquide (spread faible)
- Trading 24/5
- Réagit aux actualités rapidement

**🥇 Métaux Précieux:**
- Valeur refuge
- Bonne volatilité
- Corrélation avec USD
- Opportunités en période d'incertitude

**🛢️ Commodities:**
- Excellente volatilité (pétrole, gaz)
- Diversification du portefeuille
- Opportunités saisonnières (agriculture)

**📊 Indices:**
- Vision globale du marché
- Moins volatils mais plus stables
- Opportunités sur tendances macro

---

## 🔮 Fonctions Futures

- **Fonction 2**: (À définir par l'utilisateur)
- **Fonction 3**: (À définir par l'utilisateur)

L'application se développe **fonction par fonction** selon vos besoins.

---

## ⚠️ Avertissements

> **IMPORTANT**: Cet outil est une **aide à la décision** uniquement.
>
> - ❌ Ne constitue PAS un conseil financier
> - ❌ Ne garantit PAS de profits
> - ❌ Les performances passées ne garantissent pas les résultats futurs
> - ⚠️ Le trading Forex et Commodities est **très risqué**
> - ⚠️ Utilisez un **stop loss** strict
>
> **Tradez uniquement ce que vous pouvez vous permettre de perdre.**

---

## 📊 Statistiques

- **60+ actifs** surveillés simultanément
- **5 catégories** d'actifs (Actions, Forex, Métaux, Commodities, Indices)
- **20+ indicateurs** techniques calculés par actif
- **20 jours** d'historique analysés
- **Backtesting** sur potentiel de gain 1%

---

**Bon trading multi-marchés ! 🌍📈💰**
