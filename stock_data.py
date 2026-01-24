"""
Module pour récupérer et traiter les données boursières
"""

import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, Dict


class StockDataFetcher:
    """Classe pour récupérer les données boursières via yfinance"""

    def __init__(self):
        self.cache: Dict[str, pd.DataFrame] = {}

    def get_stock_data(self, symbol: str, period: str = "1mo", interval: str = "1d") -> Optional[pd.DataFrame]:
        """
        Récupère les données historiques d'une action

        Args:
            symbol: Symbole de l'action (ex: AAPL, GOOGL)
            period: Période de données (1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max)
            interval: Intervalle des données (1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo)

        Returns:
            DataFrame avec les données OHLCV (Open, High, Low, Close, Volume)
        """
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval)

            if df.empty:
                print(f"❌ Aucune donnée trouvée pour {symbol}")
                return None

            return df

        except Exception as e:
            print(f"❌ Erreur lors de la récupération des données pour {symbol}: {e}")
            return None

    def get_current_price(self, symbol: str) -> Optional[float]:
        """
        Récupère le prix actuel d'une action

        Args:
            symbol: Symbole de l'action

        Returns:
            Prix actuel ou None si erreur
        """
        try:
            ticker = yf.Ticker(symbol)
            data = ticker.history(period="1d", interval="1m")

            if data.empty:
                return None

            return float(data['Close'].iloc[-1])

        except Exception as e:
            print(f"❌ Erreur lors de la récupération du prix pour {symbol}: {e}")
            return None

    def get_stock_info(self, symbol: str) -> Optional[Dict]:
        """
        Récupère les informations générales d'une action

        Args:
            symbol: Symbole de l'action

        Returns:
            Dictionnaire avec les informations de l'action
        """
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info

            # Extraire les informations pertinentes
            relevant_info = {
                'symbol': symbol,
                'name': info.get('longName', 'N/A'),
                'sector': info.get('sector', 'N/A'),
                'industry': info.get('industry', 'N/A'),
                'current_price': info.get('currentPrice', 'N/A'),
                'market_cap': info.get('marketCap', 'N/A'),
                'pe_ratio': info.get('trailingPE', 'N/A'),
                '52w_high': info.get('fiftyTwoWeekHigh', 'N/A'),
                '52w_low': info.get('fiftyTwoWeekLow', 'N/A'),
            }

            return relevant_info

        except Exception as e:
            print(f"❌ Erreur lors de la récupération des infos pour {symbol}: {e}")
            return None

    def is_market_open(self) -> bool:
        """
        Vérifie si le marché américain est ouvert

        Returns:
            True si le marché est ouvert, False sinon
        """
        now = datetime.now()

        # Vérifier si c'est un jour de semaine (lundi=0, dimanche=6)
        if now.weekday() >= 5:  # Samedi ou dimanche
            return False

        # Heures de marché: 9:30 AM - 4:00 PM EST
        # Note: Cette vérification est simplifiée et ne prend pas en compte les jours fériés
        market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)

        return market_open <= now <= market_close
