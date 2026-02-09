"""
Tâches planifiées: analyses, vérifications, clôtures, rapports.
"""
import time
import os
from threading import Thread

import schedule

from .config import logger, client_twilio
from .constants import ACTIFS_PERMANENTS, ACTIFS_HORS_US, est_jour_trading_valide
from .market_context import get_paris_time, get_utc_time_for_paris, get_market_context
from .market_data import recuperer_donnees_marche
from .indicators import enrichir_donnees_avec_indicateurs
from .analysis import analyser_marche_json, generer_cloture_json
from .trades import (
    enregistrer_recommandation, verifier_resultats_trades,
    reevaluer_trades_intraday, cloturer_trades_jour,
    enrichir_opportunite_avec_donnees_marche
)
from .journal import (
    enregistrer_journal_fr, enregistrer_journal_complet,
    generer_rapport_hebdo, sauvegarder_analyse
)
from .adjustments import traiter_ajustements_automatiquement
from .ab_testing import evaluer_tous_ab_tests
from .database import backup_database

def executer_analyse_planifiee(eu_only=False):
    """Exécute une analyse planifiée.
    eu_only=True: exclut les actions US individuelles (avant ouverture US)"""
    maintenant = get_paris_time()

    # Vérifier si c'est un jour de trading valide
    is_valide, raison = est_jour_trading_valide(maintenant)
    if not is_valide:
        print(f"[{maintenant.strftime('%H:%M:%S')} CET] ⏭️ Analyse ignorée: {raison}")
        return

    actifs = ACTIFS_HORS_US if eu_only else ACTIFS_PERMANENTS
    scope = "EU/Commodités/Forex" if eu_only else "Complète"
    print(f"\n[{maintenant.strftime('%H:%M:%S')} CET] 🚀 Analyse planifiée ({scope})...")

    try:
        donnees = recuperer_donnees_marche(actifs)
        donnees_enrichies = enrichir_donnees_avec_indicateurs(donnees)
        analyse = analyser_marche_json(donnees)

        if analyse:
            marche_type, _ = get_market_context()
            sauvegarder_analyse('planifiee', analyse, marche_type)

            # Enregistrer les opportunités avec toutes les données (enrichies)
            indicateurs = donnees_enrichies.get('indicateurs_calcules', {})
            for opp in analyse.get('opportunites', []):
                opp_enrichie = enrichir_opportunite_avec_donnees_marche(opp, donnees, indicateurs)
                enregistrer_recommandation(opp_enrichie)

            print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ Analyse sauvegardée")
    except Exception as e:
        print(f"[{maintenant.strftime('%H:%M:%S')} CET] ❌ Erreur: {e}")

def executer_cloture_planifiee():
    """Exécute la clôture planifiée"""
    maintenant = get_paris_time()
    is_valide, raison = est_jour_trading_valide(maintenant)
    if not is_valide:
        return

    print(f"\n[{maintenant.strftime('%H:%M:%S')} CET] 🌙 Clôture planifiée...")

    try:
        donnees = recuperer_donnees_marche(ACTIFS_PERMANENTS)
        cloture = generer_cloture_json(donnees)

        if cloture:
            sauvegarder_analyse('cloture', cloture)
            print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ Clôture sauvegardée")
    except Exception as e:
        print(f"[{maintenant.strftime('%H:%M:%S')} CET] ❌ Erreur: {e}")

def executer_journal_fr():
    """Exécute l'enregistrement du journal FR à 18h00"""
    maintenant = get_paris_time()
    is_valide, _ = est_jour_trading_valide(maintenant)
    if not is_valide:
        return
    enregistrer_journal_fr()

def executer_journal_complet():
    """Exécute l'enregistrement du journal complet + bilan à 22h30"""
    maintenant = get_paris_time()
    is_valide, _ = est_jour_trading_valide(maintenant)
    if not is_valide:
        return
    enregistrer_journal_complet()

def executer_verification_trades():
    """Exécute la vérification des trades"""
    maintenant = get_paris_time()
    is_valide, _ = est_jour_trading_valide(maintenant)
    if not is_valide:
        return
    verifier_resultats_trades()

def executer_reevaluation_intraday():
    """Exécute la réévaluation intraday des trades en cours"""
    maintenant = get_paris_time()
    is_valide, _ = est_jour_trading_valide(maintenant)
    if not is_valide:
        return
    reevaluer_trades_intraday()

def executer_cloture_trades():
    """Exécute la clôture des trades du jour"""
    maintenant = get_paris_time()
    is_valide, _ = est_jour_trading_valide(maintenant)
    if not is_valide:
        return
    # D'abord vérifier une dernière fois
    verifier_resultats_trades()
    # Puis clôturer les trades restants
    cloturer_trades_jour()

def executer_rapport_hebdo():
    """Exécute la génération du rapport hebdomadaire (samedi matin)"""
    maintenant = get_paris_time()
    if maintenant.weekday() != 5:  # 5 = Samedi
        return
    generer_rapport_hebdo()

