"""
Contexte de marché: horloge, VIX, sessions, régime, grading, événements macro.
"""
import os
import json
import time
from datetime import datetime, timedelta

import pytz
import yfinance as yf

from .config import (
    TZ_PARIS, VIX_CACHE_TTL, VIX_CACHE_FILE,
    logger, DB_PATH, DB_TIMEOUT, to_python_type
)
from . import config

def load_vix_cache():
    """Charge le cache VIX depuis le fichier persistant"""
    try:
        cache_path = os.path.join(os.path.dirname(DB_PATH) or '.', VIX_CACHE_FILE)
        if os.path.exists(cache_path):
            with open(cache_path, 'r') as f:
                cached = json.load(f)
                # Vérifier si le cache n'est pas trop vieux (max 1h)
                if time.time() - cached.get('timestamp', 0) < 3600:
                    config.VIX_CACHE = cached
                    print(f"✅ Cache VIX chargé: {config.VIX_CACHE['value']:.1f} ({config.VIX_CACHE['source']})")
    except Exception as e:
        print(f"⚠️ Erreur chargement cache VIX: {e}")

def save_vix_cache():
    """Sauvegarde le cache VIX dans un fichier persistant"""
    try:
        cache_path = os.path.join(os.path.dirname(DB_PATH) or '.', VIX_CACHE_FILE)
        with open(cache_path, 'w') as f:
            json.dump(config.VIX_CACHE, f)
    except Exception as e:
        print(f"⚠️ Erreur sauvegarde cache VIX: {e}")

# ============================================================================
# FONCTIONS UTILITAIRES
# ============================================================================

def get_paris_time():
    """Retourne l'heure actuelle à Paris"""
    return datetime.now(TZ_PARIS)

def get_utc_time_for_paris(heure_paris):
    """Convertit une heure française en heure UTC"""
    maintenant = get_paris_time()
    # Validation du format HH:MM
    try:
        parts = heure_paris.split(':')
        if len(parts) < 2:
            raise ValueError(f"Format heure invalide: {heure_paris}")
        hour = int(parts[0])
        minute = int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"Heure hors limites: {heure_paris}")
    except (ValueError, AttributeError) as e:
        logger.warning(f"Format heure invalide '{heure_paris}': {e}, utilisation 09:00 par défaut")
        hour, minute = 9, 0

    heure_cible = maintenant.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0
    )
    heure_utc = heure_cible.astimezone(pytz.UTC)
    return heure_utc.strftime('%H:%M')

def is_weekend():
    """Vérifie si c'est le weekend (samedi ou dimanche)"""
    maintenant = get_paris_time()
    return maintenant.weekday() >= 5  # 5=samedi, 6=dimanche

def get_jour_semaine_nom():
    """Retourne le nom du jour en français"""
    jours = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']
    return jours[get_paris_time().weekday()]

def get_market_context():
    """Détermine quels marchés sont ouverts"""
    maintenant = get_paris_time()

    # Weekend = marchés fermés
    if is_weekend():
        return "WEEKEND", "Marchés fermés (weekend). Forex limité."

    heure_decimal = maintenant.hour + maintenant.minute / 60

    if 9 <= heure_decimal < 15.5:
        return "EUROPE", "Marchés européens ouverts. US fermés."
    elif 15.5 <= heure_decimal < 17.5:
        return "EUROPE_US", "Marchés EU et US ouverts (chevauchement)."
    elif 17.5 <= heure_decimal < 22:
        return "US", "Marchés EU fermés. US ouverts."
    else:
        return "FERME", "Marchés principaux fermés. Forex/Commodités 24h."

