# Finance Trading App — Instructions pour Claude

## Règle critique: Tests anti-régression

**AVANT chaque commit**, lancer les tests de régression:

```bash
python -m pytest tests/test_regression.py -v --tb=short
```

Les 55 tests doivent TOUS passer. Si un test échoue, c'est une régression — corriger AVANT de commit.

**Quand ajouter un test**: chaque fois qu'un nouveau bug est découvert et corrigé, ajouter un test correspondant dans `tests/test_regression.py` dans le groupe approprié (ou créer un nouveau groupe).

## Architecture

- `trading_app/app.py` — Orchestrateur Flask (77 lignes)
- `trading_app/config.py` — État global partagé (caches, locks, flags)
- `trading_app/trades.py` — Gestion des trades (INSERT, vérification, clôture)
- `trading_app/market_data.py` — Sources de prix (TwelveData, yfinance)
- `trading_app/validation.py` — Validation prix, R:R, heures marché
- `trading_app/scheduler.py` — Tâches planifiées (analyses, vérifications)
- `trading_app/routes/` — 6 modules Blueprint Flask

## Règles critiques à ne JAMAIS violer

### 1. TwelveData: utiliser "last", pas "close"
Le plan est **GROW (payant, temps réel)**. Le champ `"last"` = prix intraday temps réel. Le champ `"close"` = clôture de la VEILLE.
```python
# CORRECT:
td_price = float(data.get("last") or data.get("close") or 0)
# INCORRECT:
td_price = float(data.get("close") or 0)  # ← PRIX D'HIER!
```

### 2. convert_symbol_to_twelvedata retourne un TUPLE
```python
# CORRECT:
td_symbol, mic_code = convert_symbol_to_twelvedata(yahoo_sym)
# INCORRECT:
td_sym = convert_symbol_to_twelvedata(yahoo_sym)  # ← C'est un tuple!
```

### 3. Variables cross-module: config.VARIABLE
```python
# CORRECT:
config.TWELVEDATA_QUOTA_EXCEEDED = True
# INCORRECT:
global TWELVEDATA_QUOTA_EXCEEDED  # ← Ne fonctionne PAS entre modules
TWELVEDATA_QUOTA_EXCEEDED = True
```

### 4. Connexions DB: toujours conn = None + try/finally
```python
conn = None
try:
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
    # ... opérations ...
except Exception as e:
    logger.error(f"Erreur: {e}")
finally:
    if conn:
        conn.close()
```

### 5. Prix DB en float: toujours convertir
```python
entree = float(trade.get('prix_entree', 0) or 0)
# Jamais: entree = trade.get('prix_entree', 0)  # ← peut être string ou None
```

### 6. Pas de trades dupliqués
`enregistrer_recommandation()` vérifie `WHERE symbole = ? AND resultat IS NULL AND date = ?` avant INSERT. Le scheduler et l'API pré-chargent aussi `symboles_traites`.

### 7. Scheduler: isolation des erreurs
`run_pending_safe()` catch les exceptions PAR JOB et force le reschedule. Ne jamais utiliser `schedule.run_pending()` directement.
