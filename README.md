# 📈 Application de Trading Euronext Paris

Application de trading pour la **Bourse de Paris** (Euronext) avec développement **fonction par fonction**.

## 🎯 Objectif Global

Sélectionner chaque matin **2-3 valeurs** pour du **day trading**:
- **Achat** à l'ouverture de la bourse de Paris (9h00)
- **Vente** le plus rapidement possible
- **Gain cible**: 1%

---

## 📊 FONCTION 1: Backtesting & Analyse de Données

### Description

Cette première fonction vous permet de:
1. ✅ Récupérer les données des **20 derniers jours** de trading
2. ✅ Calculer **tous les indicateurs techniques** clés
3. ✅ **Interroger les données** pour identifier les meilleures opportunités
4. ✅ Obtenir les **TOP 3 actions** à trader chaque jour

### Fonctionnalités

#### 🎯 Sélection Automatique des Meilleures Opportunités

L'application analyse toutes les actions et vous donne les 3 meilleures basées sur:
- **Volatilité optimale** (0.8-2.5%) - essentielle pour atteindre +1%
- **Volume supérieur à la moyenne** - garantit la liquidité
- **Potentiel de gain de 1%** - backtest sur les 10 derniers jours
- **Score de trading** (0-100) - agrège tous les critères

#### 📊 Indicateurs Techniques Calculés

Pour chaque action, l'application calcule:

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

#### 🔍 Questions que Vous Pouvez Poser

L'application peut répondre à:
1. **Quelles sont les actions les plus volatiles?** - Pour maximiser le potentiel de gain
2. **Quelles actions ont le meilleur momentum?** - Tendance haussière forte
3. **Quelles actions ont un volume anormal?** - Possible breakout
4. **Quelles actions sont proches de leur support?** - Point d'entrée optimal
5. **Quelles actions ont eu un gap à l'ouverture?** - Opportunité de comblement

### Actions Surveillées

15 valeurs liquides du CAC40:
- MC.PA (LVMH)
- OR.PA (L'Oréal)
- AI.PA (Air Liquide)
- SAN.PA (Sanofi)
- TTE.PA (TotalEnergies)
- BNP.PA (BNP Paribas)
- SU.PA (Schneider Electric)
- SAF.PA (Safran)
- RMS.PA (Hermès)
- CS.PA (AXA)
- CAP.PA (Capgemini)
- VIE.PA (Veolia)
- DG.PA (Vinci)
- EN.PA (Bouygues)
- RI.PA (Pernod Ricard)

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
- `yfinance` - Données boursières
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
OPTIONS DISPONIBLES:

1. 🎯 Trouver les meilleures opportunités du jour (TOP 3)
2. 📊 Analyser une action spécifique
3. 🔥 Quelles sont les actions les plus volatiles ?
4. 🚀 Quelles actions ont le meilleur momentum ?
5. 📦 Quelles actions ont un volume anormal ?
6. 📉 Quelles actions sont proches de leur support ?
7. ⚡ Quelles actions ont eu un gap à l'ouverture ?
8. 📋 Liste de toutes les actions surveillées
9. 🔄 Recharger les données
0. ❌ Quitter
```

### Exemple d'Utilisation

**Scénario: Trouver les meilleures opportunités du matin**

1. Lancez l'application: `python main.py`
2. Attendez le chargement des données (20-30 secondes)
3. Choisissez l'option **1** - "Meilleures opportunités"
4. L'application affiche le TOP 3 avec:
   - Score de trading (/100)
   - Prix actuel
   - Volatilité
   - Ratio de volume
   - **Potentiel de gain 1%** (taux de réussite historique)
   - Tous les indicateurs techniques

**Exemple de sortie:**

```
✅ TOP 3 ACTIONS POUR DAY TRADING :

======================================================================
#1 - TTE.PA | Score: 85/100 ⭐
======================================================================

  💰 Prix actuel: 63.42€
  📊 Tendance: Haussière
  📈 Volatilité 5j: 1.45%
  📦 Ratio Volume: 1.78x
  🎯 RSI: 52.3

  🎲 POTENTIEL GAIN 1%:
     Taux de réussite (10 derniers jours): 80.0%
     Jours où 1% atteint: 8/10
     Gain intraday moyen: 1.23%

  📊 INDICATEURS CLÉS:
     SMA 5: 62.85€ | Distance: +0.91%
     MACD: 0.234 | Signal: 0.189
     Stochastic K: 65.2
```

---

## ⚙️ Configuration

Le fichier `config.yaml` permet de personnaliser:

### Actions surveillées
```yaml
stocks:
  - MC.PA
  - OR.PA
  # ... ajoutez vos actions
```

### Paramètres de backtesting
```yaml
backtesting:
  days_history: 20              # Nombre de jours d'historique
  target_gain_percent: 1.0      # Objectif de gain
  max_loss_percent: 0.5         # Stop loss
  market_open: "09:00"
  market_close: "17:30"
```

### Critères de sélection
```yaml
selection_criteria:
  rsi_range: [40, 60]
  volatility_min: 0.8
  volatility_max: 2.5
  volume_ratio_min: 1.2
  price_above_sma5: true
```

---

## 📁 Structure du Projet

```
Finance/
├── main.py              # Application principale avec menu interactif
├── analyzer.py          # Moteur d'analyse et backtesting
├── data_fetcher.py      # Récupération des données Euronext
├── indicators.py        # Calcul des indicateurs techniques
├── config.yaml          # Configuration
├── requirements.txt     # Dépendances
└── README.md            # Documentation
```

---

## 🔮 Fonctions Futures

- **Fonction 2**: (À venir)
- **Fonction 3**: (À venir)

L'application se développe **fonction par fonction** selon vos besoins.

---

## ⚠️ Avertissements

> **IMPORTANT**: Cet outil est une **aide à la décision** uniquement.
>
> - ❌ Ne constitue PAS un conseil financier
> - ❌ Ne garantit PAS de profits
> - ❌ Les performances passées ne garantissent pas les résultats futurs
>
> **Tradez uniquement ce que vous pouvez vous permettre de perdre.**

---

## 🤝 Support

Cette application évolue fonction par fonction. Chaque nouvelle fonction sera ajoutée après validation de la précédente.

**Bon trading sur Euronext Paris ! 🇫🇷📈**
