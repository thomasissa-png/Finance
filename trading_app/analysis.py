"""
Analyse de marché via Claude AI: analyse marché, news, clôture.
"""
import os
import json
import sqlite3
import time
from datetime import datetime, timedelta

import requests

from .config import (
    client_anthropic, NEWSAPI_KEY, NEWS_CACHE, NEWS_CACHE_TTL,
    logger, DB_PATH, DB_TIMEOUT, TZ_PARIS
)
from . import config
from .constants import ACTIFS_PERMANENTS
from .market_context import (
    get_paris_time, get_market_context, get_vix_level, get_regime_marche,
    get_session_marche, get_pattern_jour_semaine, get_contexte_trading_complet,
    get_evenements_macro_jour, get_actifs_filtres_atr, verifier_proximite_evenement_macro
)
from .market_data import recuperer_donnees_marche
from .indicators import enrichir_donnees_avec_indicateurs
from .validation import extraire_json_claude, valider_structure_analyse
from .prompts import SYSTEM_PROMPT, SYSTEM_PROMPT_NEWS_ANALYSIS, SYSTEM_PROMPT_CLOTURE
from .adjustments import generer_instructions_dynamiques, get_criteres_dynamiques
from .ab_testing import get_instructions_ab_testing
from .metrics import generer_contexte_metriques_pour_prompt

def analyser_news_trading(headlines):
    """Analyse les headlines d'actualités pour identifier les impacts trading"""
    if not headlines:
        return []

    maintenant = get_paris_time()

    # Formater les headlines pour Claude
    headlines_text = "\n".join([
        f"- [{h.get('source', 'Unknown')}] {h.get('titre', h.get('title', ''))}"
        for h in headlines[:15]  # Max 15 headlines à analyser
    ])

    question = f"""Analyse ces actualités du {maintenant.strftime('%d/%m/%Y')} et identifie celles avec un impact trading:

{headlines_text}

Retourne UNIQUEMENT les news pertinentes pour le trading en JSON."""

    try:
        message = client_anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            system=SYSTEM_PROMPT_NEWS_ANALYSIS,
            messages=[{"role": "user", "content": question}]
        )

        reponse = message.content[0].text
        result, erreur = extraire_json_claude(reponse)

        if erreur:
            logger.warning(f"Extraction JSON news échouée: {erreur}")
            return []

        return result.get('news_analysees', [])
    except Exception as e:
        print(f"⚠️ Erreur analyse news: {e}")
        return []

def fetch_and_analyze_news():
    """Récupère les news via NewsAPI et les analyse pour l'impact trading"""
    if not NEWSAPI_KEY:
        return [], "NEWSAPI_KEY non configurée"

    try:
        # 1. Récupérer les news brutes
        articles = []

        # News business générales
        url = "https://newsapi.org/v2/top-headlines"
        params = {
            'apiKey': NEWSAPI_KEY,
            'category': 'business',
            'language': 'fr',
            'pageSize': 15
        }
        response = requests.get(url, params=params, timeout=10)
        if not response.ok:
            logger.warning(f"NewsAPI HTTP {response.status_code}")
            return None
        data = response.json()

        if data.get('status') == 'ok':
            for article in data.get('articles', []):
                articles.append({
                    'titre': article.get('title', ''),
                    'source': article.get('source', {}).get('name', 'Inconnu'),
                    'heure': article.get('publishedAt', ''),
                    'url': article.get('url', '')
                })

        # News internationales (pour commodités, géopolitique)
        url_world = "https://newsapi.org/v2/top-headlines"
        params_world = {
            'apiKey': NEWSAPI_KEY,
            'category': 'general',
            'language': 'en',
            'pageSize': 10
        }
        response_world = requests.get(url_world, params=params_world, timeout=10)
        data_world = response_world.json()

        if data_world.get('status') == 'ok':
            for article in data_world.get('articles', []):
                articles.append({
                    'titre': article.get('title', ''),
                    'source': article.get('source', {}).get('name', 'Inconnu'),
                    'heure': article.get('publishedAt', ''),
                    'url': article.get('url', '')
                })

        if not articles:
            return [], "Aucune news récupérée"

        # 2. Analyser les news avec Claude
        news_analysees = analyser_news_trading(articles)

        # 3. Enrichir avec l'heure formatée
        for news in news_analysees:
            headline = news.get('headline', '').lower()
            best_match = None
            best_score = 0

            # Trouver l'article original avec correspondance flexible
            for article in articles:
                titre = article.get('titre', '').lower()
                # Vérifier plusieurs types de correspondance
                if titre in headline or headline in titre:
                    best_match = article
                    break
                # Correspondance par mots-clés significatifs (>5 caractères)
                titre_mots = set(m for m in titre.split() if len(m) > 5)
                headline_mots = set(m for m in headline.split() if len(m) > 5)
                score = len(titre_mots & headline_mots)
                if score > best_score:
                    best_score = score
                    best_match = article

            if best_match and best_match.get('heure'):
                try:
                    dt = datetime.fromisoformat(best_match['heure'].replace('Z', '+00:00'))
                    dt_paris = dt.astimezone(TZ_PARIS)
                    news['heure'] = dt_paris.strftime('%H:%M')
                except ValueError:
                    news['heure'] = get_paris_time().strftime('%H:%M')
            else:
                # Utiliser l'heure courante si pas de correspondance
                news['heure'] = get_paris_time().strftime('%H:%M')

        # 4. Sauvegarder les news analysées en base
        sauvegarder_news_analysees(news_analysees)

        return news_analysees, None

    except Exception as e:
        print(f"⚠️ Erreur fetch_and_analyze_news: {e}")
        return [], str(e)

