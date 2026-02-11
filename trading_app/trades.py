"""
Gestion des trades: enregistrement, vérification, réévaluation, clôture, performance.
"""
import sqlite3
import json
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .config import logger, DB_PATH, DB_TIMEOUT, to_python_type
from . import config
from .constants import ACTIFS_PERMANENTS, POOL_ROTATION
from .market_context import (
    get_paris_time, get_regime_marche, get_session_marche,
    calculer_trade_grade
)
from .market_data import (
    get_twelvedata_intraday, get_twelvedata_quote, get_fresh_quote_for_trade,
    get_twelvedata_time_series
)
from .validation import analyser_cloture_intraday
from .ab_testing import get_variante_ab_pour_actif

def enrichir_opportunite_avec_donnees_marche(opportunite, donnees_marche, indicateurs_calcules=None):
    """
    Enrichit une opportunité Claude avec les données de marché réelles.
    Garantit que les indicateurs sont enregistrés même si Claude les oublie.
    """
    opp = opportunite.copy()
    actif_nom = opp.get('actif', '')
    symbole = opp.get('symbole', '')

    # Chercher les données de marché pour cet actif
    donnees_actif = None
    for nom, data in donnees_marche.items():
        if isinstance(data, dict):
            if nom == actif_nom or data.get('symbole') == symbole:
                donnees_actif = data
                break

    if donnees_actif:
        # Compléter avec les données de marché (si pas déjà présent)
        if not opp.get('atr_pct'):
            opp['atr_pct'] = donnees_actif.get('atr_pct')
        if not opp.get('volume_relatif'):
            opp['volume_relatif'] = donnees_actif.get('volume_relatif')
        if not opp.get('pivot'):
            opp['pivot'] = donnees_actif.get('pivot')
        if not opp.get('support1'):
            opp['support1'] = donnees_actif.get('support1')
        if not opp.get('resistance1'):
            opp['resistance1'] = donnees_actif.get('resistance1')

    # Chercher les indicateurs calculés pour cet actif
    if indicateurs_calcules:
        indic = indicateurs_calcules.get(actif_nom)
        if indic:
            if not opp.get('rsi'):
                opp['rsi'] = indic.get('RSI')
            if not opp.get('rsi_signal') and not opp.get('RSI_signal'):
                opp['rsi_signal'] = indic.get('RSI_signal')
            if not opp.get('macd_signal') and not opp.get('MACD'):
                opp['macd_signal'] = indic.get('MACD')

    return opp