def get_vix_level():
    """Récupère le niveau actuel du VIX avec cache persistant et fallback robuste"""
    # Vérifier si le cache est encore valide
    if time.time() - config.VIX_CACHE.get('timestamp', 0) < VIX_CACHE_TTL:
        return config.VIX_CACHE['value']

    # Essayer Yahoo Finance d'abord (plus fiable pour VIX)
    try:
        vix = yf.Ticker("^VIX")
        # Méthode 1: fast_info pour le prix live
        try:
            vix_value = float(vix.fast_info['lastPrice'])
            if vix_value > 0:
                config.VIX_CACHE = {'value': vix_value, 'timestamp': time.time(), 'source': 'yahoo_fast'}
                save_vix_cache()
                return vix_value
        except Exception:
            pass
        # Méthode 2: intraday 1m pour données récentes
        try:
            hist_intra = vix.history(period="1d", interval="1m")
            if not hist_intra.empty:
                vix_value = float(hist_intra['Close'].iloc[-1])
                config.VIX_CACHE = {'value': vix_value, 'timestamp': time.time(), 'source': 'yahoo_intraday'}
                save_vix_cache()
                return vix_value
        except Exception:
            pass
        # Méthode 3: fallback daily close (données de la veille)
        hist = vix.history(period="5d")
        if not hist.empty:
            vix_value = float(hist['Close'].iloc[-1])
            config.VIX_CACHE = {'value': vix_value, 'timestamp': time.time(), 'source': 'yahoo_daily'}
            save_vix_cache()
            return vix_value
    except Exception as e:
        logger.warning(f"Erreur récup VIX Yahoo: {e}")

    # Fallback: utiliser Twelve Data si disponible
    try:
        # Lazy import to avoid circular dependency
        from .market_data import recuperer_donnees_marche
        donnees = recuperer_donnees_marche({"^VIX": "VIX"})
        if donnees and 'VIX' in donnees:
            vix_value = donnees['VIX'].get('prix', None)
            if vix_value:
                config.VIX_CACHE = {'value': vix_value, 'timestamp': time.time(), 'source': 'twelvedata'}
                save_vix_cache()
                return vix_value
    except Exception as e:
        logger.warning(f"Erreur récup VIX Twelve Data: {e}")

    # Fallback ultime: utiliser le cache même périmé (mieux que rien)
    if config.VIX_CACHE.get('value'):
        print(f"⚠️ Utilisation cache VIX périmé: {config.VIX_CACHE['value']:.1f}")
        return config.VIX_CACHE['value']

    return 20.0  # Valeur par défaut (normal)

def get_regime_marche(vix_niveau=None):
    """Détermine le régime de marché basé sur le VIX
    Returns: (regime, description, regles_adaptees)
    """
    if vix_niveau is None:
        vix_niveau = get_vix_level()

    if vix_niveau < 15:
        return "CALME", f"VIX à {vix_niveau:.1f} - Marché calme", \
            "Stops serrés (-0.4%), TP ambitieux (+1.2%), ratio 1:3 possible"
    elif vix_niveau < 20:
        return "NORMAL", f"VIX à {vix_niveau:.1f} - Conditions normales", \
            "Paramètres standard: stops -0.5% à -0.8%, TP +0.8% à +1.5%"
    elif vix_niveau < 30:
        return "VOLATILE", f"VIX à {vix_niveau:.1f} - Volatilité élevée", \
            "Stops élargis (-1%), TP conservateur (+1%), moins de trades"
    else:
        return "EXTREME", f"VIX à {vix_niveau:.1f} - Volatilité extrême", \
            "TRÈS SÉLECTIF: uniquement conviction 5, stops -1.5%, éviter les indices"

