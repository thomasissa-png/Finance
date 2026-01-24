# 🚀 Guide de Démarrage pour Débutants

## 📋 Ce dont vous avez besoin

- Un ordinateur avec Python installé
- Une connexion Internet
- 5 minutes de votre temps !

---

## ✅ ÉTAPE 1 : Installer Python (si pas déjà fait)

### Windows
1. Allez sur https://www.python.org/downloads/
2. Téléchargez Python 3.8 ou plus récent
3. **IMPORTANT** : Cochez "Add Python to PATH" pendant l'installation
4. Cliquez sur "Install Now"

### Mac
Python est déjà installé ! Ouvrez le Terminal et tapez :
```bash
python3 --version
```

### Linux
```bash
sudo apt-get update
sudo apt-get install python3 python3-pip
```

---

## ✅ ÉTAPE 2 : Ouvrir le Terminal / Invite de Commandes

### Windows
1. Appuyez sur `Windows + R`
2. Tapez `cmd` et appuyez sur Entrée
3. Vous êtes dans l'invite de commandes !

### Mac
1. Appuyez sur `Cmd + Espace`
2. Tapez "Terminal"
3. Appuyez sur Entrée

### Linux
1. Appuyez sur `Ctrl + Alt + T`

---

## ✅ ÉTAPE 3 : Aller dans le dossier de l'application

Dans le terminal, tapez :

```bash
cd /home/user/Finance
```

Puis appuyez sur Entrée.

**Vérifiez que vous êtes au bon endroit :**
```bash
ls
```

Vous devriez voir les fichiers : `main.py`, `config.yaml`, `README.md`, etc.

---

## ✅ ÉTAPE 4 : Installer les dépendances

**C'est l'étape la plus importante !**

Tapez cette commande :

```bash
pip install -r requirements.txt
```

**OU si ça ne marche pas :**

```bash
pip3 install -r requirements.txt
```

**OU si vous êtes sur Mac/Linux :**

```bash
python3 -m pip install -r requirements.txt
```

**Attendez que ça se termine** (2-3 minutes). Vous verrez plein de lignes défiler, c'est normal !

Quand c'est fini, vous verrez :
```
Successfully installed yfinance-0.2.36 pandas-2.0.0 ...
```

---

## ✅ ÉTAPE 5 : Lancer l'application !

Tapez simplement :

```bash
python main.py
```

**OU si ça ne marche pas :**

```bash
python3 main.py
```

---

## 🎯 Ce qui va se passer

### 1. Chargement des données (30-60 secondes)

Vous verrez :
```
======================================================================
📊 CHARGEMENT DES DONNÉES MULTI-MARCHÉS
   Actions • Forex • Métaux • Commodities • Indices
======================================================================

📊 Récupération des données pour 49 actions...
📅 Période : 20 derniers jours

  📈 MC.PA... ✅ 20 jours récupérés
  📈 OR.PA... ✅ 20 jours récupérés
  📈 AI.PA... ✅ 20 jours récupérés
  ...
```

**C'est normal que ça prenne du temps**, l'application télécharge les données de 60+ actifs !

### 2. Le menu principal apparaît

```
======================================================================
OPTIONS DISPONIBLES:
======================================================================

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

Votre choix: _
```

---

## 🎮 TEST RAPIDE : Les 3 Meilleures Opportunités

### Tapez `1` puis Entrée

L'application va chercher les 3 meilleures opportunités du moment !

Vous verrez quelque chose comme :

```
======================================================================
#1 - MC.PA | LVMH
🏢 Action (Euronext) | Score: 85/100 ⭐
======================================================================

  💰 Prix actuel: 680.50€
  📊 Tendance: Haussière
  📈 Volatilité 5j: 1.45%
  📦 Ratio Volume: 1.78x
  🎯 RSI: 52.3

  🎲 POTENTIEL GAIN 1%:
     Taux de réussite (10 derniers jours): 80.0%
     Jours où 1% atteint: 8/10
     Gain intraday moyen: 1.23%

  📊 INDICATEURS CLÉS:
     SMA 5: 665.30€ | Distance: +2.28%
     MACD: 3.45 | Signal: 2.87
     Stochastic K: 65.2

  🎯 SIGNAL: 📈 ACHAT RECOMMANDÉ

  🚀 TURBO RECOMMANDÉ:
     Type: CALL (Haussier)
     ISIN: FR0014008VK3
     Nom: LVMH Turbo CALL 10X
     Levier: 10x
     Prix: 1.52€ (Bid: 1.50€ / Ask: 1.55€)
     Spread: 0.05€
     Échéance: 2026-03-31 (96 jours)

     💡 Si LVMH fait +1.0%
        → Turbo fait environ +10.0% (effet levier 10x)

     ⚠️  ATTENTION: Produit à effet de levier - Risque de perte totale

```

**FÉLICITATIONS ! Ça marche !** 🎉

