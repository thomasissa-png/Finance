#!/usr/bin/env python3
"""
Application de Trading Boursier - Aide à la Décision d'Ordre
"""

import yaml
import argparse
from datetime import datetime
from colorama import init, Fore, Style
import time
from typing import List

from stock_data import StockDataFetcher
from alerts import AlertGenerator, TradingSignal

# Initialiser colorama pour les couleurs dans le terminal
init(autoreset=True)


class TradingApp:
    """Application principale de trading"""

    def __init__(self, config_path: str = "config.yaml"):
        """Initialise l'application avec la configuration"""
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

        self.fetcher = StockDataFetcher()
        self.alert_generator = AlertGenerator(self.config)

    def print_header(self):
        """Affiche l'en-tête de l'application"""
        print(f"\n{Fore.CYAN}{'='*70}")
        print(f"{Fore.CYAN}📈 APPLICATION DE TRADING BOURSIER 📊")
        print(f"{Fore.CYAN}   Aide à la décision pour placer vos ordres")
        print(f"{Fore.CYAN}{'='*70}\n")

    def analyze_single_stock(self, symbol: str):
        """Analyse une action unique"""
        print(f"{Fore.YELLOW}🔍 Analyse de {symbol}...\n")

        # Récupérer les informations de base
        info = self.fetcher.get_stock_info(symbol)
        if info:
            print(f"{Fore.WHITE}📋 Informations:")
            print(f"   Nom: {info['name']}")
            print(f"   Secteur: {info['sector']}")
            print(f"   Prix actuel: ${info['current_price']}")
            print(f"   Capitalisation: {self._format_market_cap(info['market_cap'])}")
            print(f"   P/E Ratio: {info['pe_ratio']}")
            print(f"   52w Haut: ${info['52w_high']} | Bas: ${info['52w_low']}\n")

        # Récupérer les données historiques
        data = self.fetcher.get_stock_data(symbol, period="3mo", interval="1d")

        if data is None:
            print(f"{Fore.RED}❌ Impossible de récupérer les données pour {symbol}\n")
            return

        # Générer le signal de trading
        signal = self.alert_generator.analyze_stock(symbol, data)

        if signal:
            self._print_signal(signal)
        else:
            print(f"{Fore.YELLOW}⚠️  Aucun signal fort détecté pour {symbol}\n")

    def analyze_watchlist(self):
        """Analyse toutes les actions de la liste de surveillance"""
        stocks = self.config['stocks']
        print(f"{Fore.YELLOW}🔍 Analyse de {len(stocks)} actions...\n")

        signals: List[TradingSignal] = []

        for symbol in stocks:
            print(f"{Fore.WHITE}Analyse de {symbol}...", end=" ")

            data = self.fetcher.get_stock_data(symbol, period="3mo", interval="1d")

            if data is not None:
                signal = self.alert_generator.analyze_stock(symbol, data)
                if signal:
                    signals.append(signal)
                    print(f"{Fore.GREEN}✓")
                else:
                    print(f"{Fore.YELLOW}○")
            else:
                print(f"{Fore.RED}✗")

            # Pause pour éviter de surcharger l'API
            time.sleep(0.5)

        print(f"\n{Fore.CYAN}{'='*70}\n")
        print(f"{Fore.CYAN}📊 RÉSULTATS DE L'ANALYSE\n")

        if signals:
            # Trier par force du signal
            signals.sort(key=lambda x: x.strength, reverse=True)

            # Séparer les signaux d'achat et de vente
            buy_signals = [s for s in signals if s.signal_type == 'BUY']
            sell_signals = [s for s in signals if s.signal_type == 'SELL']

            if buy_signals:
                print(f"{Fore.GREEN}🟢 SIGNAUX D'ACHAT ({len(buy_signals)}):\n")
                for signal in buy_signals:
                    self._print_signal(signal)

            if sell_signals:
                print(f"{Fore.RED}🔴 SIGNAUX DE VENTE ({len(sell_signals)}):\n")
                for signal in sell_signals:
                    self._print_signal(signal)
        else:
            print(f"{Fore.YELLOW}⚠️  Aucun signal détecté pour le moment\n")

    def monitor_continuous(self):
        """Surveillance continue des actions"""
        interval = self.config['monitoring']['interval_minutes']
        market_hours_only = self.config['monitoring']['market_hours_only']

        print(f"{Fore.CYAN}🔄 Surveillance continue activée")
        print(f"   Intervalle: {interval} minutes")
        print(f"   Heures de marché uniquement: {market_hours_only}\n")

        try:
            while True:
                # Vérifier si le marché est ouvert (si option activée)
                if market_hours_only and not self.fetcher.is_market_open():
                    print(f"{Fore.YELLOW}⏸️  Marché fermé - En attente...")
                    time.sleep(60)  # Vérifier toutes les minutes
                    continue

                # Analyser la liste de surveillance
                self.analyze_watchlist()

                # Attendre avant la prochaine analyse
                print(f"\n{Fore.CYAN}⏰ Prochaine analyse dans {interval} minutes...")
                print(f"{Fore.CYAN}   (Ctrl+C pour arrêter)\n")
                time.sleep(interval * 60)

        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}🛑 Surveillance arrêtée par l'utilisateur")

    def _print_signal(self, signal: TradingSignal):
        """Affiche un signal de trading formaté"""
        # Couleur selon le type de signal
        if signal.signal_type == 'BUY':
            color = Fore.GREEN
            emoji = "🟢"
        elif signal.signal_type == 'SELL':
            color = Fore.RED
            emoji = "🔴"
        else:
            color = Fore.YELLOW
            emoji = "🟡"

        print(f"{color}{emoji} {signal.signal_type} - {signal.symbol} @ ${signal.price:.2f}")

        # Barre de force
        strength_bars = "█" * int(signal.strength * 10)
        strength_empty = "░" * (10 - int(signal.strength * 10))
        print(f"   Force: {color}{strength_bars}{Style.DIM}{strength_empty}{Style.RESET_ALL} ({signal.strength:.1%})")

        print(f"   Raison: {signal.reason}")

        # Indicateurs
        print(f"   Indicateurs:")
        for name, value in signal.indicators.items():
            if value is not None:
                if isinstance(value, (int, float)):
                    print(f"      {name}: {value:.2f}")
                else:
                    print(f"      {name}: {value}")

        print()

    def _format_market_cap(self, market_cap) -> str:
        """Formate la capitalisation boursière"""
        if market_cap == 'N/A' or market_cap is None:
            return 'N/A'

        if market_cap >= 1_000_000_000_000:
            return f"${market_cap / 1_000_000_000_000:.2f}T"
        elif market_cap >= 1_000_000_000:
            return f"${market_cap / 1_000_000_000:.2f}B"
        elif market_cap >= 1_000_000:
            return f"${market_cap / 1_000_000:.2f}M"
        else:
            return f"${market_cap:,.0f}"


