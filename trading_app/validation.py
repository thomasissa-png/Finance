"""
Validation: extraction JSON Claude, validation opportunités, setup trades.
"""
import re
import json
import time
import pytz
from datetime import datetime

from .config import logger, TWELVEDATA_API_KEY, to_python_type, TZ_PARIS
from .constants import ACTIFS_PERMANENTS, POOL_ROTATION, SYMBOLES_YAHOO_FALLBACK
from .market_context import get_paris_time, get_session_marche
from .market_data import (
    convert_symbol_to_twelvedata, rate_limit_twelvedata,
    activate_quota_circuit_breaker, get_fresh_quote_for_trade
)

def extraire_json_claude(reponse):
    """
    Extrait et valide le JSON de la réponse Claude de manière robuste.

    Problème résolu: la regex \{[\s\S]*\} capture tout entre le premier
    et dernier {}, ce qui peut inclure du texte invalide.

    Retourne: (dict, erreur) - dict si succès, None + message si échec
    """
    if not reponse:
        return None, "Réponse vide"

    # Nettoyer la réponse (enlever markdown code blocks si présents)
    reponse_clean = reponse.strip()
    if reponse_clean.startswith('```json'):
        reponse_clean = reponse_clean[7:]
    if reponse_clean.startswith('```'):
        reponse_clean = reponse_clean[3:]
    if reponse_clean.endswith('```'):
        reponse_clean = reponse_clean[:-3]
    reponse_clean = reponse_clean.strip()

    # Essai 1: Parser directement si c'est du JSON pur
    if reponse_clean.startswith('{'):
        try:
            # Trouver la fin du JSON en comptant les accolades
            depth = 0
            end_idx = 0
            for i, char in enumerate(reponse_clean):
                if char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                    if depth == 0:
                        end_idx = i + 1
                        break

            if end_idx > 0:
                json_str = reponse_clean[:end_idx]
                result = json.loads(json_str)
                return result, None
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error (essai 1): {e}")

    # Essai 2: Chercher un bloc JSON dans le texte
    try:
        # Trouver le premier { et son } correspondant
        start_idx = reponse_clean.find('{')
        if start_idx == -1:
            return None, "Aucun JSON trouvé dans la réponse"

        depth = 0
        end_idx = 0
        for i in range(start_idx, len(reponse_clean)):
            char = reponse_clean[i]
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    end_idx = i + 1
                    break

        if end_idx > start_idx:
            json_str = reponse_clean[start_idx:end_idx]
            result = json.loads(json_str)
            return result, None
    except json.JSONDecodeError as e:
        return None, f"JSON invalide: {e}"

    return None, "Impossible d'extraire le JSON"


def valider_structure_analyse(result):
    """
    Valide que le JSON d'analyse contient tous les champs requis.

    Retourne: (bool, list_erreurs)
    """
    erreurs = []

    # Champs obligatoires niveau racine
    champs_requis = ['contexte_marche', 'opportunites']
    for champ in champs_requis:
        if champ not in result:
            erreurs.append(f"Champ manquant: {champ}")

    # Vérifier que opportunites est une liste
    if 'opportunites' in result:
        if not isinstance(result['opportunites'], list):
            erreurs.append("'opportunites' doit être une liste")

    return len(erreurs) == 0, erreurs


