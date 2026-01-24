# 📈 Application de Trading Boursier

Application Python pour vous aider à **décider quand placer vos ordres boursiers** en utilisant l'analyse technique et des signaux de trading automatisés.

## 🎯 Fonctionnalités

- **📊 Analyse Technique Complète**
  - RSI (Relative Strength Index)
  - MACD (Moving Average Convergence Divergence)
  - Moyennes Mobiles (MA 20 & MA 50)
  - Niveaux de Support/Résistance
  - Analyse du Volume et Momentum

- **🚨 Génération de Signaux**
  - Signaux d'achat (BUY) lorsque les conditions sont favorables
  - Signaux de vente (SELL) pour protéger vos gains
  - Force du signal (0-100%) pour évaluer la confiance
  - Raisons détaillées pour chaque signal

- **⚙️ Configuration Flexible**
  - Liste de surveillance personnalisable
  - Paramètres d'indicateurs ajustables
  - Seuils d'alerte configurables

- **🔄 Modes d'Utilisation**
  - Analyse ponctuelle d'une action
  - Analyse de votre liste de surveillance complète
  - Surveillance continue avec rafraîchissement automatique

## 📦 Installation

### Prérequis

- Python 3.8 ou supérieur
- pip (gestionnaire de paquets Python)

### Installation des dépendances

```bash
pip install -r requirements.txt
```

Les dépendances principales sont :
- `yfinance` - Récupération des données boursières
- `pandas` - Manipulation de données
- `numpy` - Calculs numériques
- `ta` - Indicateurs techniques
- `colorama` - Affichage coloré dans le terminal
- `pyyaml` - Gestion de la configuration

## ⚙️ Configuration

Modifiez le fichier `config.yaml` pour personnaliser l'application :

```yaml
# Liste des actions à surveiller
stocks:
  - AAPL   # Apple
  - GOOGL  # Google
  - MSFT   # Microsoft
  - TSLA   # Tesla
  - AMZN   # Amazon

# Paramètres des indicateurs
indicators:
  rsi:
    period: 14
    oversold: 30    # Signal d'achat si RSI < 30
    overbought: 70  # Signal de vente si RSI > 70

  macd:
    fast_period: 12
    slow_period: 26
    signal_period: 9

  moving_averages:
    short_period: 20
    long_period: 50

# Paramètres d'alerte
alerts:
  min_signal_strength: 0.6  # Force minimum du signal (0-1)
```

## 🚀 Utilisation

### 1. Analyser une action spécifique

```bash
python main.py --analyze AAPL
```

Résultat :
```
📈 APPLICATION DE TRADING BOURSIER 📊
======================================================================

🔍 Analyse de AAPL...

📋 Informations:
   Nom: Apple Inc.
   Secteur: Technology
   Prix actuel: $178.50
   Capitalisation: $2.85T
   P/E Ratio: 29.5
   52w Haut: $199.62 | Bas: $164.08

🟢 BUY - AAPL @ $178.50
   Force: ████████░░ (80.0%)
   Raison: RSI en survente (28.5), MACD croisement haussier
   Indicateurs:
      RSI: 28.50
      MACD: 1.23
      MA20: 175.30
      MA50: 180.50
      Support: 164.08
      Resistance: 199.62
```

### 2. Analyser plusieurs actions

```bash
python main.py --analyze AAPL GOOGL TSLA MSFT
```

### 3. Analyser toute votre liste de surveillance

```bash
python main.py --watchlist
```

ou simplement :

```bash
python main.py
```

### 4. Surveillance continue

```bash
python main.py --monitor
```

Cette commande analyse votre liste de surveillance en continu selon l'intervalle configuré (défaut: 15 minutes).

## 📊 Comprendre les Signaux

### Types de Signaux

- **🟢 BUY (Achat)** : Conditions favorables pour acheter
  - RSI en zone de survente (< 30)
  - MACD croisement haussier
  - Golden Cross (MA20 > MA50)

