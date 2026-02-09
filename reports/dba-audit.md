# Rapport DBA Complet - Agent Trading SQLite

**Date**: 2026-02-09
**Version auditee**: branche `claude/fix-analysis-scheduler-FCanR`
**DB**: SQLite 3.x, fichier `trading.db` (164 KB)
**Couverture**: 12 tables, 85+ requetes SQL, 59 connexions, 21 index

---

## 1. Schema Complet

### 1.1 Inventaire des Tables

| Table | Colonnes | Lignes | UNIQUE | NOT NULL | Fonction |
|-------|----------|--------|--------|----------|----------|
| trades_recommandes | **76** | 0 | - | id seul | Table principale: trades, indicateurs, AB tests, grading |
| analyses | 7 | 0 | - | id seul | Analyses pre-market/intraday/cloture |
| journal_actifs | 10 | 0 | - | id seul | Memoire par actif (news, pattern, etc.) |
| alertes_news | 9 | 0 | - | id seul | Alertes news detectees |
| actifs_suivis | 7 | 0 | symbole | id seul | Liste des actifs personnalises |
| stats_quotidiennes | 10 | 0 | date | id seul | Stats quotidiennes agregees |
| journal_quotidien | 16 | 0 | (date,symbole) | id seul | Journal IA par actif/jour |
| bilan_quotidien | 8 | 0 | date | id seul | Bilan fin de journee |
| rapports_hebdo | 13 | 0 | semaine | id seul | Rapports hebdomadaires |
| criteres_dynamiques | 9 | 0 | - | id seul | Historique des ajustements criteres |
| ajustements_proposes | 21 | 0 | - | id seul | Propositions d'ajustement + feedback |
| ab_tests | 14 | 0 | - | nom | Infrastructure A/B testing |

### 1.2 Focus: `trades_recommandes` (76 colonnes)

Table critique avec 19 colonnes originales + 57 ajoutees via ALTER TABLE:
- **Core**: date, actif, symbole, prix_entree/stop/tp1/tp2, resultat, pnl_pct
- **Suivi temps reel**: prix_max/min_atteint, pnl_max/min, nb_checks
- **Indicateurs reco**: rsi_reco, macd_signal_reco, atr_pct_reco, volume_relatif_reco
- **Multi-timeframe**: trend_daily/h4/h1, alignement_tf, confluence_score
- **Marche**: vix_niveau, regime_marche, session_marche, jour_semaine
- **A/B testing**: ab_test_id, ab_groupe, ab_variante
- **Grading**: trade_grade, grade_entry/exit/setup_score, conviction_score

**Observation**: 76 colonnes est excessif pour SQLite. La majorite sont NULLable sans contrainte.

---

## 2. Index (21 total)

### 2.1 Index Existants (avant audit)

| Index | Table | Colonnes | Utilise? |
|-------|-------|----------|----------|
| idx_trades_date | trades_recommandes | date | OUI |
| idx_trades_symbole | trades_recommandes | symbole | OUI |
| idx_trades_resultat | trades_recommandes | resultat | OUI |
| idx_trades_date_resultat | trades_recommandes | date, resultat | OUI |
| idx_trades_date_symbole | trades_recommandes | date, symbole | OUI |
| idx_trades_conviction | trades_recommandes | conviction_score | OUI |
| idx_trades_regime | trades_recommandes | regime_marche | OUI |
| idx_trades_strategie | trades_recommandes | strategie_entree | RAREMENT |
| idx_trades_statut | trades_recommandes | statut_intraday | OUI |
| idx_trades_ab_test | trades_recommandes | ab_test_id | RAREMENT |
| ~~idx_analyses_type~~ | ~~analyses~~ | ~~type~~ | **BUG** |
| idx_analyses_date | analyses | date | OUI |
| idx_journal_date | journal_quotidien | date | OUI |
| idx_journal_symbole | journal_quotidien | symbole | OUI |
| idx_bilan_date | bilan_quotidien | date | OUI |
| idx_news_date | alertes_news | date | OUI |
| idx_criteres_date | criteres_dynamiques | date_maj | OUI |
| idx_ajustements_statut | ajustements_proposes | statut | OUI |
| idx_ajustements_date | ajustements_proposes | date_proposition | OUI |
| idx_ab_tests_statut | ab_tests | statut | OUI |

### 2.2 Bugs & Corrections Index

| # | Probleme | Severite | Statut |
|---|----------|----------|--------|
| 1 | `idx_analyses_type` sur colonne `type` (n'existe pas, la colonne s'appelle `type_analyse`) | **CRITIQUE** | **CORRIGE** -> `idx_analyses_type_analyse` |
| 2 | FULL SCAN sur `criteres_dynamiques.ajustement_source_id` (N+1 dans api_adjustments.py) | **MAJEUR** | **CORRIGE** -> `idx_criteres_source` |