def main():
    """Point d'entrée principal"""
    parser = argparse.ArgumentParser(
        description="Application de Trading Boursier - Aide à la décision",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples d'utilisation:
  python main.py --analyze AAPL              Analyser une action spécifique
  python main.py --watchlist                 Analyser toute la liste de surveillance
  python main.py --monitor                   Surveillance continue
  python main.py --analyze TSLA GOOGL MSFT   Analyser plusieurs actions
        """
    )

    parser.add_argument(
        '--analyze', '-a',
        nargs='+',
        metavar='SYMBOL',
        help='Analyser une ou plusieurs actions (ex: AAPL GOOGL TSLA)'
    )

    parser.add_argument(
        '--watchlist', '-w',
        action='store_true',
        help='Analyser toutes les actions de la liste de surveillance'
    )

    parser.add_argument(
        '--monitor', '-m',
        action='store_true',
        help='Surveillance continue des actions'
    )

    parser.add_argument(
        '--config', '-c',
        default='config.yaml',
        help='Chemin vers le fichier de configuration (défaut: config.yaml)'
    )

    args = parser.parse_args()

    # Créer l'application
    try:
        app = TradingApp(config_path=args.config)
        app.print_header()

        # Exécuter l'action demandée
        if args.analyze:
            for symbol in args.analyze:
                app.analyze_single_stock(symbol.upper())
        elif args.watchlist:
            app.analyze_watchlist()
        elif args.monitor:
            app.monitor_continuous()
        else:
            # Par défaut, analyser la watchlist
            app.analyze_watchlist()

    except FileNotFoundError:
        print(f"{Fore.RED}❌ Fichier de configuration non trouvé: {args.config}")
        print(f"{Fore.YELLOW}💡 Assurez-vous que config.yaml existe dans le répertoire")
    except Exception as e:
        print(f"{Fore.RED}❌ Erreur: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