def get_session_marche():
    """Détermine la session de marché actuelle
    Utilise pytz pour gérer automatiquement le DST US/EU"""
    maintenant = get_paris_time()
    heure = maintenant.hour
    minute = maintenant.minute
    heure_decimal = heure + minute / 60

    # Calculer l'offset US dynamiquement via pytz (gère DST automatiquement)
    # NYSE ouvre à 9:30 ET, ferme à 16:00 ET
    tz_ny = pytz.timezone('America/New_York')
    now_ny = datetime.now(tz_ny)
    now_paris = datetime.now(TZ_PARIS)

    # Différence d'heures entre Paris et NY (normalement 6h, peut être 5h ou 7h pendant transitions DST)
    diff_heures = (now_paris.hour - now_ny.hour) % 24
    if diff_heures > 12:
        diff_heures -= 24

    # Heures US en heure Paris (9:30 ET = 15:30 CET normalement)
    us_open = 9.5 + diff_heures  # 9:30 NY -> 15:30 Paris (si diff=6)
    us_close = 16 + diff_heures  # 16:00 NY -> 22:00 Paris (si diff=6)

    if 9 <= heure_decimal < 11:
        return "EU_OPEN", "Ouverture européenne - Forte activité"
    elif 11 <= heure_decimal < 13:
        return "EU_MID", "Milieu de session EU - Consolidation"
    elif 13 <= heure_decimal < us_open:
        return "US_PREMARKET", "Pré-marché US - Attente données"
    elif us_open <= heure_decimal < 17:
        return "US_OPEN", f"Ouverture US ({us_open:.1f}h Paris) - Chevauchement EU/US"
    elif 17 <= heure_decimal < 17.5:
        return "EU_CLOSE", "Clôture EU - Prise de profits"
    elif 17.5 <= heure_decimal < (us_close - 1):
        return "US_ONLY", "Session US seule - Momentum US"
    elif (us_close - 1) <= heure_decimal < us_close:
        return "US_CLOSE", "Clôture US - Volatilité de fin"
    else:
        return "HORS_SESSION", "Hors heures principales"

def get_pattern_jour_semaine(jour=None):
    """Retourne le pattern historique typique pour un jour de la semaine"""
    if jour is None:
        jour = get_paris_time().weekday()

    patterns = {
        0: {  # Lundi
            "nom": "LUNDI",
            "pattern": "Gaps fréquents à l'open, volatilité matinale, consolidation PM",
            "conseil": "Attendre 30min après l'open, éviter les gaps extrêmes"
        },
        1: {  # Mardi
            "nom": "MARDI",
            "pattern": "Jour le plus actif de la semaine, bon volume",
            "conseil": "Journée idéale pour le day trading, momentum fiable"
        },
        2: {  # Mercredi
            "nom": "MERCREDI",
            "pattern": "Continuation de tendance, attention FOMC (si prévu)",
            "conseil": "Suivre la tendance du mardi, prudence si FOMC"
        },
        3: {  # Jeudi
            "nom": "JEUDI",
            "pattern": "Données emploi US, décisions BCE potentielles",
            "conseil": "Attention aux annonces 14h30, éviter positions avant"
        },
        4: {  # Vendredi
            "nom": "VENDREDI",
            "pattern": "Prises de profits PM, volume décroissant après 16h",
            "conseil": "Clôturer positions avant 16h, éviter overnight"
        }
    }

    return patterns.get(jour, {"nom": "WEEKEND", "pattern": "Marchés fermés", "conseil": "Pas de trading"})

def get_contexte_trading_complet():
    """Retourne le contexte complet pour Claude (VIX, session, jour, etc.)"""
    maintenant = get_paris_time()

    # VIX et régime
    vix_niveau = get_vix_level()
    regime, regime_desc, regles_adaptees = get_regime_marche(vix_niveau)

    # Session
    session, session_desc = get_session_marche()

    # Jour
    pattern_jour = get_pattern_jour_semaine()

    return {
        "datetime": maintenant.strftime("%Y-%m-%d %H:%M:%S CET"),
        "vix_niveau": vix_niveau,
        "regime_marche": regime,
        "regime_description": regime_desc,
        "regles_adaptees": regles_adaptees,
        "session_marche": session,
        "session_description": session_desc,
        "jour_semaine": pattern_jour["nom"],
        "pattern_jour": pattern_jour["pattern"],
        "conseil_jour": pattern_jour["conseil"]
    }