---

## 3. PRAGMA & Configuration

### 3.1 Avant Corrections

| PRAGMA | Valeur | Commentaire |
|--------|--------|-------------|
| journal_mode | **delete** | Lent en concurrence, verrouillage exclusif |
| synchronous | 2 (FULL) | Securise mais lent |
| foreign_keys | 0 (OFF) | Aucune FK enforced |
| busy_timeout | 5000 | 5s par defaut |
| page_size | 4096 | Standard |
| cache_size | -2000 | 2MB cache |
| mmap_size | 0 | Pas de mmap |

### 3.2 Apres Corrections

| PRAGMA | Nouvelle Valeur | Gain |
|--------|----------------|------|
| journal_mode | **WAL** | **2.2x plus rapide** en concurrent (test: 1.1s -> 0.5s) |
| synchronous | **NORMAL** | Securise pour WAL, plus rapide que FULL |

---

## 4. Analyse des Requetes (85+ SQL)

### 4.1 Requetes par Fichier

| Fichier | Requetes | Risque | Problemes |
|---------|----------|--------|-----------|
| trades.py | 9 | BAS | Aucun |
| journal.py | 17 | MOYEN | WHERE dynamique (2), INSERT en boucle (2) |
| adjustments.py | 11 | BAS | INSERT en boucle (2) |
| analysis.py | 3 | BAS | Aucun |
| ab_testing.py | 7 | BAS | IN clause parametree (safe) |
| metrics.py | 1 | BAS | Aucun |
| routes/api_analytics.py | 12 | **MOYEN** | WHERE dynamique f-string (5) |
| routes/api_journal.py | 7 | BAS | IN clause parametree (safe) |
| routes/api_trades.py | 4 | BAS | Aucun |
| routes/api_adjustments.py | 1 | MOYEN | Pattern N+1 (1) |
| routes/api_market.py | 1 | BAS | Aucun |
| scheduler.py | 1 | BAS | Aucun |
| database.py | 5 | MOYEN | ALTER TABLE avec f-string (interne) |

### 4.2 Plans d'Execution (EXPLAIN QUERY PLAN)

| Requete | Avant | Apres |
|---------|-------|-------|
| `SELECT * FROM analyses WHERE type_analyse = ?` | **SCAN analyses** (full scan) | SEARCH via idx_analyses_type_analyse |
| `SELECT * FROM criteres_dynamiques WHERE ajustement_source_id = ?` | **SCAN criteres_dynamiques** (full scan) | SEARCH via idx_criteres_source |
| `SELECT * FROM trades_recommandes WHERE resultat IS NULL AND date = ?` | SEARCH via idx_trades_date_symbole | OK (inchange) |
| `SELECT * FROM trades_recommandes WHERE date >= ? AND resultat IS NOT NULL` | SEARCH via idx_trades_date | OK |
| `SELECT * FROM ajustements_proposes WHERE statut = ?` | SEARCH via idx_ajustements_statut | OK |

### 4.3 Patterns N+1 Identifies

| # | Fichier:Ligne | Description | Impact |
|---|---------------|-------------|--------|
| 1 | api_adjustments.py:128-136 | `SELECT FROM criteres_dynamiques` dans boucle `for ajust in historique` | MOYEN (corrige par index) |
| 2 | adjustments.py:73-101 | INSERT en boucle pour chaque ajustement | BAS (5-10 ops max) |
| 3 | journal.py:1060-1083 | INSERT criteres_dynamiques en boucle | BAS (5-10 ops max) |

**Recommandation**: Convertir en `executemany()` quand le volume augmentera.

---

## 5. Connexions & Thread-Safety

### 5.1 Inventaire Connexions

| Metrique | Avant | Apres |
|----------|-------|-------|
| Total connexions | 59 | 59 |
| Avec DB_TIMEOUT (10s) | 14 (24%) | **55 (93%)** |
| Sans DB_TIMEOUT (5s defaut) | 45 (76%) | 4 (7%, scripts uniquement) |

### 5.2 Pattern de Connexion

- **Toutes** les connexions utilisent try/except/finally
- **Aucune** connexion partagee entre threads (safe)
- **Aucun** context manager `with` (acceptable)
- **1 seul** ROLLBACK explicite (trades.py:444) - acceptable car operations atomiques simples

### 5.3 Threads Concurrents

```
Thread 1: scheduler (analyses, verifications, clotures)
Thread 2: precharger_donnees_marche (lecture seule)
Thread 3: rattrapage_differe (lecture seule)
Thread principal: Flask (API requests)
```

**Risque de collision**: Thread 1 (scheduler) vs Thread principal (API POST).
**Protection**: WAL mode permet lectures/ecritures simultanees.

---

## 6. Contraintes d'Integrite

### 6.1 NOT NULL