def valider_opportunite(opp):
    """
    Valide une opportunité individuelle de manière stricte.

    Retourne: (bool valide, dict opp_corrigee, list avertissements, list erreurs_bloquantes)
    """
    avertissements = []
    erreurs = []

    # Champs obligatoires
    champs_requis = ['entree', 'stop', 'tp1', 'direction']
    for champ in champs_requis:
        if champ not in opp or opp[champ] is None:
            erreurs.append(f"Champ obligatoire manquant: {champ}")

    if erreurs:
        return False, None, avertissements, erreurs

    # Conversion et validation des prix
    try:
        entree = float(opp.get('entree', 0) or 0)
        stop = float(opp.get('stop', 0) or 0)
        tp1 = float(opp.get('tp1', 0) or 0)
        tp2 = float(opp.get('tp2', 0) or 0)
    except (ValueError, TypeError) as e:
        erreurs.append(f"Conversion prix impossible: {e}")
        return False, None, avertissements, erreurs

    # Validation prix > 0
    if entree <= 0:
        erreurs.append(f"Prix entrée invalide: {entree}")
    if stop <= 0:
        erreurs.append(f"Prix stop invalide: {stop}")
    if tp1 <= 0:
        erreurs.append(f"Prix TP1 invalide: {tp1}")

    if erreurs:
        return False, None, avertissements, erreurs

    # Validation direction
    direction = str(opp.get('direction', '')).upper().strip()
    if direction not in ['LONG', 'SHORT']:
        erreurs.append(f"Direction invalide: '{opp.get('direction')}' (attendu: LONG ou SHORT)")
        return False, None, avertissements, erreurs

    # Validation cohérence STOP/ENTREE/TP (REJET au lieu d'auto-correction)
    if direction == 'LONG':
        if stop >= entree:
            erreurs.append(f"LONG incohérent: stop ({stop}) >= entrée ({entree})")
        if tp1 <= entree:
            erreurs.append(f"LONG incohérent: TP1 ({tp1}) <= entrée ({entree})")
        if tp2 > 0 and tp2 <= tp1:
            avertissements.append(f"TP2 ({tp2}) <= TP1 ({tp1}), TP2 ignoré")
    else:  # SHORT
        if stop <= entree:
            erreurs.append(f"SHORT incohérent: stop ({stop}) <= entrée ({entree})")
        if tp1 >= entree:
            erreurs.append(f"SHORT incohérent: TP1 ({tp1}) >= entrée ({entree})")
        if tp2 > 0 and tp2 >= tp1:
            avertissements.append(f"TP2 ({tp2}) >= TP1 ({tp1}), TP2 ignoré")

    if erreurs:
        return False, None, avertissements, erreurs

    # Calcul et validation du ratio R:R
    if direction == 'LONG':
        risque = entree - stop
        reward = tp1 - entree
    else:
        risque = stop - entree
        reward = entree - tp1

    if risque <= 0:
        erreurs.append(f"Risque invalide: {risque}")
        return False, None, avertissements, erreurs

    ratio_rr = reward / risque

    # R:R minimum adapté au régime de marché (VIX)
    regime_marche = opp.get('regime_marche', 'NORMAL')
    rr_min_par_regime = {
        'CALME': 1.3,      # VIX < 15: marché calme, R:R plus souple
        'NORMAL': 1.5,     # VIX 15-20: conditions standard
        'VOLATILE': 1.8,   # VIX 20-30: exiger plus de reward pour le risque
        'EXTREME': 2.0     # VIX > 30: très sélectif
    }
    rr_minimum = rr_min_par_regime.get(regime_marche, 1.5)

    if ratio_rr < rr_minimum:
        erreurs.append(f"Ratio R:R insuffisant: {ratio_rr:.2f} (minimum {rr_minimum} en régime {regime_marche})")
        return False, None, avertissements, erreurs

    # === VALIDATION OBJECTIF 0.7% ===
    # Calculer le gain cible en pourcentage
    if direction == 'LONG':
        gain_cible_pct = ((tp1 - entree) / entree) * 100
    else:
        gain_cible_pct = ((entree - tp1) / entree) * 100

    # Avertir si gain cible < 0.7% (objectif minimum day trading)
    if gain_cible_pct < 0.7:
        avertissements.append(f"Gain cible faible: {gain_cible_pct:.2f}% < 0.7% objectif")

    # Validation ATR: le gain cible ne doit pas dépasser 1.5x l'ATR daily
    # (sinon le TP est irréaliste pour la volatilité de l'actif)
    atr_pct = opp.get('atr_pct', 0)
    if atr_pct and atr_pct > 0:
        atr_max_gain = atr_pct * 1.5  # Max 1.5x ATR daily réaliste en intraday
        if gain_cible_pct > atr_max_gain:
            avertissements.append(f"TP ambitieux: {gain_cible_pct:.2f}% > {atr_max_gain:.2f}% (1.5x ATR)")

    # Note: Pas de validation volume - trading via turbos (liquidité assurée par market maker)

    # === VALIDATION HEURES DE MARCHÉ ===
    # Avertir si le marché n'est pas ouvert (trades à planifier, pas exécuter immédiatement)
    symbole = opp.get('symbole') or opp.get('nom', '')
    if symbole:
        maintenant = get_paris_time()
        heure_decimal = maintenant.hour + maintenant.minute / 60
        jour_semaine = maintenant.weekday()  # 0=Lundi, 6=Dimanche

        # Weekend: tous les marchés fermés (sauf crypto si implémenté plus tard)
        if jour_semaine >= 5:
            avertissements.append(f"Weekend: marchés fermés, trade à exécuter lundi")
        else:
            # Déterminer le type de marché
            # Lazy import to avoid circular dependency
            from .journal import get_categorie_actif
            categorie = get_categorie_actif(symbole)

            if categorie == 'action_eu' or symbole in ['^FCHI', '^GDAXI', '^STOXX50E']:
                # Euronext/Xetra: 9h00-17h30 Paris
                if heure_decimal < 9 or heure_decimal >= 17.5:
                    if heure_decimal >= 8:
                        avertissements.append(f"Marché EU pré-ouverture: ouverture à 9h00")
                    elif heure_decimal >= 17.5:
                        avertissements.append(f"Marché EU fermé: réouverture demain 9h00")
                    else:
                        avertissements.append(f"Marché EU fermé (nuit)")
                elif heure_decimal >= 17:
                    avertissements.append(f"Marché EU bientôt fermé: 30min restantes")

            elif categorie == 'action_us' or symbole in ['^GSPC', '^IXIC', '^DJI']:
                # NYSE/NASDAQ: 15h30-22h00 Paris (9:30-16:00 ET, +6h normalement)
                # Calculer dynamiquement avec pytz
                tz_ny = pytz.timezone('America/New_York')
                now_ny = datetime.now(tz_ny)
                now_paris = datetime.now(TZ_PARIS)
                diff_heures = (now_paris.hour - now_ny.hour) % 24
                if diff_heures > 12:
                    diff_heures -= 24

                us_open = 9.5 + diff_heures  # 9:30 NY en heure Paris
                us_close = 16 + diff_heures  # 16:00 NY en heure Paris

                if heure_decimal < us_open or heure_decimal >= us_close:
                    if heure_decimal >= (us_open - 1.5) and heure_decimal < us_open:
                        avertissements.append(f"Marché US pré-ouverture: ouverture à {us_open:.0f}h30 Paris")
                    elif heure_decimal >= us_close:
                        avertissements.append(f"Marché US fermé: réouverture demain {us_open:.0f}h30 Paris")
                    else:
                        avertissements.append(f"Marché US fermé (hors heures)")
                elif heure_decimal >= (us_close - 0.5):
                    avertissements.append(f"Marché US bientôt fermé: 30min restantes")

            elif categorie == 'forex':
                # Forex: 24h du dimanche 23h au vendredi 22h Paris
                # Dimanche: fermé avant 23h
                if jour_semaine == 6:  # Dimanche
                    if heure_decimal < 23:
                        avertissements.append(f"Forex fermé jusqu'à 23h (ouverture Sydney)")
                # Vendredi: fermeture à 22h
                elif jour_semaine == 4 and heure_decimal >= 21:
                    avertissements.append(f"Forex fermeture imminente vendredi 22h")

            # Commodités/Futures: horaires variables, pas de warning systématique
            # mais attention aux heures de faible liquidité
            elif categorie == 'commodite' and symbole.endswith('=F'):
                if heure_decimal < 8 or heure_decimal >= 22:
                    avertissements.append(f"Futures: liquidité réduite hors heures principales")

    # Opportunité valide - construire la version corrigée
    opp_valide = opp.copy()
    opp_valide['direction'] = direction
    opp_valide['ratio_rr_calcule'] = round(ratio_rr, 2)
    opp_valide['rr_minimum_applique'] = rr_minimum
    opp_valide['gain_cible_pct'] = round(gain_cible_pct, 2)

    return True, opp_valide, avertissements, []


