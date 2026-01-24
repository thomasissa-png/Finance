"""
Module de récupération des données Euronext Paris
Fonction 1 : Accès aux données des 20 derniers jours pour backtesting
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import yaml


class EuronextDataFetcher:
    """Récupère les données du marché Euronext Paris"""

    def __init__(self, config_path: str = "config.yaml"):
        """Initialise le fetcher avec la configuration"""
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

        self.stocks = self.config['stocks']
        self.days_history = self.config['backtesting']['days_history']
        self.data_cache: Dict[str, pd.DataFrame] = {}

    def fetch_all_stocks_data(self) -> Dict[str, pd.DataFrame]:
        """
        Récupère les données de toutes les actions configurées

        Returns:
            Dictionnaire {symbol: DataFrame} avec les données OHLCV
        """
        print(f"\n📊 Récupération des données pour {len(self.stocks)} actions...")
        print(f"📅 Période : {self.days_history} derniers jours\n")

        all_data = {}

        for symbol in self.stocks:
            print(f"  📈 {symbol}...", end=" ")
            data = self._fetch_single_stock(symbol)

            if data is not None and not data.empty:
                all_data[symbol] = data
                print(f"✅ {len(data)} jours récupérés")
            else:
                print(f"❌ Échec")

        self.data_cache = all_data
        print(f"\n✅ Données récupérées pour {len(all_data)}/{len(self.stocks)} actions\n")

        return all_data

    def _fetch_single_stock(self, symbol: str) -> Optional[pd.DataFrame]:
        """Récupère les données d'une action unique"""
        try:
            # Calculer la période
            end_date = datetime.now()
            start_date = end_date - timedelta(days=self.days_history + 10)  # +10 pour marge

            # Télécharger les données
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_date, end=end_date, interval="1d")

            if df.empty:
                return None

            # Garder seulement les N derniers jours de trading
            df = df.tail(self.days_history)

            # Nettoyer les données
            df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
            df.columns = ['Open', 'High', 'Low', 'Close', 'Volume']

            return df

        except Exception as e:
            print(f"\n❌ Erreur pour {symbol}: {e}")
            return None

    def get_stock_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """
        Récupère les données d'une action depuis le cache ou les télécharge

        Args:
            symbol: Symbole de l'action (ex: MC.PA)

        Returns:
            DataFrame avec les données
        """
        if symbol in self.data_cache:
            return self.data_cache[symbol]

        # Télécharger si pas en cache
        data = self._fetch_single_stock(symbol)
        if data is not None:
            self.data_cache[symbol] = data

        return data

    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Récupère le dernier prix de clôture"""
        data = self.get_stock_data(symbol)
        if data is not None and not data.empty:
            return float(data['Close'].iloc[-1])
        return None

    def get_price_change(self, symbol: str, days: int = 1) -> Optional[float]:
        """
        Calcule le changement de prix sur N jours

        Args:
            symbol: Symbole de l'action
            days: Nombre de jours

        Returns:
            Variation en pourcentage
        """
        data = self.get_stock_data(symbol)
        if data is None or len(data) < days + 1:
            return None

        current_price = data['Close'].iloc[-1]
        past_price = data['Close'].iloc[-(days + 1)]

        return ((current_price - past_price) / past_price) * 100

    def get_volume_average(self, symbol: str, days: int = 20) -> Optional[float]:
        """Calcule le volume moyen sur N jours"""
        data = self.get_stock_data(symbol)
        if data is None or len(data) < days:
            return None

        return float(data['Volume'].tail(days).mean())

    def get_intraday_volatility(self, symbol: str, days: int = 5) -> Optional[float]:
        """
        Calcule la volatilité intrajournalière moyenne (High-Low)

        Args:
            symbol: Symbole de l'action
            days: Nombre de jours pour le calcul

        Returns:
            Volatilité moyenne en pourcentage
        """
        data = self.get_stock_data(symbol)
        if data is None or len(data) < days:
            return None

        # Calcul de la volatilité intrajournalière
        data_subset = data.tail(days).copy()
        data_subset['IntraDayRange'] = ((data_subset['High'] - data_subset['Low']) / data_subset['Low']) * 100

        return float(data_subset['IntraDayRange'].mean())

    def get_summary(self, symbol: str) -> Dict:
        """
        Résumé des données d'une action

        Returns:
            Dictionnaire avec les statistiques clés
        """
        data = self.get_stock_data(symbol)
        if data is None:
            return {}

        latest_price = data['Close'].iloc[-1]
        price_change_1d = self.get_price_change(symbol, 1)
        price_change_5d = self.get_price_change(symbol, 5)
        avg_volume = self.get_volume_average(symbol, 20)
        volatility = self.get_intraday_volatility(symbol, 5)

        return {
            'symbol': symbol,
            'prix_actuel': round(latest_price, 2),
            'variation_1j': round(price_change_1d, 2) if price_change_1d else None,
            'variation_5j': round(price_change_5d, 2) if price_change_5d else None,
            'volume_moyen': int(avg_volume) if avg_volume else None,
            'volatilite_5j': round(volatility, 2) if volatility else None,
            'nb_jours_data': len(data)
        }

    def export_to_csv(self, output_dir: str = "data"):
        """Exporte toutes les données en CSV"""
        import os
        os.makedirs(output_dir, exist_ok=True)

        for symbol, data in self.data_cache.items():
            filename = f"{output_dir}/{symbol.replace('.', '_')}.csv"
            data.to_csv(filename)
            print(f"✅ Exporté : {filename}")