def enregistrer_recommandation(trade_data):
    """Enregistre une recommandation de trade (avec fermeture DB garantie)

    CHANGEMENT v2: Validation stricte au lieu d'auto-correction.
    Les trades incohérents sont REJETÉS et loggés, pas corrigés silencieusement.

    CHANGEMENT v3: Déduplication cross-analyses.
    Rejette un trade si un trade OUVERT (resultat IS NULL) existe déjà
    aujourd'hui pour le même symbole. Évite les doublons BNP x3.
    """
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        cursor = conn.cursor()
        maintenant = get_paris_time()

        # === DÉDUPLICATION: Vérifier si un trade ouvert existe déjà pour ce symbole aujourd'hui ===
        symbole_check = trade_data.get('symbole', '')
        if not symbole_check:
            # Essayer de résoudre le symbole à partir du nom
            actif_nom_check = trade_data.get('actif', '')
            for sym, nom in ACTIFS_PERMANENTS.items():
                if nom == actif_nom_check:
                    symbole_check = sym
                    break
            if not symbole_check:
                for pool in POOL_ROTATION.values():
                    for sym, nom in pool.items():
                        if nom == actif_nom_check:
                            symbole_check = sym
                            break

        if symbole_check:
            cursor.execute(
                'SELECT COUNT(*) FROM trades_recommandes WHERE symbole = ? AND resultat IS NULL AND date = ?',
                (symbole_check, maintenant.date().isoformat())
            )
            nb_ouverts = cursor.fetchone()[0]
            if nb_ouverts > 0:
                logger.info(f"⚠️ [{symbole_check}] Trade déjà ouvert aujourd'hui — recommandation ignorée (déduplication)")
                print(f"  ⚠️ [{symbole_check}] Trade déjà ouvert aujourd'hui — recommandation ignorée")
                return None

        # Injecter le régime de marché actuel si non présent (pour validation R:R adaptatif)
        if 'regime_marche' not in trade_data or not trade_data.get('regime_marche'):
            regime, _, _ = get_regime_marche()
            trade_data['regime_marche'] = regime

        # NOTE: La validation (valider_opportunite) est faite par l'appelant
        # (api_market.py / scheduler.py) avant d'appeler cette fonction.
        entree = float(trade_data.get('entree', 0) or 0)
        stop = float(trade_data.get('stop', 0) or 0)
        tp1 = float(trade_data.get('tp1', 0) or 0)
        tp2 = float(trade_data.get('tp2', 0) or 0)
        direction = str(trade_data.get('direction', 'LONG')).upper()

        # Trouver le symbole et la catégorie
        symbole = trade_data.get('symbole', '')
        if not symbole:
            # Essayer de trouver le symbole à partir du nom
            actif_nom = trade_data.get('actif', '')
            for sym, nom in ACTIFS_PERMANENTS.items():
                if nom == actif_nom:
                    symbole = sym
                    break
            # Chercher aussi dans le pool de rotation
            if not symbole:
                for pool in POOL_ROTATION.values():
                    for sym, nom in pool.items():
                        if nom == actif_nom:
                            symbole = sym
                            break

        # Lazy import to avoid circular dependency
        from .journal import get_categorie_actif
        categorie = get_categorie_actif(symbole) if symbole else 'autre'

        # Extraire les indicateurs techniques de la recommandation
        rsi_reco = trade_data.get('rsi')
        rsi_signal_reco = trade_data.get('rsi_signal') or trade_data.get('RSI_signal')
        macd_signal_reco = trade_data.get('macd_signal') or trade_data.get('MACD')
        atr_pct_reco = trade_data.get('atr_pct')
        volume_relatif_reco = trade_data.get('volume_relatif')
        pivot_reco = trade_data.get('pivot')
        support1_reco = trade_data.get('support1')
        resistance1_reco = trade_data.get('resistance1')
        ratio_rr = trade_data.get('ratio_rr', '')
        ratio_rr_justification = trade_data.get('ratio_rr_justification', '')

        # A/B Testing - récupérer le groupe et la variante
        ab_groupe, ab_variante = get_variante_ab_pour_actif(symbole)
        ab_test_id = None
        if ab_groupe:
            # Trouver le test_id actif pour ce symbole
            for tid, test in config.AB_TESTS_ACTIFS.items():
                groupe_key = 'groupe_a' if ab_groupe == 'A' else 'groupe_b'
                if symbole in test.get(groupe_key, []):
                    ab_test_id = tid
                    break

        # === NOUVEAUX CHAMPS A/B TESTING AVANCÉ ===
        # 1. Timing Entry/Exit
        strategie_entree = trade_data.get('strategie_entree', 'IMMEDIATE')
        trailing_stop = 1 if trade_data.get('trailing_stop') else 0
        trailing_stop_pct = trade_data.get('trailing_stop_pct')
        prix_limite_entree = trade_data.get('prix_limite_entree')

        # 2. Contexte Marché Dynamique
        vix_niveau = trade_data.get('vix_niveau')
        regime_marche = trade_data.get('regime_marche', 'NORMAL')
        regles_adaptees = trade_data.get('regles_adaptees', '')

        # 3. Time Decay / Urgence
        validite_minutes = trade_data.get('validite_minutes', 60)
        heure_expiration = trade_data.get('heure_expiration', '')
        urgence = trade_data.get('urgence', 'MOYENNE')

        # 4. Multi Timeframe
        trend_daily = trade_data.get('trend_daily')
        trend_h4 = trade_data.get('trend_h4')
        trend_h1 = trade_data.get('trend_h1')
        alignement_tf = trade_data.get('alignement_tf', 0)
        confluence_score = trade_data.get('confluence_score', 0)

        # 5. Analyse Sentiment
        sentiment_score = trade_data.get('sentiment_score', 0)
        sentiment_source = trade_data.get('sentiment_source', 'TECHNIQUE')
        sentiment_detail = trade_data.get('sentiment_detail', '')

        # 6. Saisonnalité
        jours_fr = ['LUNDI', 'MARDI', 'MERCREDI', 'JEUDI', 'VENDREDI', 'SAMEDI', 'DIMANCHE']
        jour_semaine = trade_data.get('jour_semaine') or jours_fr[maintenant.weekday()]
        session_marche = trade_data.get('session_marche', '')
        pattern_jour = trade_data.get('pattern_jour', '')

        # Déterminer la session si non fournie
        if not session_marche:
            heure = maintenant.hour
            if 9 <= heure < 11:
                session_marche = 'EU_OPEN'
            elif 13 <= heure < 15:
                session_marche = 'US_PREMARKET'
            elif 15 <= heure < 17:
                session_marche = 'US_OPEN'
            elif 17 <= heure < 18:
                session_marche = 'EU_CLOSE'
            elif 21 <= heure < 22:
                session_marche = 'US_CLOSE'
            else:
                session_marche = 'HORS_SESSION'

        # Score de conviction global
        conviction_score = trade_data.get('conviction_score', 3)

        # === TRADE QUALITY GRADING ===
        # Préparer les données pour le calcul du grade
        grade_data = {
            'conviction_score': conviction_score,
            'ratio_rr': ratio_rr,
            'alignement_tf': alignement_tf,
            'confluence_score': confluence_score,
            'regime_marche': regime_marche,
            'session_marche': session_marche,
            'sentiment_score': sentiment_score,
            'direction': direction
        }
        grade_result = calculer_trade_grade(grade_data)
        trade_grade = grade_result['grade']
        grade_details = json.dumps(grade_result['details'], ensure_ascii=False)
        grade_setup_score = grade_result['setup_score']

        logger.info(f"Trade Grade: {symbole} - {grade_result['resume']}")

        # LOG: Tracer le prix d'entrée enregistré (debug prix incorrect)
        prix_actuel_data = trade_data.get('prix_actuel', 0)
        print(f"  📝 [{symbole}] Enregistrement: entrée={entree}, prix_actuel={prix_actuel_data}, direction={direction}")

        cursor.execute('''
            INSERT INTO trades_recommandes
            (date, heure_message, actif, symbole, type_setup, prix_entree, prix_stop,
             prix_tp1, prix_tp2, prix_actuel, catalyseur, duree_estimee, timestamp_reco,
             direction, categorie_actif, heure_entree,
             rsi_reco, rsi_signal_reco, macd_signal_reco, atr_pct_reco, volume_relatif_reco,
             pivot_reco, support1_reco, resistance1_reco, ratio_rr, ratio_rr_justification,
             ab_test_id, ab_groupe, ab_variante,
             strategie_entree, trailing_stop, trailing_stop_pct, prix_limite_entree,
             vix_niveau, regime_marche, regles_adaptees,
             validite_minutes, heure_expiration, urgence,
             trend_daily, trend_h4, trend_h1, alignement_tf, confluence_score,
             sentiment_score, sentiment_source, sentiment_detail,
             jour_semaine, session_marche, pattern_jour, conviction_score,
             trade_grade, grade_details, grade_setup_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            maintenant.date(),
            maintenant.strftime('%H:%M'),
            trade_data.get('actif', ''),
            symbole,
            'NEWS' if trade_data.get('is_news_trading') else 'TECHNIQUE',
            entree,
            stop,
            tp1,
            tp2,
            trade_data.get('prix_actuel', 0),
            trade_data.get('catalyseur', ''),
            trade_data.get('duree', ''),
            maintenant,
            direction,
            categorie,
            maintenant.hour,
            rsi_reco,
            rsi_signal_reco,
            macd_signal_reco,
            atr_pct_reco,
            volume_relatif_reco,
            pivot_reco,
            support1_reco,
            resistance1_reco,
            ratio_rr,
            ratio_rr_justification,
            ab_test_id,
            ab_groupe,
            ab_variante,
            # Nouveaux champs
            strategie_entree,
            trailing_stop,
            trailing_stop_pct,
            prix_limite_entree,
            vix_niveau,
            regime_marche,
            regles_adaptees,
            validite_minutes,
            heure_expiration,
            urgence,
            trend_daily,
            trend_h4,
            trend_h1,
            alignement_tf,
            confluence_score,
            sentiment_score,
            sentiment_source,
            sentiment_detail,
            jour_semaine,
            session_marche,
            pattern_jour,
            conviction_score,
            # Trade Quality Grading
            trade_grade,
            grade_details,
            grade_setup_score
        ))

        conn.commit()
        trade_id = cursor.lastrowid
        logger.info(f"Trade enregistré: #{trade_id} {symbole} {direction} @ {entree}")
        return trade_id
    except Exception as e:
        logger.error(f"Erreur enregistrement trade {symbole}: {e}")
        return None
    finally:
        if conn:
            conn.close()

def verifier_resultats_trades():
    """Vérifie et met à jour les résultats des trades ouverts (ordre chronologique)
    Avec suivi temps réel: prix max/min atteints, PnL max/min
    Utilise une transaction atomique pour éviter les états incohérents."""
    maintenant = get_paris_time()
    print(f"[{maintenant.strftime('%H:%M:%S')} CET] 🔍 Vérification résultats trades...")

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = 'DEFERRED'  # Transaction explicite
        cursor = conn.cursor()
        cursor.execute('BEGIN TRANSACTION')

        # Récupérer les trades sans résultat DU JOUR uniquement
        # (évite de vérifier des trades anciens avec des données intraday d'aujourd'hui)
        aujourdhui = maintenant.strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT * FROM trades_recommandes
            WHERE resultat IS NULL
            AND symbole IS NOT NULL AND symbole != ''
            AND date = ?
        ''', (aujourdhui,))
        trades_ouverts = [dict(row) for row in cursor.fetchall()]

        if not trades_ouverts:
            print(f"[{maintenant.strftime('%H:%M:%S')} CET] ℹ️ Aucun trade à vérifier")
            return 0

        nb_mis_a_jour = 0
        nb_tracking = 0

        for trade in trades_ouverts:
            try:
                symbole = trade['symbole']
                direction = trade.get('direction', 'LONG')
                entree = float(trade['prix_entree'] or 0)
                stop = float(trade['prix_stop'] or 0)
                tp1 = float(trade['prix_tp1'] or 0)
                tp2 = float(trade['prix_tp2'] or 0)

                if entree <= 0:
                    print(f"   ⚠️ Trade {trade.get('actif', 'inconnu')}: prix entrée invalide ({entree})")
                    continue

                # Récupérer les données intraday via Twelve Data (5min, 288 bougies = 24h pour trades overnight)
                hist, _ = get_twelvedata_intraday(symbole, interval="5min", outputsize=288)

                if hist.empty:
                    continue

                resultat = None
                prix_sortie = None
                pnl_pct = None

                # Tracking temps réel: calculer max/min sur toutes les bougies
                prix_max_session = hist['High'].max()
                prix_min_session = hist['Low'].min()
                prix_actuel = hist['Close'].iloc[-1]

                # Récupérer les valeurs précédentes de tracking
                prix_max_atteint = trade.get('prix_max_atteint') or prix_max_session
                prix_min_atteint = trade.get('prix_min_atteint') or prix_min_session

                # Mettre à jour les extremes
                prix_max_atteint = max(prix_max_atteint, prix_max_session)
                prix_min_atteint = min(prix_min_atteint, prix_min_session)

                # Calculer PnL actuel et extremes
                if direction == 'LONG':
                    pnl_actuel = ((prix_actuel - entree) / entree) * 100
                    pnl_max = ((prix_max_atteint - entree) / entree) * 100
                    pnl_min = ((prix_min_atteint - entree) / entree) * 100
                else:
                    pnl_actuel = ((entree - prix_actuel) / entree) * 100
                    pnl_max = ((entree - prix_min_atteint) / entree) * 100  # Inversé pour SHORT
                    pnl_min = ((entree - prix_max_atteint) / entree) * 100

                # Comparer avec les PnL extrêmes précédents
                prev_pnl_max = trade.get('pnl_max') or pnl_max
                prev_pnl_min = trade.get('pnl_min') or pnl_min
                pnl_max = max(pnl_max, prev_pnl_max)
                pnl_min = min(pnl_min, prev_pnl_min)

                nb_checks = (trade.get('nb_checks') or 0) + 1

                # NOUVELLE LOGIQUE: Utiliser analyser_cloture_intraday
                # Prend en compte: ordre chronologique, slippage, priorité intra-bougie
                resultat, prix_sortie, pnl_pct, bougie_cloture = analyser_cloture_intraday(
                    hist, direction, entree, stop, tp1, tp2
                )

                if resultat:
                    # Timestamp de sortie: utiliser l'heure réelle de la bougie de clôture
                    # (pas l'heure du check qui peut être 30min plus tard)
                    timestamp_sortie_reel = maintenant
                    if bougie_cloture is not None:
                        try:
                            # bougie_cloture est l'index pandas (datetime du candle 5min)
                            ts_bougie = pd.Timestamp(bougie_cloture)
                            if not ts_bougie.tzinfo:
                                timestamp_sortie_reel = ts_bougie.to_pydatetime()
                            else:
                                timestamp_sortie_reel = ts_bougie.to_pydatetime().replace(tzinfo=None)
                        except Exception:
                            pass  # Fallback: maintenant

                    # Trade terminé - calculer durée
                    duree_minutes = None
                    if trade.get('timestamp_reco'):
                        try:
                            ts_reco = datetime.fromisoformat(trade['timestamp_reco'].replace('Z', '+00:00'))
                            duree_minutes = int((timestamp_sortie_reel - ts_reco.replace(tzinfo=None)).total_seconds() / 60)
                        except ValueError:
                            pass

                    cursor.execute('''
                        UPDATE trades_recommandes
                        SET resultat = ?, prix_sortie = ?, pnl_pct = ?, timestamp_sortie = ?,
                            duree_minutes = ?, prix_max_atteint = ?, prix_min_atteint = ?,
                            prix_dernier_check = ?, timestamp_dernier_check = ?,
                            pnl_max = ?, pnl_min = ?, nb_checks = ?
                        WHERE id = ?
                    ''', (resultat, prix_sortie, round(pnl_pct, 2), timestamp_sortie_reel,
                          duree_minutes, round(prix_max_atteint, 4), round(prix_min_atteint, 4),
                          round(prix_actuel, 4), maintenant,
                          round(pnl_max, 2), round(pnl_min, 2), nb_checks,
                          trade['id']))
                    nb_mis_a_jour += 1
                    duree_str = f" ({duree_minutes}min)" if duree_minutes else ""
                    logger.info(f"Trade clos: {trade['actif']} {resultat} PnL={pnl_pct:+.2f}% Max={pnl_max:+.2f}%{duree_str}")
                    print(f"   ✅ {trade['actif']}: {resultat} ({pnl_pct:+.2f}%) | Max: {pnl_max:+.2f}%{duree_str}")
                else:
                    # Trade en cours - mettre à jour le tracking temps réel
                    cursor.execute('''
                        UPDATE trades_recommandes
                        SET prix_max_atteint = ?, prix_min_atteint = ?,
                            prix_dernier_check = ?, timestamp_dernier_check = ?,
                            pnl_max = ?, pnl_min = ?, nb_checks = ?
                        WHERE id = ?
                    ''', (round(prix_max_atteint, 4), round(prix_min_atteint, 4),
                          round(prix_actuel, 4), maintenant,
                          round(pnl_max, 2), round(pnl_min, 2), nb_checks,
                          trade['id']))
                    nb_tracking += 1
                    print(f"   📊 {trade['actif']}: En cours {pnl_actuel:+.2f}% | Max: {pnl_max:+.2f}% | Min: {pnl_min:+.2f}%")

            except Exception as e:
                logger.warning(f"Erreur trade {trade.get('actif', 'inconnu')}: {e}")
                continue

        conn.commit()  # Commit de la transaction atomique

        print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ {nb_mis_a_jour} terminé(s), {nb_tracking} en suivi")
        return nb_mis_a_jour

    except Exception as e:
        logger.error(f"Erreur vérification trades: {e}")
        if conn:
            try:
                conn.rollback()  # Annuler toutes les modifications en cas d'erreur
                print("🔄 Transaction annulée (rollback)")
            except:
                pass
        return 0
    finally:
        if conn:
            conn.close()