def analyser_cloture_intraday(hist, direction, entree, stop, tp1, tp2):
    """
    Analyse la clôture d'un trade en tenant compte:
    - De l'ordre chronologique des bougies
    - Du slippage potentiel (prix réel vs niveau théorique)
    - De la priorité temporelle intra-bougie

    Retourne: (resultat, prix_sortie, pnl_pct, bougie_idx) ou (None, None, None, None)
    """
    if hist is None or hist.empty:
        return None, None, None, None

    # Protection division par zéro
    if entree <= 0:
        return None, None, None, None

    # S'assurer que les bougies sont triées chronologiquement
    hist = hist.sort_index()

    for idx, row in hist.iterrows():
        high = row['High']
        low = row['Low']
        open_p = row['Open']
        close_p = row['Close']

        if direction == 'LONG':
            # Pour un LONG: Stop si Low <= stop, TP si High >= tp
            stop_touche = stop > 0 and low <= stop
            tp2_touche = tp2 > 0 and high >= tp2
            tp1_touche = tp1 > 0 and high >= tp1

            # Déterminer l'ordre probable intra-bougie
            # Si les deux sont touchés dans la même bougie, utiliser Open->Close
            if stop_touche and (tp1_touche or tp2_touche):
                # Heuristique: si Close > Open, le mouvement principal était haussier
                # donc TP probablement touché avant le dump final
                if close_p > open_p:
                    # Mouvement haussier: TP probablement d'abord
                    if tp2_touche:
                        return 'TP2', tp2, ((tp2 - entree) / entree) * 100, idx
                    return 'TP1', tp1, ((tp1 - entree) / entree) * 100, idx
                else:
                    # Mouvement baissier: Stop probablement d'abord
                    # Slippage: utiliser le Low réel si < stop
                    prix_sortie_reel = min(low, stop)
                    return 'STOP', prix_sortie_reel, ((prix_sortie_reel - entree) / entree) * 100, idx

            # Un seul niveau touché
            if stop_touche:
                prix_sortie_reel = min(low, stop)  # Slippage potentiel
                return 'STOP', prix_sortie_reel, ((prix_sortie_reel - entree) / entree) * 100, idx
            if tp2_touche:
                return 'TP2', tp2, ((tp2 - entree) / entree) * 100, idx
            if tp1_touche:
                return 'TP1', tp1, ((tp1 - entree) / entree) * 100, idx

        else:  # SHORT
            stop_touche = stop > 0 and high >= stop
            tp2_touche = tp2 > 0 and low <= tp2
            tp1_touche = tp1 > 0 and low <= tp1

            if stop_touche and (tp1_touche or tp2_touche):
                if close_p < open_p:
                    # Mouvement baissier: TP probablement d'abord
                    if tp2_touche:
                        return 'TP2', tp2, ((entree - tp2) / entree) * 100, idx
                    return 'TP1', tp1, ((entree - tp1) / entree) * 100, idx
                else:
                    # Mouvement haussier: Stop probablement d'abord
                    prix_sortie_reel = max(high, stop)
                    return 'STOP', prix_sortie_reel, ((entree - prix_sortie_reel) / entree) * 100, idx

            if stop_touche:
                prix_sortie_reel = max(high, stop)  # Slippage potentiel
                return 'STOP', prix_sortie_reel, ((entree - prix_sortie_reel) / entree) * 100, idx
            if tp2_touche:
                return 'TP2', tp2, ((entree - tp2) / entree) * 100, idx
            if tp1_touche:
                return 'TP1', tp1, ((entree - tp1) / entree) * 100, idx

    return None, None, None, None



