# 📈 Trading Platform - Day Trading avec IA

Application web de **day trading** avec **Claude AI** pour l'analyse de marché et les recommandations de trades.

## 🎯 Fonctionnalités

### Analyse de Marché
- **41+ actifs** surveillés en temps réel (Actions EU, US, Indices, Forex, Commodités)
- **Indicateurs techniques** : RSI, MACD, ATR, Pivot Points
- **News Flash** avec analyse d'impact trading par IA
- **Données Twelve Data API** avec cache intelligent

### Recommandations de Trades
- Opportunités générées par Claude AI
- Ratio Risk/Reward minimum 1:1.5
- Suivi automatique des TP/Stop
- Clôture forcée 15min avant fermeture marché

### Apprentissage Continu
- **Feedback loop** : analyse des performances pour amélioration
- **Auto-validation** des ajustements basée sur l'historique
- **A/B Testing** pour expérimentation de stratégies
- **Rapports hebdomadaires** automatiques (vendredi 23h)

### Interface Web
- Dashboard temps réel
- Journal de trading
- Page Performances avec statistiques détaillées
- Navigation historique (news, rapports)

---

## 🚀 Installation

### Prérequis
- Python 3.8+
- Clés API : Anthropic, Twelve Data, NewsAPI (optionnel), Twilio (optionnel)

### Installation

```bash
cd trading_app
pip install -r requirements.txt
```

### Variables d'environnement

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export TWELVEDATA_API_KEY="..."
export NEWSAPI_KEY="..."  # Optionnel
export TWILIO_ACCOUNT_SID="..."  # Optionnel - alertes WhatsApp
export TWILIO_AUTH_TOKEN="..."
```

### Lancement

```bash
cd trading_app
python app.py
```

L'application sera accessible sur `http://localhost:5000`

---

## 📁 Structure du Projet

```
Finance/
├── trading_app/           # Application Flask principale
│   ├── app.py             # Code principal (~5000 lignes)
│   ├── templates/         # Templates HTML
│   │   ├── index.html     # Dashboard principal
│   │   ├── journal.html   # Journal de trading
│   │   └── memoire.html   # Page performances
│   ├── static/            # Assets CSS/JS
│   └── requirements.txt   # Dépendances Python
├── docs/                  # Documentation
│   ├── GUIDE_DEMARRAGE.md
│   └── FONCTION2_TURBOS.md
├── archive/               # Ancien code CLI (référence)
└── README.md
```

---

## 📊 Actifs Surveillés

### Actions Françaises (Euronext Paris)
LVMH, L'Oréal, Hermès, TotalEnergies, Sanofi, BNP, AXA, Safran, Air Liquide...

### Actions US
Apple, Microsoft, Nvidia, Tesla, Amazon, Google, Meta...

### Indices
CAC 40, S&P 500, Nasdaq, DAX, Nikkei...

### Forex
EUR/USD, GBP/USD, USD/JPY

### Commodités
Or, Argent, Pétrole (Brent, WTI), Gaz naturel, Blé, Café...

---

## ⚠️ Avertissements

> **IMPORTANT** : Cet outil est une **aide à la décision** uniquement.
>
> - ❌ Ne constitue PAS un conseil financier
> - ❌ Ne garantit PAS de profits
> - ⚠️ Le trading avec levier est **très risqué**
> - ⚠️ Utilisez toujours un **stop loss**
>
> **Tradez uniquement ce que vous pouvez vous permettre de perdre.**

---

## 📝 Licence

Usage personnel uniquement.