def configurer_schedule():
    """Configure les tâches planifiées"""
    # Analyses EU-only (avant ouverture US): 8h, 9h15, 12h
    for heure in ["08:00", "09:15", "12:00"]:
        heure_utc = get_utc_time_for_paris(heure)
        for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
            getattr(schedule.every(), jour).at(heure_utc).do(executer_analyse_planifiee, eu_only=True)

    # Analyses complètes (US ouverts): 15h35, 17h
    for heure in ["15:35", "17:00"]:
        heure_utc = get_utc_time_for_paris(heure)
        for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
            getattr(schedule.every(), jour).at(heure_utc).do(executer_analyse_planifiee, eu_only=False)

    # Vérification trades: toutes les 30 minutes entre 9h et 22h
    for heure in ["09:30", "10:00", "10:30", "11:00", "11:30", "12:00",
                  "14:00", "14:30", "15:00", "15:30", "16:00", "16:30",
                  "17:00", "17:30", "18:00", "19:00", "20:00", "21:00"]:
        heure_utc = get_utc_time_for_paris(heure)
        for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
            getattr(schedule.every(), jour).at(heure_utc).do(executer_verification_trades)

    # Réévaluation intraday: toutes les heures pendant les sessions actives
    # (plus fréquent pendant US_OPEN car plus de volatilité)
    for heure in ["09:15", "10:15", "11:15", "14:15", "15:45", "16:30", "17:15"]:
        heure_utc = get_utc_time_for_paris(heure)
        for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
            getattr(schedule.every(), jour).at(heure_utc).do(executer_reevaluation_intraday)

    # Clôture trades EU: 17h45 (après clôture EU)
    heure_cloture_eu_utc = get_utc_time_for_paris("17:45")
    for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
        getattr(schedule.every(), jour).at(heure_cloture_eu_utc).do(executer_verification_trades)

    # Clôture: 22h
    heure_cloture_utc = get_utc_time_for_paris("22:00")
    for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
        getattr(schedule.every(), jour).at(heure_cloture_utc).do(executer_cloture_planifiee)

    # Clôture forcée trades EU: 17:15 (15min avant fermeture EU 17:30)
    heure_cloture_eu_trades_utc = get_utc_time_for_paris("17:15")
    for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
        getattr(schedule.every(), jour).at(heure_cloture_eu_trades_utc).do(executer_cloture_trades)

    # Clôture forcée trades US: 21:45 (15min avant fermeture US 22:00)
    heure_cloture_us_trades_utc = get_utc_time_for_paris("21:45")
    for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
        getattr(schedule.every(), jour).at(heure_cloture_us_trades_utc).do(executer_cloture_trades)

    # Journal FR: 18h00 (après clôture marchés EU)
    heure_journal_fr_utc = get_utc_time_for_paris("18:00")
    for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
        getattr(schedule.every(), jour).at(heure_journal_fr_utc).do(executer_journal_fr)

    # Journal complet + Bilan: 22h30 (après clôture US)
    heure_journal_complet_utc = get_utc_time_for_paris("22:30")
    for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
        getattr(schedule.every(), jour).at(heure_journal_complet_utc).do(executer_journal_complet)

    # Rapport hebdomadaire: Samedi 09h00 (après clôture complète US vendredi soir)
    heure_rapport_hebdo_utc = get_utc_time_for_paris("09:00")
    schedule.every().saturday.at(heure_rapport_hebdo_utc).do(executer_rapport_hebdo)

    # NOTE: Expiration des ajustements DÉSACTIVÉE - on garde tout l'historique
    # Les ajustements sont conservés pour analyse dans les rapports hebdo

    # Évaluation feedback des ajustements validés: dimanche 19h00
    # TODO: Implement evaluer_tous_ajustements_valides
    # heure_feedback_utc = get_utc_time_for_paris("19:00")
    # schedule.every().sunday.at(heure_feedback_utc).do(evaluer_tous_ajustements_valides)

    # Traitement automatique des ajustements en attente: tous les jours à 08h00
    heure_auto_ajust_utc = get_utc_time_for_paris("08:00")
    schedule.every().day.at(heure_auto_ajust_utc).do(traiter_ajustements_automatiquement)

    # Évaluation des tests A/B: dimanche 18h00
    heure_ab_eval_utc = get_utc_time_for_paris("18:00")
    schedule.every().sunday.at(heure_ab_eval_utc).do(evaluer_tous_ab_tests)

    # Backup quotidien de la base de données: tous les jours à 23h00
    heure_backup_utc = get_utc_time_for_paris("23:00")
    schedule.every().day.at(heure_backup_utc).do(backup_database)
    print(f"  ⏰ Backup DB: 23h00 CET ({heure_backup_utc} UTC)")

def run_scheduler():
    """Thread robuste pour le scheduler avec gestion d'erreurs"""
    consecutive_errors = 0
    max_consecutive_errors = 3

    while True:
        try:
            schedule.run_pending()
            consecutive_errors = 0  # Reset si succès
        except Exception as e:
            consecutive_errors += 1
            logger.error(f"Erreur scheduler (#{consecutive_errors}): {e}")

            # Alerte après plusieurs erreurs consécutives
            if consecutive_errors >= max_consecutive_errors:
                print(f"🚨 ALERTE CRITIQUE: Scheduler a échoué {consecutive_errors} fois!")
                # Envoyer notification WhatsApp si configuré
                try:
                    twilio_to = os.environ.get('TWILIO_WHATSAPP_TO')
                    twilio_from = os.environ.get('TWILIO_WHATSAPP_FROM')
                    if client_twilio and twilio_to and twilio_from:
                        client_twilio.messages.create(
                            body=f"🚨 ALERTE TRADING: Scheduler en erreur ({consecutive_errors}x): {str(e)[:100]}",
                            from_=twilio_from,
                            to=twilio_to
                        )
                except Exception as twilio_err:
                    print(f"⚠️ Impossible d'envoyer l'alerte: {twilio_err}")

                # Cooldown prolongé après alertes
                time.sleep(120)
                consecutive_errors = 0  # Reset après cooldown
                continue

            # Petit délai avant retry en cas d'erreur
            time.sleep(60)
            continue

        time.sleep(15)