| Constat | Detail |
|---------|--------|
| Tables sans NOT NULL | **11 sur 12** (seule `ab_tests.nom` est NOT NULL) |
| Champs critiques non proteges | `trades_recommandes.date`, `.symbole`, `.prix_entree`, `.prix_stop`, `.prix_tp1`, `.direction` |
| Impact | Possible d'inserer un trade sans date ni symbole |
| Correction possible? | **NON** - SQLite ne supporte pas `ALTER TABLE ... ADD CONSTRAINT`. Necessiterait migration. |

### 6.2 Foreign Keys

| Constat | Detail |
|---------|--------|
| `PRAGMA foreign_keys` | **0 (OFF)** |
| FK definies | **0** |
| Relations implicites | `trades_recommandes.ab_test_id` -> `ab_tests.id` |
| | `criteres_dynamiques.ajustement_source_id` -> `ajustements_proposes.id` |
| Impact | Possible de referencer un ab_test_id inexistant |
| Recommandation | Acceptable pour la taille du projet. L'application gere la coherence. |

### 6.3 UNIQUE Constraints (5)

| Table | Contrainte | Correcte? |
|-------|-----------|-----------|
| actifs_suivis | symbole UNIQUE | OUI |
| stats_quotidiennes | date UNIQUE | OUI |
| journal_quotidien | (date, symbole) UNIQUE | OUI |
| bilan_quotidien | date UNIQUE | OUI |
| rapports_hebdo | semaine UNIQUE | OUI |

---

## 7. Estimation Croissance Donnees

### 7.1 Hypotheses

- 3-5 trades/jour ouvrable (~250 jours/an)
- 1 analyse pre-market + 1 cloture/jour
- 15 actifs suivis -> 15 journal_quotidien/jour
- 5-10 alertes news/jour
- 1 bilan + 1 rapport hebdo/semaine

### 7.2 Projections

| Table | Rows/jour | 6 mois | 1 an | Taille estimee |
|-------|-----------|--------|------|----------------|
| trades_recommandes | 4 | 500 | 1000 | ~500 KB (76 cols) |
| analyses | 2 | 250 | 500 | ~200 KB |
| journal_quotidien | 15 | 1875 | 3750 | ~1 MB |
| alertes_news | 7 | 875 | 1750 | ~500 KB |
| journal_actifs | 5 | 625 | 1250 | ~300 KB |
| criteres_dynamiques | 3 | 375 | 750 | ~100 KB |
| ajustements_proposes | 2 | 250 | 500 | ~150 KB |
| bilan_quotidien | 1 | 125 | 250 | ~200 KB |
| **Total** | | | | **~3 MB a 1 an** |

**Verdict**: Aucun risque de taille. SQLite gere confortablement jusqu'a 10+ GB. A 1 an, la DB restera sous 5 MB.

### 7.3 Point d'Attention: `trades_recommandes`

Avec 76 colonnes et ~1000 rows/an, cette table est large mais pas volumineuse. Le risque n'est pas la taille mais la **complexite des requetes** sur 76 colonnes. Les `SELECT *` retournent 76 colonnes alors que la plupart des use cases n'en utilisent que 5-10.

**Recommandation**: Remplacer `SELECT *` par colonnes explicites dans les requetes critiques (18 `SELECT *` identifies).

---

## 8. Strategie de Backup

### 8.1 Implementation Actuelle

```python
# database.py:backup_database()
- WAL checkpoint (TRUNCATE) avant copie
- shutil.copy2() (copie atomique avec metadonnees)
- Dossier: ./backups/trading_backup_YYYYMMDD_HHMMSS.db
- Retention: 7 derniers backups (cleanup_old_backups)
- Appel: schedule.every().day.at("18:05") dans scheduler.py
```

### 8.2 Evaluation

| Critere | Statut | Note |
|---------|--------|------|
| Backup automatique | OUI | 1x/jour a 18h05 |
| WAL checkpoint avant copie | OUI | Garantit coherence |
| Rotation | OUI | 7 derniers gardes |
| Test de restauration | **NON** | Pas de verification d'integrite apres backup |
| Backup avant migration | **NON** | Pas de backup automatique avant ALTER TABLE |
| Backup off-site | **NON** | Uniquement local dans ./backups/ |

### 8.3 Recommandations Backup

| Priorite | Action |
|----------|--------|
| HAUTE | Ajouter `PRAGMA integrity_check` apres chaque backup |
| MOYENNE | Backup avant chaque `init_database()` (protection ALTER TABLE) |
| BASSE | Copie vers stockage externe (S3, Google Drive, etc.) |

---

## 9. Tests d'Ecritures Concurrentes

### 9.1 Resultats (benchmark sur structure identique)