def analyser_historique_intraday_pour_tp_stop(symbole, direction, entree, stop, tp1, tp2):
    """
    Analyse l'historique intraday complet pour détecter si TP ou Stop a été touché.
    Retourne: (resultat, prix_sortie, note, timestamp_bougie) ou (None, None, None, None) si rien touché.
    """
    # Récupérer l'historique intraday complet (5min, 288 bougies = 24h pour trades overnight)
    hist, _ = get_twelvedata_intraday(symbole, interval="5min", outputsize=288)

    if hist.empty:
        return None, None, None, None

    # Parcourir chronologiquement pour trouver le premier événement
    for idx, row in hist.iterrows():
        high = row['High']
        low = row['Low']

        if direction == 'LONG':
            # Vérifier STOP d'abord (priorité au stop)
            if stop > 0 and low <= stop:
                return 'STOP', stop, f"Stop touché à {idx}", idx
            # Puis TP2
            if tp2 and tp2 > 0 and high >= tp2:
                return 'TP2', tp2, f"TP2 touché à {idx}", idx
            # Puis TP1
            if tp1 and tp1 > 0 and high >= tp1:
                return 'TP1', tp1, f"TP1 touché à {idx}", idx
        else:  # SHORT
            # Vérifier STOP d'abord
            if stop > 0 and high >= stop:
                return 'STOP', stop, f"Stop touché à {idx}", idx
            # Puis TP2
            if tp2 and tp2 > 0 and low <= tp2:
                return 'TP2', tp2, f"TP2 touché à {idx}", idx
            # Puis TP1
            if tp1 and tp1 > 0 and low <= tp1:
                return 'TP1', tp1, f"TP1 touché à {idx}", idx

    return None, None, None, None

