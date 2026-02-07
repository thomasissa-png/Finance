#!/usr/bin/env python3
"""
Application de Trading Multi-Marchés
FONCTION 1: Backtesting et Analyse des Données (20 derniers jours)

Objectif: Sélectionner 2-3 valeurs pour day trading avec gain cible de 1%
Marchés: Actions, Forex, Métaux Précieux, Commodities, Indices
"""

from analyzer import TradingAnalyzer
from colorama import Fore, Style, init
import sys

init(autoreset=True)


def print_header():
    """Affiche l'en-tête de l'application"""
    print(f"\n{Fore.CYAN}{'='*70}")
    print(f"{Fore.CYAN}📈 TRADING MULTI-MARCHÉS - FONCTION 1: BACKTESTING")
    print(f"{Fore.CYAN}{'='*70}\n")
    print(f"{Fore.YELLOW}Objectif: Day Trading avec gain cible de 1%")
    print(f"{Fore.YELLOW}Marchés: 🏢 Actions • 💱 Forex • 🥇 Métaux • 🛢️  Commodities • 📊 Indices")
    print(f"{Fore.YELLOW}Période d'analyse: 20 derniers jours")
    print(f"{Fore.YELLOW}Total actifs surveillés: 60+\n")


def print_menu():
    """Affiche le menu d'options"""
    print(f"\n{Fore.CYAN}{'='*70}")
    print(f"{Fore.CYAN}OPTIONS DISPONIBLES:")
    print(f"{Fore.CYAN}{'='*70}\n")

    print(f"{Fore.GREEN}OPPORTUNITÉS:")
    print(f"{Fore.GREEN}  1. 🎯 TOP 3 opportunités (TOUS marchés)")
    print(f"{Fore.GREEN}  2. 🏢 TOP 3 Actions Euronext")
    print(f"{Fore.GREEN}  3. 💱 TOP 3 Forex")
    print(f"{Fore.GREEN}  4. 🥇 TOP 3 Métaux Précieux")
    print(f"{Fore.GREEN}  5. 🛢️  TOP 3 Commodities")
    print(f"{Fore.GREEN}  6. 📊 TOP 3 Indices")

    print(f"\n{Fore.YELLOW}ANALYSE:")
    print(f"{Fore.YELLOW}  10. 📈 Analyser un actif spécifique")
    print(f"{Fore.YELLOW}  11. 🔥 Les plus volatiles")
    print(f"{Fore.YELLOW}  12. 🚀 Meilleur momentum")
    print(f"{Fore.YELLOW}  13. 📦 Volume anormal")
    print(f"{Fore.YELLOW}  14. 📉 Proches du support")
    print(f"{Fore.YELLOW}  15. ⚡ Gaps à l'ouverture")

    print(f"\n{Fore.CYAN}AUTRES:")
    print(f"{Fore.CYAN}  20. 📋 Liste de tous les actifs")
    print(f"{Fore.CYAN}  21. 🔄 Recharger les données")
    print(f"{Fore.RED}  0. ❌ Quitter\n")


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

        # OPPORTUNITÉS
        elif choice == "1":
            # TOP 3 opportunités (tous marchés)
            analyzer.display_best_candidates(top_n=3)

        elif choice == "2":
            # TOP 3 Actions
            candidates = analyzer.find_best_daily_trading_candidates(top_n=3, category='action')
            if candidates:
                print(f"{Fore.GREEN}✅ TOP 3 ACTIONS EURONEXT:\n")
                for i, (symbol, analysis) in enumerate(candidates, 1):
                    analyzer._print_candidate(i, symbol, analysis)
            else:
                print(f"{Fore.RED}❌ Aucune action ne remplit les critères\n")

        elif choice == "3":
            # TOP 3 Forex
            candidates = analyzer.find_best_daily_trading_candidates(top_n=3, category='forex')
            if candidates:
                print(f"{Fore.GREEN}✅ TOP 3 FOREX:\n")
                for i, (symbol, analysis) in enumerate(candidates, 1):
                    analyzer._print_candidate(i, symbol, analysis)
            else:
                print(f"{Fore.RED}❌ Aucune paire forex ne remplit les critères\n")

        elif choice == "4":
            # TOP 3 Métaux
            candidates = analyzer.find_best_daily_trading_candidates(top_n=3, category='metal')
            if candidates:
                print(f"{Fore.GREEN}✅ TOP 3 MÉTAUX PRÉCIEUX:\n")
                for i, (symbol, analysis) in enumerate(candidates, 1):
                    analyzer._print_candidate(i, symbol, analysis)
            else:
                print(f"{Fore.RED}❌ Aucun métal ne remplit les critères\n")

        elif choice == "5":
            # TOP 3 Commodities
            candidates = analyzer.find_best_daily_trading_candidates(top_n=3, category='commodity')
            if candidates:
                print(f"{Fore.GREEN}✅ TOP 3 COMMODITIES:\n")
                for i, (symbol, analysis) in enumerate(candidates, 1):
                    analyzer._print_candidate(i, symbol, analysis)
            else:
                print(f"{Fore.RED}❌ Aucune commodity ne remplit les critères\n")

        elif choice == "6":
            # TOP 3 Indices
            candidates = analyzer.find_best_daily_trading_candidates(top_n=3, category='indice')
            if candidates:
                print(f"{Fore.GREEN}✅ TOP 3 INDICES:\n")
                for i, (symbol, analysis) in enumerate(candidates, 1):
                    analyzer._print_candidate(i, symbol, analysis)
            else:
                print(f"{Fore.RED}❌ Aucun indice ne remplit les critères\n")

        # ANALYSE
        elif choice == "10":
            # Analyser un actif spécifique
            symbol = input(f"\n{Fore.CYAN}Symbole (ex: MC.PA, GC=F, EURUSD=X, ^FCHI): {Style.RESET_ALL}").strip().upper()
            analysis = analyzer.get_stock_analysis(symbol)

            if 'error' in analysis:
                print(f"\n{Fore.RED}❌ {analysis['error']}\n")
            else:
                analyzer._print_candidate(1, symbol, analysis)

        elif choice == "11":
            # Les plus volatiles
            print(f"\n{Fore.YELLOW}🔥 TOP 10 ACTIFS LES PLUS VOLATILES:\n")
            results = analyzer.answer_question("plus_volatile", top_n=10)
            for i, (symbol, volatility) in enumerate(results, 1):
                cat = analyzer.get_asset_category(symbol)
                name = analyzer.get_asset_name(symbol)
                print(f"  {i}. {symbol:12} {cat:20} {name:25} {volatility:.2f}%")
            print()

        elif choice == "12":
            # Meilleur momentum
            print(f"\n{Fore.YELLOW}🚀 TOP 10 ACTIFS AVEC LE MEILLEUR MOMENTUM:\n")
            results = analyzer.answer_question("meilleur_momentum", top_n=10)
            for i, (symbol, momentum) in enumerate(results, 1):
                cat = analyzer.get_asset_category(symbol)
                name = analyzer.get_asset_name(symbol)
                color = Fore.GREEN if momentum > 0 else Fore.RED
                print(f"  {i}. {symbol:12} {cat:20} {name:25} {color}{momentum:+.2f}%{Style.RESET_ALL}")
            print()

        elif choice == "13":
            # Volume anormal
            print(f"\n{Fore.YELLOW}📦 ACTIFS AVEC VOLUME ANORMAL (>150%):\n")
            results = analyzer.answer_question("volume_anormal", min_ratio=1.5, top_n=10)
            if results:
                for i, (symbol, ratio) in enumerate(results, 1):
                    cat = analyzer.get_asset_category(symbol)
                    name = analyzer.get_asset_name(symbol)
                    print(f"  {i}. {symbol:12} {cat:20} {name:25} {ratio:.2f}x")
            else:
                print(f"  {Fore.RED}Aucun actif trouvé")
            print()

        elif choice == "14":
            # Proche du support
            print(f"\n{Fore.YELLOW}📉 ACTIFS PROCHES DE LEUR SUPPORT (<2%):\n")
            results = analyzer.answer_question("proche_support", threshold_pct=2.0)
            if results:
                for i, (symbol, distance) in enumerate(results, 1):
                    cat = analyzer.get_asset_category(symbol)
                    name = analyzer.get_asset_name(symbol)
                    print(f"  {i}. {symbol:12} {cat:20} {name:25} +{distance:.2f}%")
            else:
                print(f"  {Fore.RED}Aucun actif trouvé")
            print()

        elif choice == "15":
            # Gaps à l'ouverture
            print(f"\n{Fore.YELLOW}⚡ ACTIFS AVEC GAP (>0.5%):\n")
            results = analyzer.answer_question("gap_ouverture", min_gap_pct=0.5)
            if results:
                for i, (symbol, gap) in enumerate(results, 1):
                    cat = analyzer.get_asset_category(symbol)
                    name = analyzer.get_asset_name(symbol)
                    color = Fore.GREEN if gap > 0 else Fore.RED
                    direction = "↑" if gap > 0 else "↓"
                    print(f"  {i}. {symbol:12} {cat:20} {name:25} {color}{gap:+.2f}% {direction}{Style.RESET_ALL}")
            else:
                print(f"  {Fore.RED}Aucun actif trouvé")
            print()

        # AUTRES
        elif choice == "20":
            # Liste de tous les actifs par catégorie
            print(f"\n{Fore.YELLOW}📋 TOUS LES ACTIFS SURVEILLÉS:\n")

            # Grouper par catégorie
            categories = {}
            for symbol in sorted(analyzer.enriched_data.keys()):
                cat = analyzer.get_asset_category(symbol)
                if cat not in categories:
                    categories[cat] = []
                categories[cat].append(symbol)

            # Afficher par catégorie
            for cat, symbols in sorted(categories.items()):
                print(f"\n{Fore.CYAN}{cat}:")
                for symbol in symbols:
                    name = analyzer.get_asset_name(symbol)
                    print(f"  • {symbol:12} - {name}")

            print(f"\n{Fore.GREEN}Total: {len(analyzer.enriched_data)} actifs\n")

        elif choice == "21":
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