| Test | Mode DELETE | Mode WAL | Gain |
|------|-----------|----------|------|
| 4 writers, 50 ops chacun | 1.64s | - | baseline |
| 2W + 2R + 2M, 30 ops chacun | 1.13s | **0.52s** | **2.2x** |
| Erreurs concurrence | 0 | 0 | - |
| Integrite donnees | 100% | 100% | - |

### 9.2 Conclusion

- **WAL mode** resout le probleme de concurrence lecture/ecriture
- **Aucune perte de donnees** avec timeout=10s
- **Zero erreurs** meme avec 6 threads simultanes
- Le passage DELETE -> WAL est la correction la plus impactante de cet audit

---

## 10. Corrections Appliquees dans cet Audit

| # | Correction | Fichiers | Impact |
|---|-----------|----------|--------|
| 1 | **WAL mode** active (journal_mode=WAL, synchronous=NORMAL) | database.py + DB live | **Performance 2.2x** en concurrent |
| 2 | **Index casse** `idx_analyses_type` corrige -> `idx_analyses_type_analyse` | database.py + DB live | Elimine FULL SCAN sur analyses |
| 3 | **Index manquant** `idx_criteres_source` sur criteres_dynamiques.ajustement_source_id | database.py + DB live | Elimine FULL SCAN sur criteres_dynamiques |
| 4 | **DB_TIMEOUT** ajoute a 45 connexions (93% couverture) | 11 fichiers | Protection SQLITE_BUSY uniforme |
| 5 | **Import manquant** DB_TIMEOUT dans scheduler.py | scheduler.py | Corrige NameError potentiel |

---

## 11. Risques Residuels (Non Corriges)

| # | Risque | Severite | Justification |
|---|--------|----------|---------------|
| 1 | Pas de NOT NULL sur champs critiques (date, symbole, prix) | MAJEUR | SQLite ne supporte pas ALTER TABLE ADD CONSTRAINT. Migration requise. |
| 2 | Pas de FK entre trades.ab_test_id et ab_tests.id | MINEUR | Application gere la coherence. Volume faible. |
| 3 | 18 `SELECT *` au lieu de colonnes explicites | MINEUR | Pas d'impact performance a ce volume (<5MB). |
| 4 | Pattern N+1 dans api_adjustments.py:128 | MINEUR | Corrige par index. Convertir en JOIN quand volume augmentera. |
| 5 | 5 requetes avec WHERE dynamique f-string dans api_analytics.py | MOYEN | Parametres sont maps via if/elif (pas d'injection directe). A refactorer. |
| 6 | Pas de ROLLBACK explicite sur 50/51 connexions | MINEUR | Operations atomiques simples. Acceptable. |
| 7 | Pas de test d'integrite automatique apres backup | MOYEN | Ajouter PRAGMA integrity_check dans backup_database(). |
| 8 | cleanup_db.py n'utilise pas DB_TIMEOUT (4 appels) | MINEUR | Script de maintenance uniquement, pas d'acces concurrent. |

---

## 12. Resume Executif

### Ce qui va bien
- Schema coherent avec 5 contraintes UNIQUE appropriees
- 21 index couvrant les requetes critiques
- Backup automatique quotidien avec rotation (7 copies)
- Aucune erreur de concurrence avec timeout 10s
- Taille projetee a 1 an: ~3-5 MB (largement dans les limites SQLite)

### Ce qui a ete corrige
- **WAL mode** active: 2.2x plus rapide en ecriture concurrente
- **2 index** corriges/ajoutes: elimination de 2 FULL SCAN
- **45 connexions** securisees avec DB_TIMEOUT=10s (24% -> 93%)
- **1 import** manquant corrige (scheduler.py)

### Actions recommandees (ordre de priorite)
1. **Migration schema** pour ajouter NOT NULL sur champs critiques (requiert CREATE TABLE + INSERT INTO + DROP TABLE)
2. **PRAGMA integrity_check** apres chaque backup
3. **Remplacer SELECT * ** par colonnes explicites dans les 18 requetes concernees
4. **Refactorer** les 5 WHERE dynamiques de api_analytics.py en query builder safe
5. **Backup avant init_database()** pour proteger les ALTER TABLE

---

## 13. Annexe: Commandes de Verification

```bash
# Verifier le mode WAL
python3 -c "import sqlite3; c=sqlite3.connect('trading.db'); print(c.execute('PRAGMA journal_mode').fetchone())"

# Verifier l'integrite
python3 -c "import sqlite3; c=sqlite3.connect('trading.db'); print(c.execute('PRAGMA integrity_check').fetchone())"

# Lister les index
python3 -c "import sqlite3; c=sqlite3.connect('trading.db'); [print(r) for r in c.execute(\"SELECT name, tbl_name FROM sqlite_master WHERE type='index' ORDER BY tbl_name\")]"

# Taille de la DB
ls -lh trading.db
```