def calculer_trade_grade(trade_data):
    """
    Calcule la note qualité d'un trade (A/B/C/D) basée sur plusieurs critères.

    A = Setup parfait (score >= 85): Tous les feux au vert
    B = Bon setup (score 70-84): La plupart des critères OK
    C = Setup acceptable (score 50-69): Trade risqué mais justifiable
    D = Setup faible (score < 50): Trade de basse qualité

    Critères:
    - Conviction (20 points max)
    - R:R ratio (20 points max)
    - Alignement multi-timeframe (15 points max)
    - Confluence technique (15 points max)
    - Régime marché adapté (10 points max)
    - Session appropriée (10 points max)
    - Sentiment aligné (10 points max)
    """
    score = 0
    details = {}

    # 1. CONVICTION (20 points)
    conviction = trade_data.get('conviction_score', 3)
    conviction_score = conviction * 4  # 1→4, 2→8, 3→12, 4→16, 5→20
    score += conviction_score
    details['conviction'] = {'score': conviction_score, 'max': 20, 'valeur': conviction}

    # 2. RATIO R:R (20 points)
    ratio_rr_str = trade_data.get('ratio_rr', '1:1')
    try:
        parts = ratio_rr_str.replace(' ', '').split(':')
        if len(parts) == 2:
            rr_ratio = float(parts[1]) / float(parts[0])
        else:
            rr_ratio = 1.0
    except (ValueError, ZeroDivisionError, TypeError) as e:
        logger.warning(f"Erreur parsing ratio R:R '{ratio_rr_str}': {e}")
        rr_ratio = 1.0

    if rr_ratio >= 3.0:
        rr_score = 20
    elif rr_ratio >= 2.5:
        rr_score = 18
    elif rr_ratio >= 2.0:
        rr_score = 15
    elif rr_ratio >= 1.5:
        rr_score = 12
    elif rr_ratio >= 1.0:
        rr_score = 8
    else:
        rr_score = 4

    score += rr_score
    details['ratio_rr'] = {'score': rr_score, 'max': 20, 'valeur': ratio_rr_str}

    # 3. ALIGNEMENT MULTI-TIMEFRAME (15 points)
    alignement = trade_data.get('alignement_tf', 0)
    align_score = alignement * 5  # 0→0, 1→5, 2→10, 3→15
    score += align_score
    details['alignement_tf'] = {'score': align_score, 'max': 15, 'valeur': alignement}

    # 4. CONFLUENCE TECHNIQUE (15 points)
    confluence = trade_data.get('confluence_score', 5)
    conf_score = int(confluence * 1.5)  # 0→0, 5→7.5, 10→15
    score += conf_score
    details['confluence'] = {'score': conf_score, 'max': 15, 'valeur': confluence}

    # 5. RÉGIME MARCHÉ ADAPTÉ (10 points)
    regime = trade_data.get('regime_marche', 'NORMAL')
    direction = trade_data.get('direction', 'LONG')

    # En régime EXTREME, seules les positions SHORT ou conviction 5 sont OK
    if regime == 'EXTREME':
        if direction == 'SHORT' or conviction == 5:
            regime_score = 10
        else:
            regime_score = 2
    elif regime == 'VOLATILE':
        if conviction >= 4:
            regime_score = 8
        else:
            regime_score = 5
    elif regime == 'CALME':
        regime_score = 10  # Conditions idéales
    else:  # NORMAL
        regime_score = 8

    score += regime_score
    details['regime'] = {'score': regime_score, 'max': 10, 'valeur': regime}

    # 6. SESSION APPROPRIÉE (10 points)
    session = trade_data.get('session_marche', 'HORS_SESSION')

    session_scores = {
        'EU_OPEN': 10,      # Meilleur moment
        'US_OPEN': 10,      # Très bon
        'EU_MID': 7,        # Acceptable
        'US_ONLY': 7,       # OK
        'US_PREMARKET': 5,  # Risqué (avant données)
        'EU_CLOSE': 4,      # Prises de profits
        'US_CLOSE': 3,      # Volatile fin de journée
        'HORS_SESSION': 2   # Mauvais timing
    }
    session_score = session_scores.get(session, 5)
    score += session_score
    details['session'] = {'score': session_score, 'max': 10, 'valeur': session}

    # 7. SENTIMENT ALIGNÉ (10 points)
    sentiment = trade_data.get('sentiment_score', 0)

    # Sentiment doit être aligné avec la direction
    if direction == 'LONG':
        if sentiment >= 2:
            sent_score = 10
        elif sentiment >= 0:
            sent_score = 6
        elif sentiment >= -2:
            sent_score = 3
        else:
            sent_score = 0
    else:  # SHORT
        if sentiment <= -2:
            sent_score = 10
        elif sentiment <= 0:
            sent_score = 6
        elif sentiment <= 2:
            sent_score = 3
        else:
            sent_score = 0

    score += sent_score
    details['sentiment'] = {'score': sent_score, 'max': 10, 'valeur': sentiment}

    # Calcul du grade final
    if score >= 85:
        grade = 'A'
        grade_label = 'Excellent'
    elif score >= 70:
        grade = 'B'
        grade_label = 'Bon'
    elif score >= 50:
        grade = 'C'
        grade_label = 'Acceptable'
    else:
        grade = 'D'
        grade_label = 'Faible'

    # Score setup (avant exécution)
    setup_score = score

    return {
        'grade': grade,
        'grade_label': grade_label,
        'setup_score': setup_score,
        'details': details,
        'resume': f"{grade} ({setup_score}/100) - {grade_label}"
    }

