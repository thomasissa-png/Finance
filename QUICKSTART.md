# 🚀 Guide de Démarrage Rapide

## Installation en 3 étapes

### 1️⃣ Installer les dépendances

```bash
pip install -r requirements.txt
```

### 2️⃣ Configurer vos actions (optionnel)

Éditez `config.yaml` pour ajouter vos actions préférées :

```yaml
stocks:
  - AAPL   # Apple
  - GOOGL  # Google
  - MSFT   # Microsoft
  - NVDA   # NVIDIA
  - TSLA   # Tesla
```

### 3️⃣ Lancer l'analyse !

```bash
# Analyser toute votre liste de surveillance
python main.py

# Ou analyser une action spécifique
python main.py --analyze AAPL
```

## 📊 Exemples de Commandes

```bash
# Analyser Apple
python main.py --analyze AAPL

# Analyser plusieurs actions
python main.py --analyze AAPL GOOGL MSFT

# Analyser votre watchlist complète
python main.py --watchlist

# Surveillance continue (rafraîchissement toutes les 15 min)
python main.py --monitor
```

## 💡 Interprétation des Signaux

### Signal d'Achat 🟢
```
🟢 BUY - AAPL @ $178.50
   Force: ████████░░ (80.0%)
   Raison: RSI en survente (28.5)
```
➡️ **Conditions favorables pour acheter** cette action

### Signal de Vente 🔴
```
🔴 SELL - TSLA @ $245.30
   Force: ███████░░░ (70.0%)
   Raison: RSI en surachat (75.2)
```
➡️ **Conditions favorables pour vendre** cette action

## ⚙️ Configuration Rapide

### Recevoir plus de signaux
Dans `config.yaml`, réduisez le seuil :
```yaml
alerts:
  min_signal_strength: 0.3  # Au lieu de 0.6
```

### Recevoir moins de signaux (mais plus fiables)
```yaml
alerts:
  min_signal_strength: 0.8  # Au lieu de 0.6
```

### Changer l'intervalle de surveillance
```yaml
monitoring:
  interval_minutes: 5  # Vérifier toutes les 5 minutes
```

## 🎯 Conseils d'Utilisation

1. **Commencez par analyser quelques actions** : `python main.py --analyze AAPL GOOGL`
2. **Comprenez les indicateurs** : RSI, MACD, MA - lisez la documentation
3. **Ne suivez pas aveuglément les signaux** : faites toujours vos propres recherches
4. **Utilisez plusieurs sources** : combinez cette application avec d'autres analyses
5. **Testez avec de petites positions** avant d'investir massivement

## ⚠️ Important

> Cette application est un outil d'aide à la décision, PAS un conseil financier.
> Investissez uniquement ce que vous pouvez vous permettre de perdre.

---

**Besoin d'aide ?** Consultez le [README.md](README.md) complet pour plus de détails.
