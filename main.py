#!/usr/bin/env python3
"""
Application de Trading Euronext Paris
FONCTION 1: Backtesting et Analyse des Données (20 derniers jours)

Objectif: Sélectionner 2-3 valeurs pour day trading avec gain cible de 1%
"""

from analyzer import TradingAnalyzer
from colorama import Fore, Style, init
import sys

init(autoreset=True)


def print_header():
    """Affiche l'en-tête de l'application"""
    print(f"\n{Fore.CYAN}{'='*70}")
    print(f"{Fore.CYAN}📈 TRADING EURONEXT PARIS - FONCTION 1: BACKTESTING")
    print(f"{Fore.CYAN}{'='*70}\n")
    print(f"{Fore.YELLOW}Objectif: Day Trading avec gain cible de 1%")
    print(f"{Fore.YELLOW}Marché: Euronext Paris (Bourse de Paris)")
    print(f"{Fore.YELLOW}Période d'analyse: 20 derniers jours\n")


def print_menu():
    """Affiche le menu d'options"""
    print(f"\n{Fore.CYAN}{'='*70}")
    print(f"{Fore.CYAN}OPTIONS DISPONIBLES:")
    print(f"{Fore.CYAN}{'='*70}\n")

    print(f"{Fore.GREEN}1. 🎯 Trouver les meilleures opportunités du jour (TOP 3)")
    print(f"{Fore.GREEN}2. 📊 Analyser une action spécifique")
    print(f"{Fore.GREEN}3. 🔥 Quelles sont les actions les plus volatiles ?")
    print(f"{Fore.GREEN}4. 🚀 Quelles actions ont le meilleur momentum ?")
    print(f"{Fore.GREEN}5. 📦 Quelles actions ont un volume anormal ?")
    print(f"{Fore.GREEN}6. 📉 Quelles actions sont proches de leur support ?")
    print(f"{Fore.GREEN}7. ⚡ Quelles actions ont eu un gap à l'ouverture ?")
    print(f"{Fore.GREEN}8. 📋 Liste de toutes les actions surveillées")
    print(f"{Fore.GREEN}9. 🔄 Recharger les données")
    print(f"{Fore.RED}0. ❌ Quitter\n")


def main():
    """Point d'entrée principal"""
    print_header()

    # Initialiser l'analyseur
    print(f"{Fore.YELLOW}🔧 Initialisation de l'analyseur...\n")
    analyzer = TradingAnalyzer()

    # Charger les données
    analyzer.load_and_prepare_data()

    # Boucle principale
    while True:
        print_menu()
        choice = input(f"{Fore.CYAN}Votre choix: {Style.RESET_ALL}").strip()

        if choice == "0":
            print(f"\n{Fore.YELLOW}👋 Au revoir ! Bon trading !\n")
            break

        elif choice == "1":
            # TOP 3 opportunités
            analyzer.display_best_candidates(top_n=3)

        elif choice == "2":
            # Analyser une action spécifique
            symbol = input(f"\n{Fore.CYAN}Symbole de l'action (ex: MC.PA): {Style.RESET_ALL}").strip().upper()
            analysis = analyzer.get_stock_analysis(symbol)

            if 'error' in analysis:
                print(f"\n{Fore.RED}❌ {analysis['error']}\n")
            else:
                analyzer._print_candidate(1, symbol, analysis)

        elif choice == "3":
            # Actions les plus volatiles
            print(f"\n{Fore.YELLOW}🔥 TOP 5 ACTIONS LES PLUS VOLATILES:\n")
            results = analyzer.answer_question("plus_volatile", top_n=5)
            for i, (symbol, volatility) in enumerate(results, 1):
                print(f"  {i}. {symbol}: {volatility:.2f}%")
            print()

        elif choice == "4":
            # Meilleur momentum
            print(f"\n{Fore.YELLOW}🚀 TOP 5 ACTIONS AVEC LE MEILLEUR MOMENTUM:\n")
            results = analyzer.answer_question("meilleur_momentum", top_n=5)
            for i, (symbol, momentum) in enumerate(results, 1):
                color = Fore.GREEN if momentum > 0 else Fore.RED
                print(f"  {i}. {symbol}: {color}{momentum:+.2f}%{Style.RESET_ALL}")
            print()

        elif choice == "5":
            # Volume anormal
            print(f"\n{Fore.YELLOW}📦 ACTIONS AVEC VOLUME ANORMAL (>150% de la moyenne):\n")
            results = analyzer.answer_question("volume_anormal", min_ratio=1.5, top_n=5)
            if results:
                for i, (symbol, ratio) in enumerate(results, 1):
                    print(f"  {i}. {symbol}: {ratio:.2f}x la moyenne")
            else:
                print(f"  {Fore.RED}Aucune action trouvée")
            print()

        elif choice == "6":
            # Proche du support
            print(f"\n{Fore.YELLOW}📉 ACTIONS PROCHES DE LEUR SUPPORT (<2%):\n")
            results = analyzer.answer_question("proche_support", threshold_pct=2.0)
            if results:
                for i, (symbol, distance) in enumerate(results, 1):
                    print(f"  {i}. {symbol}: +{distance:.2f}% au-dessus du support")
            else:
                print(f"  {Fore.RED}Aucune action trouvée")
            print()

        elif choice == "7":
            # Gaps à l'ouverture
            print(f"\n{Fore.YELLOW}⚡ ACTIONS AVEC GAP À L'OUVERTURE (>0.5%):\n")
            results = analyzer.answer_question("gap_ouverture", min_gap_pct=0.5)
            if results:
                for i, (symbol, gap) in enumerate(results, 1):
                    color = Fore.GREEN if gap > 0 else Fore.RED
                    direction = "↑ Gap Haussier" if gap > 0 else "↓ Gap Baissier"
                    print(f"  {i}. {symbol}: {color}{gap:+.2f}% {direction}{Style.RESET_ALL}")
            else:
                print(f"  {Fore.RED}Aucune action trouvée")
            print()

        elif choice == "8":
            # Liste des actions
            print(f"\n{Fore.YELLOW}📋 ACTIONS SURVEILLÉES:\n")
            for i, symbol in enumerate(sorted(analyzer.enriched_data.keys()), 1):
                print(f"  {i}. {symbol}")
            print(f"\n{Fore.GREEN}Total: {len(analyzer.enriched_data)} actions\n")

        elif choice == "9":
            # Recharger les données
            print(f"\n{Fore.YELLOW}🔄 Rechargement des données...\n")
            analyzer.load_and_prepare_data()
            print(f"{Fore.GREEN}✅ Données rechargées !\n")

        else:
            print(f"\n{Fore.RED}❌ Choix invalide. Veuillez réessayer.\n")

        input(f"\n{Fore.CYAN}Appuyez sur Entrée pour continuer...{Style.RESET_ALL}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{Fore.YELLOW}👋 Programme interrompu. Au revoir !\n")
        sys.exit(0)
    except Exception as e:
        print(f"\n{Fore.RED}❌ Erreur: {e}\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)
