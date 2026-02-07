"""
Module de calcul des indicateurs techniques
Tous les indicateurs clés pour le backtesting et l'analyse
"""

import pandas as pd
import numpy as np
from typing import Tuple, Dict


class TechnicalIndicators:
    """Calcule tous les indicateurs techniques pour le trading"""

    @staticmethod
    def calculate_all_indicators(data: pd.DataFrame) -> pd.DataFrame:
        """
        Calcule TOUS les indicateurs techniques sur un DataFrame

        Args:
            data: DataFrame avec colonnes OHLCV

        Returns:
            DataFrame enrichi avec tous les indicateurs
        """
        df = data.copy()

        # === MOYENNES MOBILES ===
        df['SMA_5'] = df['Close'].rolling(window=5).mean()
        df['SMA_10'] = df['Close'].rolling(window=10).mean()
        df['SMA_20'] = df['Close'].rolling(window=20).mean()

        df['EMA_5'] = df['Close'].ewm(span=5, adjust=False).mean()
        df['EMA_10'] = df['Close'].ewm(span=10, adjust=False).mean()
        df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()

        # === RSI ===
        df['RSI_14'] = TechnicalIndicators.calculate_rsi(df, period=14)

        # === MACD ===
        macd, signal, histogram = TechnicalIndicators.calculate_macd(df)
        df['MACD'] = macd
        df['MACD_Signal'] = signal
        df['MACD_Histogram'] = histogram

        # === BANDES DE BOLLINGER ===
        upper, middle, lower = TechnicalIndicators.calculate_bollinger_bands(df, period=20, std_dev=2)
        df['BB_Upper'] = upper
        df['BB_Middle'] = middle
        df['BB_Lower'] = lower
        df['BB_Width'] = ((upper - lower) / middle) * 100

        # === ATR (Average True Range) - Volatilité ===
        df['ATR_14'] = TechnicalIndicators.calculate_atr(df, period=14)

        # === STOCHASTIC ===
        k, d = TechnicalIndicators.calculate_stochastic(df)
        df['Stochastic_K'] = k
        df['Stochastic_D'] = d

        # === MOMENTUM ===
        df['Momentum_10'] = TechnicalIndicators.calculate_momentum(df, period=10)

        # === ROC (Rate of Change) ===
        df['ROC_10'] = TechnicalIndicators.calculate_roc(df, period=10)

        # === VOLUME ===
        df['Volume_SMA_20'] = df['Volume'].rolling(window=20).mean()
        df['Volume_Ratio'] = df['Volume'] / df['Volume_SMA_20']

        # === VOLATILITÉ INTRAJOURNALIÈRE ===
        df['IntraDay_Range'] = ((df['High'] - df['Low']) / df['Low']) * 100
        df['Volatility_5d'] = df['IntraDay_Range'].rolling(window=5).mean()

        # === GAP (Écart à l'ouverture) ===
        df['Gap'] = ((df['Open'] - df['Close'].shift(1)) / df['Close'].shift(1)) * 100

        # === SUPPORT / RESISTANCE ===
        df['Support_5d'] = df['Low'].rolling(window=5).min()
        df['Resistance_5d'] = df['High'].rolling(window=5).max()

        # === DISTANCE DU PRIX AUX MOYENNES ===
        df['Price_vs_SMA5'] = ((df['Close'] - df['SMA_5']) / df['SMA_5']) * 100
        df['Price_vs_SMA20'] = ((df['Close'] - df['SMA_20']) / df['SMA_20']) * 100

        # === TENDANCE ===
        df['Trend_SMA'] = TechnicalIndicators.identify_trend(df['Close'], df['SMA_20'])

        return df

    @staticmethod
    def calculate_rsi(data: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calcule le RSI"""
        delta = data['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def calculate_macd(data: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """Calcule le MACD"""
        exp1 = data['Close'].ewm(span=fast, adjust=False).mean()
        exp2 = data['Close'].ewm(span=slow, adjust=False).mean()

        macd_line = exp1 - exp2
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line

        return macd_line, signal_line, histogram

    @staticmethod
    def calculate_bollinger_bands(data: pd.DataFrame, period: int = 20, std_dev: int = 2) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """Calcule les bandes de Bollinger"""
        middle_band = data['Close'].rolling(window=period).mean()
        std = data['Close'].rolling(window=period).std()

        upper_band = middle_band + (std * std_dev)
        lower_band = middle_band - (std * std_dev)

        return upper_band, middle_band, lower_band

    @staticmethod
    def calculate_atr(data: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calcule l'ATR (Average True Range)"""
        high_low = data['High'] - data['Low']
        high_close = np.abs(data['High'] - data['Close'].shift())
        low_close = np.abs(data['Low'] - data['Close'].shift())

        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = true_range.rolling(window=period).mean()

        return atr

    @staticmethod
    def calculate_stochastic(data: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> Tuple[pd.Series, pd.Series]:
        """Calcule l'oscillateur Stochastic"""
        low_min = data['Low'].rolling(window=k_period).min()
        high_max = data['High'].rolling(window=k_period).max()

        k = 100 * ((data['Close'] - low_min) / (high_max - low_min))
        d = k.rolling(window=d_period).mean()

        return k, d

    @staticmethod
    def calculate_momentum(data: pd.DataFrame, period: int = 10) -> pd.Series:
        """Calcule le momentum"""
        return ((data['Close'] - data['Close'].shift(period)) / data['Close'].shift(period)) * 100

    @staticmethod
    def calculate_roc(data: pd.DataFrame, period: int = 10) -> pd.Series:
        """Calcule le Rate of Change"""
        return ((data['Close'] - data['Close'].shift(period)) / data['Close'].shift(period)) * 100

    @staticmethod
    def identify_trend(price: pd.Series, ma: pd.Series) -> pd.Series:
        """Identifie la tendance (1=haussière, -1=baissière, 0=neutre)"""
        trend = pd.Series(0, index=price.index)
        trend[price > ma] = 1
        trend[price < ma] = -1
        return trend

    @staticmethod
    def get_latest_indicators(df: pd.DataFrame) -> Dict:
        """
        Extrait les derniers indicateurs dans un dictionnaire

        Args:
            df: DataFrame avec tous les indicateurs calculés

        Returns:
            Dictionnaire avec les dernières valeurs
        """
        if df.empty:
            return {}

        latest = df.iloc[-1]

        indicators = {
            'Prix': round(latest['Close'], 2),
            'SMA_5': round(latest['SMA_5'], 2) if pd.notna(latest['SMA_5']) else None,
            'SMA_10': round(latest['SMA_10'], 2) if pd.notna(latest['SMA_10']) else None,
            'SMA_20': round(latest['SMA_20'], 2) if pd.notna(latest['SMA_20']) else None,
            'EMA_5': round(latest['EMA_5'], 2) if pd.notna(latest['EMA_5']) else None,
            'RSI_14': round(latest['RSI_14'], 2) if pd.notna(latest['RSI_14']) else None,
            'MACD': round(latest['MACD'], 3) if pd.notna(latest['MACD']) else None,
            'MACD_Signal': round(latest['MACD_Signal'], 3) if pd.notna(latest['MACD_Signal']) else None,
            'MACD_Histogram': round(latest['MACD_Histogram'], 3) if pd.notna(latest['MACD_Histogram']) else None,
            'BB_Upper': round(latest['BB_Upper'], 2) if pd.notna(latest['BB_Upper']) else None,
            'BB_Lower': round(latest['BB_Lower'], 2) if pd.notna(latest['BB_Lower']) else None,
            'ATR_14': round(latest['ATR_14'], 2) if pd.notna(latest['ATR_14']) else None,
            'Stochastic_K': round(latest['Stochastic_K'], 2) if pd.notna(latest['Stochastic_K']) else None,
            'Stochastic_D': round(latest['Stochastic_D'], 2) if pd.notna(latest['Stochastic_D']) else None,
            'Momentum_10': round(latest['Momentum_10'], 2) if pd.notna(latest['Momentum_10']) else None,
            'Volume_Ratio': round(latest['Volume_Ratio'], 2) if pd.notna(latest['Volume_Ratio']) else None,
            'Volatility_5d': round(latest['Volatility_5d'], 2) if pd.notna(latest['Volatility_5d']) else None,
            'Gap': round(latest['Gap'], 2) if pd.notna(latest['Gap']) else None,
            'Price_vs_SMA5': round(latest['Price_vs_SMA5'], 2) if pd.notna(latest['Price_vs_SMA5']) else None,
            'Trend': int(latest['Trend_SMA']) if pd.notna(latest['Trend_SMA']) else 0
        }

        return indicators