def reevaluer_trades_intraday():
    """
    Réévaluation active des trades en cours.
    Analyse chaque trade et propose des ajustements:
    - Move stop to breakeven si TP50% atteint
    - Trailing stop activation si conviction >= 4
    - Alerte si le trade approche du stop
    - Recommandation: HOLD / RENFORCER / REDUIRE / SORTIR
    """
    maintenant = get_paris_time()
    print(f"[{maintenant.strftime('%H:%M:%S')} CET] 🔄 Réévaluation intraday des trades...")

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        aujourdhui = maintenant.strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT * FROM trades_recommandes
            WHERE resultat IS NULL
            AND symbole IS NOT NULL AND symbole != ''
            AND date = ?
        ''', (aujourdhui,))
        trades_ouverts = [dict(row) for row in cursor.fetchall()]

        if not trades_ouverts:
            print(f"   Aucun trade ouvert à réévaluer")
            return []

        print(f"   {len(trades_ouverts)} trade(s) à réévaluer")

        reevaluations = []

        for trade in trades_ouverts:
            try:
                symbole = trade.get('symbole')
                if not symbole:
                    continue

                # Récupérer le prix actuel
                quote = get_fresh_quote_for_trade(symbole)
                if not quote:
                    continue

                prix_actuel = quote.get('prix', 0)
                if prix_actuel <= 0:
                    continue

                entree = float(trade.get('prix_entree', 0) or 0)
                stop = float(trade.get('prix_stop', 0) or 0)
                tp1 = float(trade.get('prix_tp1', 0) or 0)
                direction = trade.get('direction', 'LONG')
                conviction = trade.get('conviction_score', 3)
                trailing_stop_actif = trade.get('trailing_stop', 0)
                trailing_stop_pct = trade.get('trailing_stop_pct', 0.3)

                if entree <= 0:
                    continue

                # Calcul PnL actuel
                if direction == 'LONG':
                    pnl_pct = ((prix_actuel - entree) / entree) * 100
                else:
                    pnl_pct = ((entree - prix_actuel) / entree) * 100

                # Calcul distance au TP et au Stop
                if direction == 'LONG':
                    distance_tp_pct = ((tp1 - prix_actuel) / prix_actuel) * 100 if tp1 > 0 else 0
                    distance_stop_pct = ((prix_actuel - stop) / prix_actuel) * 100 if stop > 0 else 0
                else:
                    distance_tp_pct = ((prix_actuel - tp1) / prix_actuel) * 100 if tp1 > 0 else 0
                    distance_stop_pct = ((stop - prix_actuel) / prix_actuel) * 100 if stop > 0 else 0

                # Déterminer le statut et l'action recommandée avec PRIORITÉ
                # Priorité: SORTIR > REDUIRE > RENFORCER > HOLD
                # On accumule les signaux et on garde le plus critique
                statut_intraday = trade.get('statut_intraday', 'EN_COURS')
                stop_ajuste = None
                detail_reevaluation = []
                signaux = []  # Liste de (priorité, action, raison)

                # 1. Vérifier si TP50% atteint → Breakeven
                tp_pct = ((tp1 - entree) / entree) * 100 if direction == 'LONG' and tp1 > 0 else \
                         ((entree - tp1) / entree) * 100 if direction == 'SHORT' and tp1 > 0 else 0

                if tp_pct > 0 and pnl_pct >= tp_pct * 0.5 and statut_intraday == 'EN_COURS':
                    statut_intraday = 'TP50_ATTEINT'
                    stop_ajuste = entree  # Breakeven
                    detail_reevaluation.append(f"✅ TP50% atteint ({pnl_pct:+.2f}%), stop → breakeven")
                    signaux.append((0, 'HOLD', 'TP50% protégé'))

                # 2. Trailing stop si activé et conviction élevée
                if trailing_stop_actif and conviction >= 4:
                    if direction == 'LONG':
                        nouveau_stop = prix_actuel * (1 - trailing_stop_pct / 100)
                        if nouveau_stop > (stop_ajuste or stop):
                            stop_ajuste = nouveau_stop
                            detail_reevaluation.append(f"📈 Trailing stop → {nouveau_stop:.2f}")
                    else:
                        nouveau_stop = prix_actuel * (1 + trailing_stop_pct / 100)
                        if nouveau_stop < (stop_ajuste or stop):
                            stop_ajuste = nouveau_stop
                            detail_reevaluation.append(f"📉 Trailing stop → {nouveau_stop:.2f}")

                # 3. Alerte si proche du stop (< 30% du chemin original restant)
                if direction == 'LONG' and stop > 0:
                    distance_originale_pct = ((entree - stop) / entree) * 100
                elif direction == 'SHORT' and stop > 0:
                    distance_originale_pct = ((stop - entree) / entree) * 100
                else:
                    distance_originale_pct = 0

                seuil_alerte = distance_originale_pct * 0.30
                if distance_stop_pct > 0 and distance_stop_pct < seuil_alerte:
                    pct_restant = (distance_stop_pct / distance_originale_pct * 100) if distance_originale_pct > 0 else 0
                    detail_reevaluation.append(f"⚠️ PROCHE DU STOP ({pct_restant:.0f}% restant)")
                    if pnl_pct < -0.3:
                        signaux.append((2, 'REDUIRE', f'Proche stop avec perte {pnl_pct:.2f}%'))

                # 4. Renforcer si très positif et haute conviction
                if pnl_pct > 0.5 and conviction >= 4 and statut_intraday != 'TP50_ATTEINT':
                    detail_reevaluation.append(f"💪 En profit ({pnl_pct:+.2f}%), renforcement possible")
                    signaux.append((1, 'RENFORCER', f'Profit {pnl_pct:.2f}% + conviction {conviction}'))

                # 5. Sortir si momentum inversé
                pnl_max = trade.get('pnl_max', 0) or 0
                if pnl_max > 0.5 and pnl_pct < pnl_max * 0.3:
                    detail_reevaluation.append(f"🔻 Momentum perdu: max {pnl_max:+.2f}% → actuel {pnl_pct:+.2f}%")
                    signaux.append((3, 'SORTIR', f'Perte momentum ({pnl_max:.2f}% → {pnl_pct:.2f}%)'))

                # 6. Sortir si perte importante (> 50% du stop)
                if pnl_pct < 0 and distance_originale_pct > 0:
                    perte_vs_stop = abs(pnl_pct) / distance_originale_pct
                    if perte_vs_stop > 0.7:  # > 70% du chemin vers le stop
                        signaux.append((3, 'SORTIR', f'Perte critique {pnl_pct:.2f}% (70%+ vers stop)'))

                # Déterminer l'action finale par priorité (le plus haut gagne)
                if signaux:
                    signaux.sort(key=lambda x: x[0], reverse=True)
                    action_recommandee = signaux[0][1]
                    # Ajouter toutes les raisons au détail
                    if len(signaux) > 1:
                        autres_signaux = [f"{s[1]}:{s[2]}" for s in signaux[1:]]
                        detail_reevaluation.append(f"[Autres signaux: {', '.join(autres_signaux)}]")
                else:
                    action_recommandee = 'HOLD'

                # Construire la réévaluation
                reevaluation = {
                    'trade_id': trade['id'],
                    'actif': trade.get('actif'),
                    'symbole': symbole,
                    'direction': direction,
                    'pnl_actuel': round(pnl_pct, 2),
                    'distance_tp_pct': round(distance_tp_pct, 2),
                    'distance_stop_pct': round(distance_stop_pct, 2),
                    'statut_intraday': statut_intraday,
                    'action_recommandee': action_recommandee,
                    'stop_ajuste': round(stop_ajuste, 4) if stop_ajuste else None,
                    'detail': ' | '.join(detail_reevaluation) if detail_reevaluation else 'RAS',
                    'timestamp': maintenant.isoformat()
                }
                reevaluations.append(reevaluation)

                # Mettre à jour le trade dans la base
                nb_reevaluations = (trade.get('nb_reevaluations') or 0) + 1
                anciennes_reeval = trade.get('reevaluations')
                if anciennes_reeval:
                    try:
                        liste_reeval = json.loads(anciennes_reeval)
                    except:
                        liste_reeval = []
                else:
                    liste_reeval = []
                liste_reeval.append(reevaluation)
                # Garder les 10 dernières
                liste_reeval = liste_reeval[-10:]

                cursor.execute('''
                    UPDATE trades_recommandes
                    SET statut_intraday = ?,
                        action_recommandee = ?,
                        stop_ajuste = ?,
                        nb_reevaluations = ?,
                        reevaluations = ?
                    WHERE id = ?
                ''', (
                    statut_intraday,
                    action_recommandee,
                    stop_ajuste,
                    nb_reevaluations,
                    json.dumps(liste_reeval, ensure_ascii=False),
                    trade['id']
                ))

                emoji = {'HOLD': '✋', 'RENFORCER': '💪', 'REDUIRE': '📉', 'SORTIR': '🚪'}.get(action_recommandee, '❓')
                print(f"   {emoji} {trade.get('actif')}: {pnl_pct:+.2f}% → {action_recommandee}")

            except Exception as e:
                print(f"   ⚠️ Erreur rééval {trade.get('actif', 'inconnu')}: {e}")
                continue

        conn.commit()
        print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ {len(reevaluations)} trade(s) réévalué(s)")
        return reevaluations

    except Exception as e:
        logger.error(f"Erreur réévaluation intraday: {e}")
        return []
    finally:
        if conn:
            conn.close()

def cloturer_trades_jour():
    """
    Clôture FORCÉE des trades 15 minutes avant fermeture du marché.
    AMÉLIORATION: Analyse l'historique intraday pour détecter si TP/Stop a été touché
    plus tôt dans la journée (évite les faux WIN_FORCE/LOSS_FORCE).
    - Si TP/Stop touché: utilise le vrai résultat (TP1, TP2, STOP)
    - Sinon: PnL > 0 = WIN_FORCE, PnL <= 0 = LOSS_FORCE
    """
    maintenant = get_paris_time()
    aujourdhui = maintenant.date()

    print(f"[{maintenant.strftime('%H:%M:%S')} CET] 🔔 Clôture forcée trades (15min avant fermeture)...")

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Récupérer les trades du jour sans résultat
        cursor.execute('''
            SELECT * FROM trades_recommandes
            WHERE date = ? AND resultat IS NULL AND symbole IS NOT NULL AND symbole != ''
        ''', (aujourdhui,))
        trades_ouverts = [dict(row) for row in cursor.fetchall()]

        nb_clotures = 0
        total_pnl = 0

        for trade in trades_ouverts:
            try:
                symbole = trade['symbole']
                direction = trade.get('direction', 'LONG')
                entree = float(trade['prix_entree'] or 0)
                stop = float(trade['prix_stop'] or 0)
                tp1 = float(trade['prix_tp1'] or 0)
                tp2 = float(trade['prix_tp2'] or 0)

                if entree <= 0:
                    print(f"   ⚠️ Trade {trade.get('actif', 'inconnu')}: prix entrée invalide ({entree})")
                    continue

                # AMÉLIORATION: Vérifier si TP/Stop a été touché plus tôt dans la journée
                resultat_historique, prix_historique, note_historique, ts_bougie_hist = analyser_historique_intraday_pour_tp_stop(
                    symbole, direction, entree, stop, tp1, tp2
                )

                # Timestamp de sortie réel (bougie si détecté historiquement, sinon maintenant)
                timestamp_sortie_reel = maintenant

                if resultat_historique and prix_historique is not None:
                    # TP ou Stop a été touché plus tôt - utiliser ce résultat
                    resultat = resultat_historique
                    prix_sortie = float(prix_historique)

                    # Utiliser l'heure réelle de la bougie comme timestamp de sortie
                    if ts_bougie_hist is not None:
                        try:
                            ts_b = pd.Timestamp(ts_bougie_hist)
                            timestamp_sortie_reel = ts_b.to_pydatetime().replace(tzinfo=None) if ts_b.tzinfo else ts_b.to_pydatetime()
                        except Exception:
                            pass

                    if direction == 'LONG':
                        pnl_pct = ((prix_sortie - entree) / entree) * 100
                    else:
                        pnl_pct = ((entree - prix_sortie) / entree) * 100

                    emoji = '🎯' if resultat in ['TP1', 'TP2'] else '🛑'
                    note_cloture = f" | {note_historique} (détecté à clôture)"
                else:
                    # Aucun TP/Stop touché - utiliser le prix actuel
                    quote = get_twelvedata_quote(symbole)

                    if not quote:
                        hist, _ = get_twelvedata_time_series(symbole, outputsize=1, interval="1day")
                        if hist.empty:
                            continue
                        prix_sortie = hist['Close'].iloc[-1]
                    else:
                        prix_sortie = quote.get('price', 0)

                    if prix_sortie == 0:
                        continue

                    if direction == 'LONG':
                        pnl_pct = ((prix_sortie - entree) / entree) * 100
                    else:
                        pnl_pct = ((entree - prix_sortie) / entree) * 100

                    # Classifier le résultat avec tolérance pour BREAKEVEN
                    if pnl_pct > 0.05:  # Win si > 0.05%
                        resultat = 'WIN_FORCE'
                        emoji = '✅'
                    elif pnl_pct >= -0.05:  # Breakeven si entre -0.05% et +0.05%
                        resultat = 'BREAKEVEN'
                        emoji = '⚖️'
                    else:  # Perte si < -0.05%
                        resultat = 'LOSS_FORCE'
                        emoji = '❌'

                    note_cloture = " | Clôture forcée 15min avant fermeture"

                # Calculer la durée (basée sur le timestamp réel de sortie)
                duree_minutes = None
                if trade.get('timestamp_reco'):
                    try:
                        ts_reco = datetime.fromisoformat(trade['timestamp_reco'].replace('Z', '+00:00'))
                        duree_minutes = int((timestamp_sortie_reel - ts_reco.replace(tzinfo=None)).total_seconds() / 60)
                    except ValueError:
                        pass

                # Récupérer les valeurs de tracking si disponibles
                prix_max = trade.get('prix_max_atteint') or prix_sortie
                prix_min = trade.get('prix_min_atteint') or prix_sortie
                pnl_max = trade.get('pnl_max') or pnl_pct
                pnl_min = trade.get('pnl_min') or pnl_pct

                # Mettre à jour le trade
                cursor.execute('''
                    UPDATE trades_recommandes
                    SET resultat = ?, prix_sortie = ?, pnl_pct = ?, timestamp_sortie = ?,
                        duree_minutes = ?, prix_dernier_check = ?, timestamp_dernier_check = ?,
                        notes = COALESCE(notes, '') || ?
                    WHERE id = ?
                ''', (
                    resultat, round(prix_sortie, 4), round(pnl_pct, 2), timestamp_sortie_reel,
                    duree_minutes, round(prix_sortie, 4), maintenant,
                    note_cloture,
                    trade['id']
                ))

                nb_clotures += 1
                total_pnl += pnl_pct
                duree_str = f" ({duree_minutes}min)" if duree_minutes else ""

                # Afficher avec les infos de tracking
                max_info = f" | Max atteint: {pnl_max:+.2f}%" if pnl_max != pnl_pct else ""
                print(f"   {emoji} {trade['actif']}: {resultat} ({pnl_pct:+.2f}%){max_info}{duree_str}")

            except Exception as e:
                print(f"   ⚠️ Erreur clôture {trade.get('actif', 'inconnu')}: {e}")
                continue

        conn.commit()

        if nb_clotures > 0:
            print(f"[{maintenant.strftime('%H:%M:%S')} CET] 📊 Bilan clôture: {nb_clotures} trade(s), PnL total: {total_pnl:+.2f}%")
        else:
            print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ Aucun trade à clôturer")

        return nb_clotures

    except Exception as e:
        logger.error(f"Erreur clôture trades: {e}")
        return 0
    finally:
        if conn:
            conn.close()

def get_trades_du_jour():
    """Récupère les trades du jour"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        aujourdhui = get_paris_time().date()
        cursor.execute('''
            SELECT * FROM trades_recommandes
            WHERE date = ?
            ORDER BY timestamp_reco DESC
        ''', (aujourdhui,))

        trades = [dict(row) for row in cursor.fetchall()]
        return trades
    except Exception as e:
        print(f"⚠️ Erreur récupération trades: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_performances(periode='semaine'):
    """Récupère les performances sur une période"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        cursor = conn.cursor()

        maintenant = get_paris_time()
        if periode == 'jour':
            date_debut = maintenant.date()
        elif periode == 'semaine':
            date_debut = (maintenant - timedelta(days=7)).date()
        elif periode == 'mois':
            date_debut = (maintenant - timedelta(days=30)).date()
        else:
            date_debut = (maintenant - timedelta(days=365)).date()

        cursor.execute('''
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as reussis_naturel,
                SUM(CASE WHEN resultat = 'WIN_FORCE' THEN 1 ELSE 0 END) as reussis_force,
                SUM(CASE WHEN resultat = 'STOP' THEN 1 ELSE 0 END) as stops_naturel,
                SUM(CASE WHEN resultat = 'LOSS_FORCE' THEN 1 ELSE 0 END) as stops_force,
                SUM(CASE WHEN resultat = 'NON_CONCLU' THEN 1 ELSE 0 END) as non_conclus,
                SUM(CASE WHEN resultat = 'EXPIRED' THEN 1 ELSE 0 END) as expires,
                SUM(CASE WHEN resultat = 'BREAKEVEN' THEN 1 ELSE 0 END) as breakeven,
                AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_total
            FROM trades_recommandes
            WHERE date >= ?
        ''', (date_debut,))

        row = cursor.fetchone()

        total = row[0] or 0
        reussis_naturel = row[1] or 0
        reussis_force = row[2] or 0
        stops_naturel = row[3] or 0
        stops_force = row[4] or 0
        non_conclus = row[5] or 0
        expires = row[6] or 0
        breakeven = row[7] or 0

        # Totaux combinés (naturels + forcés)
        reussis = reussis_naturel + reussis_force
        stops = stops_naturel + stops_force

        # Conclus = uniquement les trades avec résultat définitif (inclut BREAKEVEN)
        conclus = reussis + stops + breakeven

        return {
            'total': total,
            'reussis': reussis,
            'reussis_naturel': reussis_naturel,  # TP1, TP2
            'reussis_force': reussis_force,       # WIN_FORCE (clôture forcée positive)
            'stops': stops,
            'stops_naturel': stops_naturel,       # STOP
            'stops_force': stops_force,           # LOSS_FORCE (clôture forcée négative)
            'breakeven': breakeven,                # BREAKEVEN (ni gain ni perte)
            'non_conclus': non_conclus,           # Historique (ne devrait plus arriver)
            'expires': expires,                    # Trades orphelins auto-expirés
            'en_cours': total - conclus - non_conclus - expires,  # Trades en cours
            'taux_reussite': round((reussis / conclus * 100) if conclus > 0 else 0, 1),
            'pnl_moyen': round(row[8] or 0, 2),
            'pnl_total': round(row[9] or 0, 2)
        }
    except Exception as e:
        print(f"⚠️ Erreur performances: {e}")
        return {'total': 0, 'reussis': 0, 'reussis_naturel': 0, 'reussis_force': 0, 'stops': 0, 'stops_naturel': 0, 'stops_force': 0, 'breakeven': 0, 'non_conclus': 0, 'en_cours': 0, 'taux_reussite': 0, 'pnl_moyen': 0, 'pnl_total': 0}
    finally:
        if conn:
            conn.close()


def cloturer_trades_orphelins():
    """
    Clôture les trades des jours PRÉCÉDENTS restés sans résultat (biais de survivant).
    Appelée au démarrage et chaque matin avant la première analyse.
    Marque ces trades comme EXPIRED avec PnL=0 pour ne pas biaiser les statistiques.
    """
    maintenant = get_paris_time()
    aujourdhui = maintenant.strftime('%Y-%m-%d')

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, actif, symbole, date, prix_entree
            FROM trades_recommandes
            WHERE resultat IS NULL AND date < ?
        ''', (aujourdhui,))
        orphelins = cursor.fetchall()

        if not orphelins:
            return 0

        nb = 0
        for trade in orphelins:
            cursor.execute('''
                UPDATE trades_recommandes
                SET resultat = 'EXPIRED', pnl_pct = 0, prix_sortie = prix_entree,
                    timestamp_sortie = ?,
                    notes = COALESCE(notes, '') || ' [AUTO-EXPIRED: trade orphelin]'
                WHERE id = ?
            ''', (maintenant.isoformat(), trade['id']))
            nb += 1
            logger.warning(
                f"Trade orphelin cloture: {trade['actif']} ({trade['symbole']}) "
                f"du {trade['date']} -> EXPIRED"
            )

        conn.commit()
        print(f"[{maintenant.strftime('%H:%M:%S')} CET] "
              f"⚠️ {nb} trade(s) orphelin(s) cloture(s) (EXPIRED)")
        return nb

    except Exception as e:
        logger.error(f"Erreur cloture trades orphelins: {e}")
        return 0
    finally:
        if conn:
            conn.close()