def sauvegarder_news_analysees(news_list):
    """Sauvegarde les news analysées dans la table alertes_news"""
    if not news_list:
        return

    maintenant = get_paris_time()
    aujourdhui = maintenant.date()

    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        cursor = conn.cursor()

        for news in news_list:
            # Vérifier si la news existe déjà (par titre)
            cursor.execute('''
                SELECT id FROM alertes_news WHERE titre = ? AND date = ?
            ''', (news.get('headline', '')[:200], aujourdhui))

            if cursor.fetchone() is None:
                # Insérer la nouvelle news
                # Note: Le JSON de Claude utilise 'actifs', pas 'actifs_concernes'
                actifs_list = news.get('actifs', news.get('actifs_concernes', []))
                actifs_concernes = ', '.join([a.get('symbole', '') for a in actifs_list])
                cursor.execute('''
                    INSERT INTO alertes_news (date, heure, titre, contenu, actif, impact, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    aujourdhui,
                    news.get('heure', '--:--'),
                    news.get('headline', '')[:200],
                    news.get('analyse', ''),
                    actifs_concernes,
                    news.get('impact', 'faible'),
                    maintenant
                ))

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ Erreur sauvegarde news: {e}")

def get_news_historique(jours=7):
    """Récupère l'historique des news analysées"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        date_limite = get_paris_time().date() - timedelta(days=jours)

        cursor.execute('''
            SELECT * FROM alertes_news
            WHERE date >= ?
            ORDER BY date DESC, timestamp DESC
        ''', (date_limite,))

        news = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return news
    except Exception as e:
        print(f"⚠️ Erreur historique news: {e}")
        return []

def analyser_marche_json(donnees):
    """Analyse le marché et retourne un JSON structuré"""
    maintenant = get_paris_time()
    heure_str = maintenant.strftime('%H:%M')

    marche_type, marche_info = get_market_context()

    # Vérifier les événements macro imminents
    evt_imminent, evt_details = verifier_proximite_evenement_macro(minutes_avant=30)
    alerte_macro = None
    if evt_imminent:
        alerte_macro = f"⚠️ ATTENTION: {evt_details['nom']} dans moins de 30 min ({evt_details['heure']}). {evt_details.get('impact', '')}"

    # Récupérer les événements macro du jour
    evenements_macro = get_evenements_macro_jour()

    # Identifier les actifs à faible ATR
    actifs_faible_atr = get_actifs_filtres_atr(donnees, seuil_atr_min=1.0)
    liste_exclus = [a['nom'] for a in actifs_faible_atr]

    # Enrichir les données avec les indicateurs RSI/MACD calculés (si pas déjà fait)
    if 'indicateurs_calcules' in donnees:
        donnees_enrichies = donnees  # Déjà enrichies
    else:
        donnees_enrichies = enrichir_donnees_avec_indicateurs(donnees)

    # Formater les données pour le prompt
    donnees_texte = json.dumps(donnees_enrichies, ensure_ascii=False, indent=2)

    # Construire le contexte enrichi
    contexte_macro = ""
    if alerte_macro:
        contexte_macro = f"\n\n🚨 ALERTE MACRO: {alerte_macro}"
    if evenements_macro:
        contexte_macro += f"\n\nÉVÉNEMENTS MACRO DU JOUR:\n"
        for evt in evenements_macro:
            contexte_macro += f"- {evt['heure']}: {evt['nom']} (importance {evt['importance']}/3)\n"

    exclusions_atr = ""
    if liste_exclus:
        exclusions_atr = f"\n\nACTIFS À EXCLURE (ATR < 1%):\n{', '.join(liste_exclus)}"

    # Récupérer les critères dynamiques
    criteres = get_criteres_dynamiques()
    contexte_criteres = ""
    if criteres.get('scores_confiance'):
        contexte_criteres = "\n\nSCORES DE CONFIANCE (basés sur performances passées):\n"
        for cat, data in criteres['scores_confiance'].items():
            if isinstance(data, dict):
                score = data.get('score', 50)
                emoji = "🟢" if score >= 60 else "🟡" if score >= 40 else "🔴"
                contexte_criteres += f"- {cat}: {emoji} {score}/100\n"

    if criteres.get('ajustements_recents'):
        contexte_criteres += "\nAJUSTEMENTS RÉCENTS:\n"
        for aj in criteres['ajustements_recents'][:3]:
            contexte_criteres += f"- {aj.get('critere', '')}: {aj.get('raison', '')}\n"

    # === NOUVEAU: Contexte trading avancé (VIX, session, saisonnalité) ===
    contexte_avance = get_contexte_trading_complet()
    contexte_regime = f"""
=== CONTEXTE TRADING AVANCÉ ===
📊 VIX: {contexte_avance['vix_niveau']:.1f} → Régime: {contexte_avance['regime_marche']}
   {contexte_avance['regime_description']}
   Règles adaptées: {contexte_avance['regles_adaptees']}

⏰ Session: {contexte_avance['session_marche']} - {contexte_avance['session_description']}

📅 Jour: {contexte_avance['jour_semaine']}
   Pattern: {contexte_avance['pattern_jour']}
   Conseil: {contexte_avance['conseil_jour']}
"""

    # Contexte métriques de performance (auto-correction)
    contexte_metriques = generer_contexte_metriques_pour_prompt(jours=14)

    question = f"""DONNÉES MARCHÉ EN TEMPS RÉEL (avec ATR%, Volume Relatif, RSI daily + intraday, MACD, Pivot/Support/Résistance):
{donnees_texte}

Heure: {maintenant.strftime('%d/%m/%Y %H:%M')} CET
Contexte: {marche_type} - {marche_info}
{contexte_regime}
{contexte_macro}
{exclusions_atr}
{contexte_criteres}
{contexte_metriques}

Analyse le marché et fournis une réponse JSON structurée selon le format demandé.
RÈGLES IMPÉRATIVES:
- EXCLUS les actifs listés avec ATR < 1%
- Si événement macro imminent, mentionne-le dans alerte_macro
- PRIVILÉGIE les catégories avec score de confiance élevé (>60)
- ÉVITE les catégories avec score faible (<40)
- AU MOINS 1 opportunité NEWS TRADING (si conditions favorables)
- Liste UNIQUEMENT les événements APRÈS {heure_str}
- UTILISE les indicateurs RSI (daily ET intraday si disponible) et MACD fournis pour confirmer tes trades
- RSI_intraday est le RSI 5min (plus réactif pour le scalping) - PRIORITAIRE sur le RSI daily pour les entrées
- UTILISE les niveaux support1/resistance1 pour placer stop/TP intelligemment
- RATIO R/R MINIMUM 1:1.5 - justifie ton choix dans ratio_rr_justification
- ADAPTE tes règles au RÉGIME DE MARCHÉ indiqué (VIX)
- INDIQUE pour chaque opportunité: conviction_score (1-5), strategie_entree, validite_minutes, sentiment_score
- REMPLIS tous les champs multi-timeframe: trend_daily, trend_h4, trend_h1, alignement_tf
- Pour chaque opportunité: symbole, atr_pct, volume_relatif, rsi, macd_signal, ratio_rr, ratio_rr_justification"""

    try:
        # Générer le prompt système avec les instructions dynamiques et A/B testing
        instructions_dyn = generer_instructions_dynamiques()
        instructions_ab = get_instructions_ab_testing()
        system_prompt_complet = SYSTEM_PROMPT
        if instructions_dyn:
            system_prompt_complet += "\n\n" + instructions_dyn
        if instructions_ab:
            system_prompt_complet += "\n\n" + instructions_ab

        message = client_anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            system=system_prompt_complet,
            messages=[{"role": "user", "content": question}]
        )

        reponse = message.content[0].text

        # Extraire le JSON avec la nouvelle fonction robuste
        result, erreur = extraire_json_claude(reponse)
        if erreur:
            logger.error(f"Extraction JSON échouée: {erreur}")
            return None

        # Valider la structure
        valide, erreurs_structure = valider_structure_analyse(result)
        if not valide:
            logger.error(f"Structure JSON invalide: {erreurs_structure}")
            return None

        # Ajouter l'alerte macro si présente
        if alerte_macro and not result.get('alerte_macro'):
            result['alerte_macro'] = alerte_macro

        return result
    except Exception as e:
        logger.error(f"Erreur analyse: {e}")
        return None

def generer_cloture_json(donnees):
    """Génère l'analyse de clôture en JSON"""
    maintenant = get_paris_time()
    demain = maintenant + timedelta(days=1)

    donnees_texte = json.dumps(donnees, ensure_ascii=False, indent=2)

    question = f"""DONNÉES MARCHÉ FIN DE JOURNÉE:
{donnees_texte}

Date: {maintenant.strftime('%d/%m/%Y')}
Demain: {demain.strftime('%d/%m/%Y')}

Génère un résumé de clôture complet en JSON selon le format demandé."""

    try:
        message = client_anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            system=SYSTEM_PROMPT_CLOTURE,
            messages=[{"role": "user", "content": question}]
        )

        reponse = message.content[0].text
        result, erreur = extraire_json_claude(reponse)
        if erreur:
            logger.error(f"Extraction JSON clôture échouée: {erreur}")
            return None
        return result
    except Exception as e:
        logger.error(f"Erreur clôture: {e}")
        return None