- **🔴 SELL (Vente)** : Conditions favorables pour vendre
  - RSI en zone de surachat (> 70)
  - MACD croisement baissier
  - Death Cross (MA20 < MA50)

- **🟡 HOLD (Maintenir)** : Signaux contradictoires ou faibles

### Force du Signal

La force du signal est représentée par une barre de progression et un pourcentage :

- **0-30%** : Signal faible (ne sera pas affiché si min_signal_strength > 0.3)
- **30-60%** : Signal modéré
- **60-85%** : Signal fort
- **85-100%** : Signal très fort

### Indicateurs Techniques Utilisés

#### RSI (Relative Strength Index)
- **< 30** : Survente (signal d'achat potentiel)
- **> 70** : Surachat (signal de vente potentiel)
- **30-70** : Zone neutre

#### MACD (Moving Average Convergence Divergence)
- **Croisement haussier** : La ligne MACD croise la ligne de signal vers le haut → BUY
- **Croisement baissier** : La ligne MACD croise la ligne de signal vers le bas → SELL

#### Moyennes Mobiles (MA)
- **Golden Cross** : MA20 croise MA50 vers le haut → Signal haussier fort
- **Death Cross** : MA20 croise MA50 vers le bas → Signal baissier fort

## 📁 Structure du Projet

```
Finance/
├── main.py              # Application principale
├── stock_data.py        # Récupération des données boursières
├── indicators.py        # Calcul des indicateurs techniques
├── alerts.py            # Génération des signaux de trading
├── config.yaml          # Configuration
├── requirements.txt     # Dépendances Python
└── README.md            # Documentation
```

## 🎓 Exemples d'Utilisation Avancée

### Créer une configuration personnalisée

```bash
python main.py --config ma_config.yaml --analyze NVDA
```

### Surveillance uniquement pendant les heures de marché

Dans `config.yaml` :
```yaml
monitoring:
  interval_minutes: 5
  market_hours_only: true  # Surveiller uniquement de 9:30 à 16:00 EST
```

### Ajuster la sensibilité des signaux

Pour recevoir plus de signaux (moins de filtrage) :
```yaml
alerts:
  min_signal_strength: 0.3  # Au lieu de 0.6
```

Pour recevoir seulement les signaux très forts :
```yaml
alerts:
  min_signal_strength: 0.8
```

## ⚠️ Avertissements

> **IMPORTANT** : Cette application est un **outil d'aide à la décision** uniquement.
>
> - ❌ Ne constitue PAS un conseil financier
> - ❌ Ne garantit PAS des profits
> - ❌ Les performances passées ne présagent pas des résultats futurs
>
> **Toujours faire vos propres recherches avant d'investir !**

## 🔧 Dépannage

### Problème : "No module named 'yfinance'"
```bash
pip install yfinance pandas numpy ta colorama pyyaml
```

### Problème : "Aucune donnée trouvée pour XXXX"
- Vérifiez que le symbole est correct
- Certaines actions peuvent ne pas être disponibles via Yahoo Finance
- Vérifiez votre connexion internet

### Problème : "Rate limit exceeded"
- Ajoutez des pauses entre les requêtes (déjà implémenté : 0.5s)
- Réduisez le nombre d'actions surveillées
- Augmentez l'intervalle de surveillance

## 📝 TODO / Améliorations Futures

- [ ] Interface graphique (GUI)
- [ ] Notifications par email/SMS
- [ ] Support de plusieurs marchés (Europe, Asie)
- [ ] Backtesting des stratégies
- [ ] Export des signaux en CSV/Excel
- [ ] API REST pour intégration
- [ ] Machine Learning pour améliorer les prédictions

## 🤝 Contribution

Les contributions sont les bienvenues ! N'hésitez pas à :
- Signaler des bugs
- Proposer de nouvelles fonctionnalités
- Améliorer la documentation

## 📄 Licence

Ce projet est fourni tel quel, sans garantie. Utilisez-le à vos propres risques.

---

**Bon trading ! 📈💰**
