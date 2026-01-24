"""
Module pour calculer les indicateurs techniques
"""

import pandas as pd
import numpy as np
from typing import Tuple, Optional


class TechnicalIndicators:
    """Classe pour calculer les indicateurs techniques courants"""

    @staticmethod
    def calculate_rsi(data: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        Calcule le RSI (Relative Strength Index)

        Args:
            data: DataFrame avec une colonne 'Close'
            period: Période de calcul (défaut: 14)

        Returns:
            Series avec les valeurs RSI
        """
        delta = data['Close'].diff()

        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    @staticmethod
    def calculate_macd(data: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Calcule le MACD (Moving Average Convergence Divergence)

        Args:
            data: DataFrame avec une colonne 'Close'
            fast: Période EMA rapide (défaut: 12)
            slow: Période EMA lente (défaut: 26)
            signal: Période de la ligne de signal (défaut: 9)

        Returns:
            Tuple (MACD line, Signal line, Histogram)
        """
        exp1 = data['Close'].ewm(span=fast, adjust=False).mean()
        exp2 = data['Close'].ewm(span=slow, adjust=False).mean()

        macd_line = exp1 - exp2
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line

        return macd_line, signal_line, histogram

    @staticmethod
    def calculate_moving_averages(data: pd.DataFrame, short_period: int = 20, long_period: int = 50) -> Tuple[pd.Series, pd.Series]:
        """
        Calcule les moyennes mobiles simples

        Args:
            data: DataFrame avec une colonne 'Close'
            short_period: Période MA courte (défaut: 20)
            long_period: Période MA longue (défaut: 50)

        Returns:
            Tuple (Short MA, Long MA)
        """
        short_ma = data['Close'].rolling(window=short_period).mean()
        long_ma = data['Close'].rolling(window=long_period).mean()

        return short_ma, long_ma

    @staticmethod
    def calculate_bollinger_bands(data: pd.DataFrame, period: int = 20, std_dev: int = 2) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Calcule les bandes de Bollinger

        Args:
            data: DataFrame avec une colonne 'Close'
            period: Période de la moyenne mobile (défaut: 20)
            std_dev: Nombre d'écarts-types (défaut: 2)

        Returns:
            Tuple (Upper band, Middle band, Lower band)
        """
        middle_band = data['Close'].rolling(window=period).mean()
        std = data['Close'].rolling(window=period).std()

        upper_band = middle_band + (std * std_dev)
        lower_band = middle_band - (std * std_dev)

        return upper_band, middle_band, lower_band

    @staticmethod
    def calculate_support_resistance(data: pd.DataFrame, window: int = 20) -> Tuple[float, float]:
        """
        Identifie les niveaux de support et résistance basiques

        Args:
            data: DataFrame avec colonnes 'High' et 'Low'
            window: Période de calcul

        Returns:
            Tuple (Support level, Resistance level)
        """
        recent_data = data.tail(window)

        support = recent_data['Low'].min()
        resistance = recent_data['High'].max()

        return support, resistance

    @staticmethod
    def calculate_volume_trend(data: pd.DataFrame, period: int = 20) -> str:
        """
        Analyse la tendance du volume

        Args:
            data: DataFrame avec une colonne 'Volume'
            period: Période de comparaison

        Returns:
            'increasing', 'decreasing', ou 'stable'
        """
        if len(data) < period:
            return 'insufficient_data'

        avg_volume = data['Volume'].rolling(window=period).mean()
        recent_avg = avg_volume.iloc[-5:].mean()
        previous_avg = avg_volume.iloc[-period:-5].mean()

        if recent_avg > previous_avg * 1.2:
            return 'increasing'
        elif recent_avg < previous_avg * 0.8:
            return 'decreasing'
        else:
            return 'stable'

    @staticmethod
    def calculate_price_momentum(data: pd.DataFrame, period: int = 10) -> float:
        """
        Calcule le momentum du prix

        Args:
            data: DataFrame avec une colonne 'Close'
            period: Période de calcul

        Returns:
            Valeur du momentum (pourcentage de changement)
        """
        if len(data) < period:
            return 0.0

        current_price = data['Close'].iloc[-1]
        past_price = data['Close'].iloc[-period]

        momentum = ((current_price - past_price) / past_price) * 100

        return momentum