def calculer_exit_grade(trade):
    """
    Calcule la note de sortie d'un trade après clôture.

    Critères:
    - Résultat vs objectif (40 points)
    - Gestion du drawdown (30 points)
    - Timing de sortie (30 points)
    """
    score = 0
    details = {}

    resultat = trade.get('resultat', 'NON_CONCLU')
    pnl_pct = trade.get('pnl_pct', 0) or 0
    pnl_max = trade.get('pnl_max', 0) or 0
    pnl_min = trade.get('pnl_min', 0) or 0

    # 1. RÉSULTAT VS OBJECTIF (40 points)
    if resultat == 'TP2':
        result_score = 40  # Objectif dépassé
    elif resultat == 'TP1':
        result_score = 35  # Objectif atteint
    elif resultat == 'WIN_FORCE':
        if pnl_pct >= 0.5:
            result_score = 28
        else:
            result_score = 22
    elif resultat == 'BREAKEVEN':
        result_score = 15  # Capital préservé
    elif resultat == 'STOP':
        result_score = 10  # Stop respecté
    elif resultat == 'LOSS_FORCE':
        if pnl_pct >= -0.5:
            result_score = 8
        else:
            result_score = 5
    else:
        result_score = 0

    score += result_score
    details['resultat'] = {'score': result_score, 'max': 40, 'valeur': resultat}

    # 2. GESTION DU DRAWDOWN (30 points)
    # Mesure: combien de % du gain max a été capturé
    if pnl_max > 0:
        if pnl_pct >= pnl_max * 0.8:
            dd_score = 30  # Excellent - gardé 80%+ du max
        elif pnl_pct >= pnl_max * 0.5:
            dd_score = 22  # Bon - gardé 50%+
        elif pnl_pct >= pnl_max * 0.2:
            dd_score = 15  # Passable
        elif pnl_pct >= 0:
            dd_score = 10  # Breakeven après profit
        else:
            dd_score = 5   # Perte après profit
    else:
        # Jamais été en profit
        if pnl_pct >= 0:
            dd_score = 15
        elif pnl_pct >= pnl_min * 0.5:
            dd_score = 12  # Remonté du min
        else:
            dd_score = 8   # Près du pire

    score += dd_score
    details['drawdown'] = {'score': dd_score, 'max': 30, 'pnl_max': pnl_max, 'pnl_pct': pnl_pct}

    # 3. TIMING DE SORTIE (30 points)
    duree = trade.get('duree_minutes', 0) or 0

    # Pour le day trading, durée idéale entre 30 min et 3h
    if 30 <= duree <= 180:
        timing_score = 30  # Durée idéale
    elif 15 <= duree <= 240:
        timing_score = 22  # Acceptable
    elif 5 <= duree <= 360:
        timing_score = 15  # Long/court mais OK
    else:
        timing_score = 8   # Très court ou overnight

    score += timing_score
    details['timing'] = {'score': timing_score, 'max': 30, 'duree_min': duree}

    return {
        'exit_score': score,
        'details': details
    }

# ============================================================================
# CALENDRIER MACRO & ÉVÉNEMENTS
# ============================================================================

