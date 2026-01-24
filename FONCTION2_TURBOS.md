# 🚀 FONCTION 2 : Sélection Automatique de Turbos

## 📋 Description

Cette fonction **sélectionne automatiquement les turbos call ou put** de la Société Générale correspondant aux valeurs recommandées par l'analyse.

### 🎯 Objectif

Maximiser le gain potentiel grâce à l'effet de levier :
- **Sous-jacent fait +1%** → **Turbo avec levier 10x fait +10%**
- Sélection automatique du meilleur turbo selon vos critères
- Recommandation intégrée dans l'affichage de chaque opportunité

---

## ✅ Fonctionnalités

### 1. Détection Automatique du Signal
- **BUY** (Achat recommandé) → Recherche de **Turbos CALL** (haussiers)
- **SELL** (Vente recommandée) → Recherche de **Turbos PUT** (baissiers)
- **HOLD** (Attendre) → Pas de turbo recommandé

### 2. Critères de Sélection Intelligents

Les turbos sont filtrés selon :
- **Levier** : Entre 5x et 15x (optimal : 10x)
- **Distance à la barrière** : Minimum 10% (sécurité)
- **Échéance** : Au moins 30 jours
- **Spread** : Maximum 0.5%

### 3. Scoring et Classement

Chaque turbo reçoit un score basé sur :
- Proximité du levier optimal (10x)
- Échéance idéale (60-180 jours)
- Spread le plus faible possible

---

## ⚙️ Configuration

Dans `config.yaml` :

```yaml
turbos:
  enabled: true                # Activer/désactiver la fonction

  selection:
    leverage_min: 5            # Levier minimum
    leverage_max: 15           # Levier maximum
    leverage_optimal: 10       # Levier idéal
    barrier_distance_min: 10.0 # Distance min à la barrière (%)
    min_days_to_expiry: 30     # Échéance minimale (jours)
    max_spread_percent: 0.5    # Spread maximum (%)

  cache:
    enabled: true
    duration_minutes: 60       # Durée de validité du cache
```

---

## 📊 Exemple d'Affichage

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

---

## 🔧 Architecture Technique

### Modules

1. **turbos_scraper.py**
   - Classe `TurboData` : Représentation d'un turbo
   - Classe `TurbosScraper` : Scraping et sélection

2. **analyzer.py** (modifié)
   - Intégration du scraper de turbos
   - Méthode `_determine_signal()` : Détection BUY/SELL/HOLD
   - Recommandation automatique de turbo dans `get_stock_analysis()`

3. **config.yaml** (étendu)
   - Section `turbos` avec tous les paramètres

### Mapping des Sous-jacents

Le fichier `config.yaml` contient le mapping entre les symboles et les noms sur le site SG :

```yaml
underlying_mapping:
  "MC.PA": "LVMH"
  "TTE.PA": "TOTALENERGIES"
  "^FCHI": "CAC 40"
  # ... etc
```

---

## 🌐 Scraping du Site Société Générale

### Version Actuelle : Mode Simulation

Pour le moment, le module fonctionne en **mode simulation** car :
- L'accès au site SG nécessite de connaître la structure exacte
- Les URL et sélecteurs CSS peuvent changer
- Besoin de tester sans surcharger le site

### Données Simulées

Le scraper génère des turbos fictifs avec des caractéristiques réalistes :
- ISIN aléatoires (FR001400XXXX)
- Leviers variés (5x, 8x, 10x, 12x, 15x)
- Échéances de 90 à 210 jours
- Prix et spreads réalistes

### Migration vers Scraping Réel

Pour activer le scraping réel du site SG, il faudra :

1. **Analyser la structure du site**
```python
# URL de recherche (exemple)
url = "https://produits-de-bourse.societegenerale.com/search"
params = {
    'underlying': 'LVMH',
    'product_type': 'turbo',
    'direction': 'call'
}
```

2. **Parser les résultats**
```python
soup = BeautifulSoup(response.content, 'html.parser')
turbos = soup.find_all('div', class_='product-item')
```

3. **Extraire les données**
- ISIN
- Levier
- Barrière
- Échéance
- Prix (Bid/Ask)

4. **Gérer les changements**
- Mise en cache des résultats
- Gestion des erreurs
- Logs pour debugging

---

## 📝 Actifs Supportés

### ✅ Actions Euronext Paris (15 valeurs)
- LVMH, L'Oréal, Air Liquide, Sanofi, TotalEnergies
- BNP Paribas, Schneider Electric, Safran, Hermès, AXA
- Capgemini, Veolia, Vinci, Bouygues, Pernod Ricard

### ✅ Indices (5 indices majeurs)
- CAC 40, S&P 500, Dow Jones, Nasdaq, DAX

### ⚠️ Non Supportés (pour le moment)
- Forex (pas de turbos SG sur devises)
- Métaux Précieux (vérifier disponibilité SG)
- Commodities (vérifier disponibilité SG)

---

## 🎯 Utilisation Quotidienne

### Workflow Recommandé

1. **Lancer l'analyse du matin**
```bash
python main.py
Choix: 1  # TOP 3 opportunités tous marchés
```

2. **Consulter les recommandations**
- L'application affiche automatiquement le turbo recommandé pour chaque opportunité
- Vérifier le signal (BUY/SELL)
- Noter l'ISIN du turbo

3. **Passer l'ordre sur le site SG**
- Se connecter sur https://produits-de-bourse.societegenerale.com
- Rechercher le turbo par ISIN
- Vérifier les caractéristiques
- Placer l'ordre

4. **Gérer le risque**
- **Barrière désactivante** : Si le sous-jacent atteint la barrière, le turbo vaut 0€
- **Stop loss** : Définir un seuil de perte acceptable
- **Taille de position** : Ne jamais investir plus que ce qu'on peut perdre

---

## ⚠️ Avertissements Importants

### Risques des Turbos

> **ATTENTION** : Les turbos sont des produits financiers **très risqués** :
>
> - **Effet de levier** : Les gains sont amplifiés, mais **les pertes aussi**
> - **Barrière désactivante** : Si le sous-jacent touche la barrière, le turbo vaut **0€**
> - **Perte totale possible** : Vous pouvez perdre **100% de votre investissement**
> - **Volatilité** : Les turbos peuvent perdre beaucoup de valeur rapidement
>
> **Ne tradez que l'argent que vous pouvez vous permettre de perdre**

### Responsabilité

- Cette application est un **outil d'aide à la décision**
- Ce n'est **PAS un conseil financier**
- Les performances passées ne garantissent **PAS** les résultats futurs
- **Faites toujours vos propres recherches**

---

## 🔮 Améliorations Futures

- [ ] Scraping réel du site SG (remplacer la simulation)
- [ ] Support des turbos sur métaux précieux
- [ ] Support des turbos sur commodities
- [ ] Calcul automatique de la taille de position
- [ ] Alertes si la barrière devient trop proche
- [ ] Historique des trades et performance
- [ ] Backtest des stratégies avec turbos

---

**Bon trading avec les turbos ! 🚀💰**

**⚠️ Tradez prudemment et gérez votre risque !**