def valider_setup_avant_trade(symbole, direction, prix_entree_prevu, donnees_marche=None):
    """
    Valide qu'un setup de trade est toujours pertinent avant exécution.

    Vérifie:
    - Le prix actuel vs prix prévu (pas trop d'écart, dynamique selon actif)
    - La direction du mouvement intraday
    - Le volume (liquidité suffisante, sauf indices)
    - La volatilité (spread raisonnable)

    Args:
        symbole: Symbole de l'actif
        direction: 'LONG' ou 'SHORT'
        prix_entree_prevu: Prix auquel Claude a recommandé d'entrer
        donnees_marche: Données du cache (optionnel, pour ATR)

    Returns:
        (bool valide, str raison, dict quote_fraiche)
    """
    quote = get_fresh_quote_for_trade(symbole)

    if not quote:
        return False, "Impossible d'obtenir un prix frais", None

    prix_actuel = quote['prix']

    # Protection division par zéro
    if prix_entree_prevu <= 0:
        return False, f"Prix d'entrée prévu invalide ({prix_entree_prevu})", None

    ecart_pct = abs((prix_actuel - prix_entree_prevu) / prix_entree_prevu * 100)

    # Écart max DYNAMIQUE selon le type d'actif
    # Commodités très volatiles: tolérance plus élevée
    COMMODITES_VOLATILES = {"NG=F", "CC=F", "KC=F", "SI=F", "PL=F", "ZW=F", "ZC=F", "ZS=F", "SB=F"}
    COMMODITES_STANDARD = {"GC=F", "BZ=F", "CL=F", "HG=F"}

    if symbole in COMMODITES_VOLATILES:
        MAX_ECART_PCT = 2.5  # Gaz naturel, cacao, café très volatils
        MAX_SPREAD_PCT = 5.0
    elif symbole in COMMODITES_STANDARD:
        MAX_ECART_PCT = 1.5  # Or, pétrole moyennement volatils
        MAX_SPREAD_PCT = 4.0
    elif symbole.endswith("=X"):  # Forex
        MAX_ECART_PCT = 0.5  # Forex très stable
        MAX_SPREAD_PCT = 1.5
    else:
        MAX_ECART_PCT = 1.0  # Actions et indices: standard
        MAX_SPREAD_PCT = 3.0

    # 1. Vérifier l'écart de prix
    if ecart_pct > MAX_ECART_PCT:
        return False, f"Prix a bougé de {ecart_pct:.1f}% (max {MAX_ECART_PCT}%)", quote

    # 2. Vérifier le spread (volatilité excessive)
    if quote['spread_pct'] > MAX_SPREAD_PCT:
        return False, f"Spread trop large: {quote['spread_pct']:.1f}% (max {MAX_SPREAD_PCT}%)", quote

    # 3. Vérifier la cohérence direction/mouvement
    if direction == 'LONG' and quote['variation_open'] < -2.0:
        return False, f"Momentum baissier depuis open ({quote['variation_open']:.1f}%)", quote
    if direction == 'SHORT' and quote['variation_open'] > 2.0:
        return False, f"Momentum haussier depuis open ({quote['variation_open']:.1f}%)", quote

    # 4. Vérifier le volume (si disponible)
    # Exception: indices via Yahoo Finance peuvent avoir volume=0 (données agrégées)
    if quote['volume'] == 0 and symbole not in SYMBOLES_YAHOO_FALLBACK:
        return False, "Volume nul - possible problème de données", quote

    return True, "Setup validé", quote