---

## 🧪 Autres Tests à Essayer

### Test 2 : Analyser une action spécifique

1. Au menu, tapez `10` puis Entrée
2. Tapez `MC.PA` (LVMH) puis Entrée
3. Vous verrez l'analyse complète de LVMH !

### Test 3 : Voir les actions les plus volatiles

1. Au menu, tapez `11` puis Entrée
2. Vous verrez le TOP 10 des actifs les plus volatiles

### Test 4 : TOP 3 Forex

1. Au menu, tapez `3` puis Entrée
2. Vous verrez les 3 meilleures paires de devises

### Test 5 : Voir tous les actifs surveillés

1. Au menu, tapez `20` puis Entrée
2. Vous verrez la liste complète des 60+ actifs

---

## ❌ Quitter l'Application

Au menu, tapez `0` puis Entrée.

Vous verrez :
```
👋 Au revoir ! Bon trading !
```

---

## 🔧 Problèmes Courants et Solutions

### Problème 1 : "python: command not found"
**Solution :** Essayez `python3` au lieu de `python`

### Problème 2 : "No module named 'yfinance'"
**Solution :** Relancez l'étape 4 (installation des dépendances)
```bash
pip3 install -r requirements.txt
```

### Problème 3 : "Permission denied"
**Solution :** Ajoutez `sudo` devant la commande :
```bash
sudo pip3 install -r requirements.txt
```

### Problème 4 : L'application ne trouve aucune opportunité
**Raison :** C'est normal ! Selon les conditions du marché, il se peut qu'aucun actif ne remplisse tous les critères.

**Solution :**
- Essayez les options 11-15 (analyses différentes)
- Attendez et relancez plus tard
- Les critères sont stricts pour garantir la qualité

### Problème 5 : Le chargement est très long
**C'est normal !** L'application télécharge 60+ actifs avec 20 jours d'historique. Ça peut prendre 1-2 minutes.

**Patience !** ☕

### Problème 6 : "Aucun turbo SG trouvé"
**C'est normal !** L'application fonctionne en mode simulation pour les turbos.

Les turbos affichés sont des **exemples réalistes** pour vous montrer comment ça fonctionne.

---

## 📊 Comment Interpréter les Résultats

### Score de Trading (/100)
- **80-100** : Excellente opportunité ⭐⭐⭐
- **60-79** : Bonne opportunité ⭐⭐
- **40-59** : Opportunité moyenne ⭐
- **<40** : Peu intéressant

### Signal de Trading
- **📈 ACHAT** : L'analyse recommande d'acheter
- **📉 VENTE** : L'analyse recommande de vendre
- **⏸️ ATTENDRE** : Signaux contradictoires, mieux vaut attendre

### Potentiel Gain 1%
- **Taux de réussite** : Sur les 10 derniers jours, combien de fois l'actif a dépassé +1% en intrajournalier
- **80%+** : Très bon
- **50-79%** : Correct
- **<50%** : Peu probable

### Turbo Recommandé
- **Levier 10x** : Si l'actif fait +1%, le turbo fait +10%
- **ATTENTION** : Si l'actif touche la barrière, le turbo vaut 0€ !

---

## 🎯 Utilisation Quotidienne Recommandée

### Chaque Matin (Avant 9h00)

1. Lancez l'application :
   ```bash
   python main.py
   ```

2. Attendez le chargement (1-2 minutes)

3. Choisissez option `1` : TOP 3 opportunités

4. Notez les 2-3 meilleures actions

5. **Faites vos propres recherches** avant d'investir !

6. Quittez avec `0`

---

## ⚠️ RAPPELS IMPORTANTS

### Cette application est une AIDE À LA DÉCISION
- ❌ Ce n'est PAS un conseil financier
- ❌ Ça ne garantit PAS de profits
- ❌ Faites TOUJOURS vos propres recherches

### Les Turbos sont TRÈS RISQUÉS
- 🔴 Barrière désactivante = perte totale
- 🔴 Effet de levier = pertes amplifiées
- 🔴 Réservé aux traders expérimentés

### Ne Tradez QUE l'argent que vous pouvez perdre !

---

## 🆘 Besoin d'Aide ?

Si vous avez un problème :

1. Vérifiez la section "Problèmes Courants" ci-dessus
2. Relisez les étapes depuis le début
3. Vérifiez que vous avez bien installé les dépendances (Étape 4)

---

## 🎉 Prochaines Étapes

Une fois que vous maîtrisez l'application :

1. **Personnalisez** `config.yaml` (ajoutez vos actions préférées)
2. **Testez** les différentes options du menu
3. **Comparez** les résultats avec la réalité du marché
4. **Affinez** vos critères de sélection

---

**Bon trading et bonne découverte de l'application !** 📈💰

**N'oubliez pas : la patience et la discipline sont les clés du succès en trading !** 🔑