# Événements macro récurrents majeurs (heure CET)
EVENEMENTS_MACRO_RECURRENTS = {
    # Chaque mois, jour approximatif
    "NFP": {"jour_semaine": 4, "semaine": 1, "heure": "14:30", "importance": 3},  # 1er vendredi
    "CPI_US": {"jour_mois": [10, 11, 12, 13], "heure": "14:30", "importance": 3},
    "FOMC": {"heures": ["20:00"], "importance": 3},  # Vérifié via API
    "BCE": {"heures": ["14:15", "14:45"], "importance": 3},
}

def get_evenements_macro_jour():
    """Récupère les événements macro du jour (basé sur le calendrier économique)
    IMPORTANT: Retourne une liste VIDE le weekend (pas de marchés ouverts)"""
    maintenant = get_paris_time()

    # Weekend: pas d'événements économiques (marchés fermés)
    if is_weekend():
        return []

    aujourdhui = maintenant.date()
    jour_semaine = maintenant.weekday()  # 0=Lundi, 4=Vendredi
    jour_mois = maintenant.day

    evenements = []

    # Vérifier NFP (1er vendredi du mois)
    if jour_semaine == 4:  # Vendredi
        premier_jour = maintenant.replace(day=1)
        premier_vendredi = 1 + (4 - premier_jour.weekday()) % 7
        if jour_mois == premier_vendredi:
            evenements.append({
                "nom": "NFP (Non-Farm Payrolls)",
                "heure": "14:30",
                "importance": 3,
                "impact": "MAJEUR - Très forte volatilité USD et indices"
            })

    # Vérifier CPI US (généralement entre le 10 et 15 du mois, jours ouvrés)
    if 10 <= jour_mois <= 15:
        evenements.append({
            "nom": "CPI US (potentiel)",
            "heure": "14:30",
            "importance": 2,
            "impact": "FORT - Volatilité sur USD, Or, Indices"
        })

    # ISM/PMI Services: 1er jour ouvré du mois uniquement
    if jour_mois <= 3:  # Dans les 3 premiers jours du mois
        # Calculer le 1er jour ouvré du mois
        premier_jour = maintenant.replace(day=1)
        premier_jour_ouvre = premier_jour
        while premier_jour_ouvre.weekday() >= 5:  # Skip weekend
            premier_jour_ouvre += timedelta(days=1)
        if maintenant.date() == premier_jour_ouvre.date():
            evenements.append({
                "nom": "ISM/PMI Services",
                "heure": "16:00",
                "importance": 2,
                "impact": "Volatilité modérée - indicateur économique clé"
            })

    # Stocks pétrole EIA: Mercredi uniquement
    if jour_semaine == 2:  # Mercredi
        evenements.append({
            "nom": "Stocks pétrole EIA",
            "heure": "16:30",
            "importance": 2,
            "impact": "Volatilité sur WTI/Brent"
        })

    return evenements

def verifier_proximite_evenement_macro(minutes_avant=30):
    """
    Vérifie si un événement macro majeur est imminent.
    Retourne (True, evenement) si on est à moins de X minutes d'un événement important.
    """
    maintenant = get_paris_time()
    evenements = get_evenements_macro_jour()

    for evt in evenements:
        if evt.get("importance", 1) >= 2:  # Événement important
            try:
                heure_evt = evt["heure"]
                h, m = map(int, heure_evt.split(":"))
                heure_evenement = maintenant.replace(hour=h, minute=m, second=0, microsecond=0)

                # Calculer la différence en minutes
                diff = (heure_evenement - maintenant).total_seconds() / 60

                # Si l'événement est dans les X prochaines minutes
                if 0 <= diff <= minutes_avant:
                    return True, evt
            except (ValueError, KeyError):
                continue

    return False, None

def get_actifs_filtres_atr(donnees_marche, seuil_atr_min=1.0):
    """
    Filtre les actifs avec un ATR trop faible pour le day trading.
    Retourne la liste des actifs à éviter.
    """
    actifs_faible_atr = []
    for nom, data in donnees_marche.items():
        atr_pct = data.get("atr_pct", 0)
        if atr_pct > 0 and atr_pct < seuil_atr_min:
            actifs_faible_atr.append({
                "nom": nom,
                "symbole": data.get("symbole"),
                "atr_pct": atr_pct
            })
    return actifs_faible_atr


