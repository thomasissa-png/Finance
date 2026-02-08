"""
Module d'analyse et d'interrogation des données
Fonction 1 : Système de backtesting et réponse aux questions
Fonction 2 : Sélection automatique de turbos Société Générale
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from data_fetcher import EuronextDataFetcher
from indicators import TechnicalIndicators
from turbos_scraper import TurbosScraper
from tabulate import tabulate
from colorama import Fore, Style, init

init(autoreset=True)


class TradingAnalyzer:
    """
    Analyseur de données boursières pour répondre aux questions
    et identifier les meilleures opportunités de trading
    """

    def __init__(self, config_path: str = "config.yaml"):
        """Initialise l'analyseur"""
        self.fetcher = EuronextDataFetcher(config_path)
        self.indicators = TechnicalIndicators()
        self.turbos_scraper = TurbosScraper(config_path)
        self.enriched_data: Dict[str, pd.DataFrame] = {}

    def get_asset_category(self, symbol: str) -> str:
        """
        Identifie la catégorie d'un actif basé sur son symbole

        Args:
            symbol: Symbole de l'actif

        Returns:
            Catégorie de l'actif
        """
        if symbol.endswith('.PA'):
            return '🏢 Action (Euronext)'
        elif symbol.endswith('=F'):
            # Distinguer métaux précieux et commodities
            if symbol in ['GC=F', 'SI=F', 'PL=F', 'PA=F']:
                return '🥇 Métal Précieux'
            else:
                return '🛢️  Commodity'
        elif symbol.endswith('=X'):
            return '💱 Forex'
        elif symbol.startswith('^'):
            return '📊 Indice'
        else:
            return '❓ Autre'

    def get_asset_name(self, symbol: str) -> str:
        """
        Retourne le nom complet d'un actif

        Args:
            symbol: Symbole de l'actif

        Returns:
            Nom de l'actif
        """
        names = {
            # Actions Euronext
            'MC.PA': 'LVMH',
            'OR.PA': 'L\'Oréal',
            'AI.PA': 'Air Liquide',
            'SAN.PA': 'Sanofi',
            'TTE.PA': 'TotalEnergies',
            'BNP.PA': 'BNP Paribas',
            'SU.PA': 'Schneider Electric',
            'SAF.PA': 'Safran',
            'RMS.PA': 'Hermès',
            'CS.PA': 'AXA',
            'CAP.PA': 'Capgemini',
            'VIE.PA': 'Veolia',
            'DG.PA': 'Vinci',
            'EN.PA': 'Bouygues',
            'RI.PA': 'Pernod Ricard',

            # Métaux précieux
            'GC=F': 'Or (Gold)',
            'SI=F': 'Argent (Silver)',
            'PL=F': 'Platine (Platinum)',
            'PA=F': 'Palladium',

            # Forex
            'EURUSD=X': 'EUR/USD',
            'GBPUSD=X': 'GBP/USD',
            'USDJPY=X': 'USD/JPY',
            'AUDUSD=X': 'AUD/USD',
            'USDCHF=X': 'USD/CHF',
            'USDCAD=X': 'USD/CAD',
            'NZDUSD=X': 'NZD/USD',
            'EURGBP=X': 'EUR/GBP',
            'EURJPY=X': 'EUR/JPY',

            # Commodities
            'CL=F': 'Pétrole WTI',
            'BZ=F': 'Pétrole Brent',
            'NG=F': 'Gaz Naturel',
            'ZC=F': 'Maïs',
            'ZW=F': 'Blé',
            'ZS=F': 'Soja',
            'KC=F': 'Café',
            'SB=F': 'Sucre',
            'HG=F': 'Cuivre',

            # Indices
            '^FCHI': 'CAC 40',
            '^GSPC': 'S&P 500',
            '^DJI': 'Dow Jones',
            '^IXIC': 'Nasdaq',
            '^RUT': 'Russell 2000',
            '^GDAXI': 'DAX',
            '^FTSE': 'FTSE 100',
            '^IBEX': 'IBEX 35',
            '^FTSEMIB': 'FTSE MIB',
            '^N225': 'Nikkei 225',
            '^HSI': 'Hang Seng',
            '^AXJO': 'ASX 200'
        }
        return names.get(symbol, symbol)

    def load_and_prepare_data(self):
        """
        Charge toutes les données et calcule tous les indicateurs
        """
        print(f"\n{Fore.CYAN}{'='*70}")
        print(f"{Fore.CYAN}📊 CHARGEMENT DES DONNÉES MULTI-MARCHÉS")
        print(f"{Fore.CYAN}   Actions • Forex • Métaux • Commodities • Indices")
        print(f"{Fore.CYAN}{'='*70}\n")

        # Récupérer les données brutes
        raw_data = self.fetcher.fetch_all_stocks_data()

        # Calculer tous les indicateurs pour chaque action
        print(f"\n{Fore.YELLOW}🔧 Calcul des indicateurs techniques...\n")

        for symbol, data in raw_data.items():
            enriched = self.indicators.calculate_all_indicators(data)
            self.enriched_data[symbol] = enriched
            print(f"  ✅ {symbol} - {len(enriched.columns)} indicateurs calculés")

        print(f"\n{Fore.GREEN}✅ Données prêtes : {len(self.enriched_data)} actions avec indicateurs complets\n")

    def get_stock_analysis(self, symbol: str) -> Dict:
        """
        Analyse complète d'une action

        Args:
            symbol: Symbole de l'action (ex: MC.PA)

        Returns:
            Dictionnaire avec toutes les analyses
        """
        if symbol not in self.enriched_data:
            return {'error': f'Action {symbol} non trouvée'}

        df = self.enriched_data[symbol]
        latest_indicators = self.indicators.get_latest_indicators(df)

        # Analyse de la volatilité pour day trading
        volatility = latest_indicators.get('Volatility_5d') or 0
        is_volatile_enough = volatility >= 0.8

        # Analyse du volume
        volume_ratio = latest_indicators.get('Volume_Ratio') or 0
        has_good_volume = volume_ratio >= 1.0

        # Analyse de la tendance
        trend = latest_indicators.get('Trend') or 0
        price_vs_sma5 = latest_indicators.get('Price_vs_SMA5') or 0

        # Calcul du potentiel de gain de 1%
        target_gain_reachable = self._check_1percent_potential(df)

        # Déterminer le signal de trading (BUY/SELL/HOLD)
        signal = self._determine_signal(latest_indicators, trend)

        # Recommandation de turbo (FONCTION 2)
        turbo_recommendation = None
        if signal in ['BUY', 'SELL']:
            current_price = latest_indicators.get('Prix') or 0
            turbos = self.turbos_scraper.find_turbos_for_asset(symbol, signal, current_price)
            if turbos:
                best_turbo = self.turbos_scraper.select_best_turbo(turbos, current_price)
                if best_turbo:
                    turbo_recommendation = {
                        'turbo': best_turbo,
                        'signal': signal,
                        'formatted': self.turbos_scraper.format_turbo_recommendation(best_turbo, current_price)
                    }

        return {
            'symbol': symbol,
            'indicateurs': latest_indicators,
            'volatilite_suffisante': is_volatile_enough,
            'volume_suffisant': has_good_volume,
            'tendance': 'Haussière' if trend == 1 else 'Baissière' if trend == -1 else 'Neutre',
            'signal': signal,
            'potentiel_1pct': target_gain_reachable,
            'score_trading': self._calculate_trading_score(latest_indicators, volatility, volume_ratio),
            'turbo': turbo_recommendation
        }

    def _check_1percent_potential(self, df: pd.DataFrame, lookback_days: int = 10) -> Dict:
        """
        Vérifie combien de fois un gain de 1% était atteignable
        dans les N derniers jours

        Returns:
            Statistiques sur le potentiel de gain
        """
        recent_data = df.tail(lookback_days)

        gains_reached = 0
        max_intraday_gains = []

        for idx, row in recent_data.iterrows():
            # Gain max intrajournalier depuis l'ouverture
            max_gain = ((row['High'] - row['Open']) / row['Open']) * 100
            max_intraday_gains.append(max_gain)

            if max_gain >= 1.0:
                gains_reached += 1

        success_rate = (gains_reached / lookback_days) * 100 if lookback_days > 0 else 0
        avg_max_gain = np.mean(max_intraday_gains) if max_intraday_gains else 0

        return {
            'jours_analyses': lookback_days,
            'jours_1pct_atteint': gains_reached,
            'taux_reussite': round(success_rate, 1),
            'gain_intraday_moyen': round(avg_max_gain, 2),
            'faisable': success_rate >= 50
        }

    def _calculate_trading_score(self, indicators: Dict, volatility: float, volume_ratio: float) -> int:
        """
        Calcule un score de trading sur 100

        Score basé sur :
        - Volatilité optimale (0.8-2.5%)
        - Volume au-dessus de la moyenne
        - RSI dans zone neutre (40-60)
        - Momentum positif
        """
        score = 0

        # Volatilité (30 points max)
        if 0.8 <= volatility <= 2.5:
            score += 30
        elif volatility > 2.5:
            score += 15  # Trop volatile
        elif volatility > 0.5:
            score += 20

        # Volume (25 points max)
        if volume_ratio >= 1.5:
            score += 25
        elif volume_ratio >= 1.2:
            score += 20
        elif volume_ratio >= 1.0:
            score += 15

        # RSI (25 points max)
        rsi = indicators.get('RSI_14') or 50
        if 40 <= rsi <= 60:
            score += 25
        elif 30 <= rsi <= 70:
            score += 20
        elif rsi < 30:
            score += 15  # Survente - potentiel rebond
        else:
            score += 10

        # Tendance et momentum (20 points max)
        trend = indicators.get('Trend') or 0
        momentum = indicators.get('Momentum_10') or 0

        if trend == 1 and momentum > 0:
            score += 20
        elif trend == 1 or momentum > 0:
            score += 15
        elif trend == 0:
            score += 10

        return min(score, 100)

    def _determine_signal(self, indicators: Dict, trend: int) -> str:
        """
        Détermine le signal de trading (BUY/SELL/HOLD)

        Args:
            indicators: Dictionnaire des indicateurs
            trend: Tendance (-1, 0, 1)

        Returns:
            'BUY', 'SELL', ou 'HOLD'
        """
        rsi = indicators.get('RSI_14') or 50
        macd = indicators.get('MACD') or 0
        macd_signal = indicators.get('MACD_Signal') or 0
        momentum = indicators.get('Momentum_10') or 0

        # Compteur de signaux haussiers et baissiers
        buy_signals = 0
        sell_signals = 0

        # RSI
        if rsi < 40:
            buy_signals += 1
        elif rsi > 60:
            sell_signals += 1

        # MACD
        if macd > macd_signal:
            buy_signals += 1
        else:
            sell_signals += 1

        # Tendance
        if trend == 1:
            buy_signals += 1
        elif trend == -1:
            sell_signals += 1

        # Momentum
        if momentum > 0:
            buy_signals += 1
        elif momentum < 0:
            sell_signals += 1

        # Décision finale
        if buy_signals >= 3:
            return 'BUY'
        elif sell_signals >= 3:
            return 'SELL'
        else:
            return 'HOLD'

    def find_best_daily_trading_candidates(self, top_n: int = 3, category: str = None) -> List[Tuple[str, Dict]]:
        """
        Trouve les N meilleures actions pour du day trading

        Args:
            top_n: Nombre de candidats à retourner
            category: Filtrer par catégorie ('action', 'forex', 'metal', 'commodity', 'indice', None=tous)

        Returns:
            Liste de tuples (symbol, analysis)
        """
        if category:
            print(f"\n{Fore.CYAN}🎯 RECHERCHE DES MEILLEURES OPPORTUNITÉS ({category.upper()})\n")
        else:
            print(f"\n{Fore.CYAN}🎯 RECHERCHE DES MEILLEURES OPPORTUNITÉS (TOUS MARCHÉS)\n")

        candidates = []

        for symbol in self.enriched_data.keys():
            # Filtrer par catégorie si spécifié
            if category:
                asset_category = self.get_asset_category(symbol).lower()
                if category == 'action' and not symbol.endswith('.PA'):
                    continue
                elif category == 'forex' and not symbol.endswith('=X'):
                    continue
                elif category == 'metal' and symbol not in ['GC=F', 'SI=F', 'PL=F', 'PA=F']:
                    continue
                elif category == 'commodity' and (not symbol.endswith('=F') or symbol in ['GC=F', 'SI=F', 'PL=F', 'PA=F']):
                    continue
                elif category == 'indice' and not symbol.startswith('^'):
                    continue

            analysis = self.get_stock_analysis(symbol)

            # Filtres de base
            if (analysis.get('volatilite_suffisante') and
                analysis.get('volume_suffisant') and
                analysis.get('potentiel_1pct', {}).get('faisable')):

                candidates.append((symbol, analysis))

        # Trier par score
        candidates.sort(key=lambda x: x[1]['score_trading'], reverse=True)

        return candidates[:top_n]

    def display_best_candidates(self, top_n: int = 3):
        """Affiche les meilleurs candidats de manière formatée"""
        candidates = self.find_best_daily_trading_candidates(top_n)

        if not candidates:
            print(f"{Fore.RED}❌ Aucun candidat ne remplit les critères\n")
            return

        print(f"{Fore.GREEN}✅ TOP {len(candidates)} ACTIONS POUR DAY TRADING :\n")

        for i, (symbol, analysis) in enumerate(candidates, 1):
            self._print_candidate(i, symbol, analysis)

    def _print_candidate(self, rank: int, symbol: str, analysis: Dict):
        """Affiche une action candidate de manière formatée"""
        indicators = analysis['indicateurs']
        potentiel = analysis['potentiel_1pct']
        score = analysis['score_trading']

        # Couleur selon le score
        if score >= 80:
            color = Fore.GREEN
        elif score >= 60:
            color = Fore.YELLOW
        else:
            color = Fore.WHITE

        # Récupérer catégorie et nom
        category = self.get_asset_category(symbol)
        name = self.get_asset_name(symbol)

        print(f"{color}{'='*70}")
        print(f"{color}#{rank} - {symbol} | {name}")
        print(f"{color}{category} | Score: {score}/100 ⭐")
        print(f"{color}{'='*70}\n")

        # Adapter l'affichage du prix selon le type d'actif
        price_unit = "€" if symbol.endswith('.PA') else ""
        print(f"  💰 Prix actuel: {indicators['Prix']}{price_unit}")
        print(f"  📊 Tendance: {analysis['tendance']}")
        print(f"  📈 Volatilité 5j: {indicators.get('Volatility_5d', 'N/A')}%")
        print(f"  📦 Ratio Volume: {indicators.get('Volume_Ratio', 'N/A')}x")
        print(f"  🎯 RSI: {indicators.get('RSI_14', 'N/A')}")

        print(f"\n  🎲 POTENTIEL GAIN 1%:")
        print(f"     Taux de réussite (10 derniers jours): {potentiel['taux_reussite']}%")
        print(f"     Jours où 1% atteint: {potentiel['jours_1pct_atteint']}/{potentiel['jours_analyses']}")
        print(f"     Gain intraday moyen: {potentiel['gain_intraday_moyen']}%")

        print(f"\n  📊 INDICATEURS CLÉS:")
        print(f"     SMA 5: {indicators.get('SMA_5', 'N/A')}€ | Distance: {indicators.get('Price_vs_SMA5', 'N/A')}%")
        print(f"     MACD: {indicators.get('MACD', 'N/A')} | Signal: {indicators.get('MACD_Signal', 'N/A')}")
        print(f"     Stochastic K: {indicators.get('Stochastic_K', 'N/A')}")

        # Afficher le signal de trading
        signal = analysis.get('signal', 'HOLD')
        if signal == 'BUY':
            signal_color = Fore.GREEN
            signal_text = "📈 ACHAT RECOMMANDÉ"
        elif signal == 'SELL':
            signal_color = Fore.RED
            signal_text = "📉 VENTE RECOMMANDÉE"
        else:
            signal_color = Fore.YELLOW
            signal_text = "⏸️  CONSERVER / ATTENDRE"

        print(f"\n  {signal_color}🎯 SIGNAL: {signal_text}{Style.RESET_ALL}")

        # Afficher la recommandation de turbo (FONCTION 2)
        turbo_rec = analysis.get('turbo')
        if turbo_rec:
            print(f"{Fore.CYAN}{turbo_rec['formatted']}{Style.RESET_ALL}")
        elif signal in ['BUY', 'SELL']:
            print(f"\n  ℹ️  Aucun turbo SG trouvé pour {symbol} (ou non disponible)")

        print()

    def answer_question(self, question_type: str, **kwargs):
        """
        Répond à différents types de questions sur les données

        Args:
            question_type: Type de question
            **kwargs: Paramètres de la question
        """
        if question_type == "plus_volatile":
            return self._find_most_volatile(**kwargs)

        elif question_type == "meilleur_momentum":
            return self._find_best_momentum(**kwargs)

        elif question_type == "volume_anormal":
            return self._find_unusual_volume(**kwargs)

        elif question_type == "proche_support":
            return self._find_near_support(**kwargs)

        elif question_type == "gap_ouverture":
            return self._find_gaps(**kwargs)

        else:
            return f"Type de question '{question_type}' non reconnu"

    def _find_most_volatile(self, top_n: int = 5) -> List[Tuple[str, float]]:
        """Trouve les actions les plus volatiles"""
        volatility_data = []

        for symbol, df in self.enriched_data.items():
            vol = df['Volatility_5d'].iloc[-1]
            if pd.notna(vol):
                volatility_data.append((symbol, vol))

        volatility_data.sort(key=lambda x: x[1], reverse=True)
        return volatility_data[:top_n]

    def _find_best_momentum(self, top_n: int = 5) -> List[Tuple[str, float]]:
        """Trouve les actions avec le meilleur momentum"""
        momentum_data = []

        for symbol, df in self.enriched_data.items():
            mom = df['Momentum_10'].iloc[-1]
            if pd.notna(mom):
                momentum_data.append((symbol, mom))

        momentum_data.sort(key=lambda x: x[1], reverse=True)
        return momentum_data[:top_n]

    def _find_unusual_volume(self, min_ratio: float = 1.5, top_n: int = 5) -> List[Tuple[str, float]]:
        """Trouve les actions avec un volume anormal"""
        volume_data = []

        for symbol, df in self.enriched_data.items():
            vol_ratio = df['Volume_Ratio'].iloc[-1]
            if pd.notna(vol_ratio) and vol_ratio >= min_ratio:
                volume_data.append((symbol, vol_ratio))

        volume_data.sort(key=lambda x: x[1], reverse=True)
        return volume_data[:top_n]

    def _find_near_support(self, threshold_pct: float = 2.0) -> List[Tuple[str, float]]:
        """Trouve les actions proches de leur support"""
        near_support = []

        for symbol, df in self.enriched_data.items():
            current_price = df['Close'].iloc[-1]
            support = df['Support_5d'].iloc[-1]

            if pd.notna(support):
                distance = ((current_price - support) / support) * 100
                if 0 <= distance <= threshold_pct:
                    near_support.append((symbol, distance))

        near_support.sort(key=lambda x: x[1])
        return near_support

    def _find_gaps(self, min_gap_pct: float = 0.5) -> List[Tuple[str, float]]:
        """Trouve les actions avec des gaps récents"""
        gaps_data = []

        for symbol, df in self.enriched_data.items():
            gap = df['Gap'].iloc[-1]
            if pd.notna(gap) and abs(gap) >= min_gap_pct:
                gaps_data.append((symbol, gap))

        gaps_data.sort(key=lambda x: abs(x[1]), reverse=True)
        return gaps_data
