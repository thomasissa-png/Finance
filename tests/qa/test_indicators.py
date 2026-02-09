"""
Tests QA: Indicateurs techniques (RSI, MACD, ATR, Trend)
Vérifie les calculs avec des données de référence connues.
"""
import sys
import os
import pytest
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from trading_app.indicators import calculer_rsi, calculer_macd, calculer_atr, calculer_trend


# ============================================================================
# FIXTURES: Données de référence
# ============================================================================

@pytest.fixture
def df_trending_up():
    """DataFrame avec tendance haussière claire (prix en hausse linéaire)"""
    dates = pd.date_range('2025-01-01', periods=50, freq='D')
    prices = [100 + i * 0.5 for i in range(50)]
    return pd.DataFrame({
        'Open': [p - 0.2 for p in prices],
        'High': [p + 0.3 for p in prices],
        'Low': [p - 0.3 for p in prices],
        'Close': prices,
        'Volume': [1000000] * 50
    }, index=dates)


@pytest.fixture
def df_trending_down():
    """DataFrame avec tendance baissière claire"""
    dates = pd.date_range('2025-01-01', periods=50, freq='D')
    prices = [120 - i * 0.5 for i in range(50)]
    return pd.DataFrame({
        'Open': [p + 0.2 for p in prices],
        'High': [p + 0.3 for p in prices],
        'Low': [p - 0.3 for p in prices],
        'Close': prices,
        'Volume': [1000000] * 50
    }, index=dates)


@pytest.fixture
def df_range():
    """DataFrame avec marché en range (oscillation autour de 100)"""
    dates = pd.date_range('2025-01-01', periods=50, freq='D')
    prices = [100 + 2 * np.sin(i * 0.5) for i in range(50)]
    return pd.DataFrame({
        'Open': [p - 0.1 for p in prices],
        'High': [p + 0.5 for p in prices],
        'Low': [p - 0.5 for p in prices],
        'Close': prices,
        'Volume': [1000000] * 50
    }, index=dates)


@pytest.fixture
def df_empty():
    """DataFrame vide"""
    return pd.DataFrame()


@pytest.fixture
def df_too_short():
    """DataFrame avec trop peu de données"""
    return pd.DataFrame({
        'Open': [100, 101],
        'High': [102, 103],
        'Low': [99, 100],
        'Close': [101, 102],
        'Volume': [1000, 1000]
    })


# ============================================================================
# TESTS RSI
# ============================================================================

class TestRSI:
    def test_rsi_trending_up_above_50(self, df_trending_up):
        """RSI en tendance haussière doit être > 50"""
        rsi = calculer_rsi(df_trending_up)
        assert rsi is not None
        assert rsi > 50, f"RSI en uptrend devrait être > 50, got {rsi}"

    def test_rsi_trending_down_below_50(self, df_trending_down):
        """RSI en tendance baissière doit être < 50"""
        rsi = calculer_rsi(df_trending_down)
        assert rsi is not None
        assert rsi < 50, f"RSI en downtrend devrait être < 50, got {rsi}"

    def test_rsi_range_0_100(self, df_trending_up):
        """RSI doit toujours être entre 0 et 100"""
        rsi = calculer_rsi(df_trending_up)
        assert rsi is not None
        assert 0 <= rsi <= 100, f"RSI hors bornes: {rsi}"

    def test_rsi_empty_dataframe(self, df_empty):
        """RSI sur DataFrame vide retourne None"""
        assert calculer_rsi(df_empty) is None

    def test_rsi_too_short(self, df_too_short):
        """RSI avec trop peu de données retourne None"""
        assert calculer_rsi(df_too_short) is None

    def test_rsi_returns_float(self, df_trending_up):
        """RSI doit retourner un float Python (pas numpy)"""
        rsi = calculer_rsi(df_trending_up)
        assert isinstance(rsi, float)

    def test_rsi_custom_period(self, df_trending_up):
        """RSI avec période personnalisée fonctionne"""
        rsi_7 = calculer_rsi(df_trending_up, periode=7)
        rsi_14 = calculer_rsi(df_trending_up, periode=14)
        assert rsi_7 is not None
        assert rsi_14 is not None
        # Les deux sont au-dessus de 50 en uptrend
        assert rsi_7 > 50
        assert rsi_14 > 50


# ============================================================================
# TESTS MACD
# ============================================================================

class TestMACD:
    def test_macd_trending_up_bullish(self, df_trending_up):
        """MACD en uptrend doit donner signal BULLISH"""
        macd_val, signal_val, hist_val, signal_type = calculer_macd(df_trending_up)
        assert macd_val is not None
        assert signal_type in ('BULLISH', 'BULLISH_CROSS')

    def test_macd_trending_down_bearish(self, df_trending_down):
        """MACD en downtrend doit donner signal BEARISH"""
        macd_val, signal_val, hist_val, signal_type = calculer_macd(df_trending_down)
        assert macd_val is not None
        assert signal_type in ('BEARISH', 'BEARISH_CROSS')

    def test_macd_empty(self, df_empty):
        """MACD sur DataFrame vide retourne des None"""
        result = calculer_macd(df_empty)
        assert result == (None, None, None, None)

    def test_macd_too_short(self, df_too_short):
        """MACD avec trop peu de données retourne des None"""
        result = calculer_macd(df_too_short)
        assert result == (None, None, None, None)

    def test_macd_returns_python_floats(self, df_trending_up):
        """MACD doit retourner des floats Python"""
        macd_val, signal_val, hist_val, signal_type = calculer_macd(df_trending_up)
        assert isinstance(macd_val, float)
        assert isinstance(signal_val, float)
        assert isinstance(hist_val, float)
        assert isinstance(signal_type, str)

    def test_macd_histogram_is_difference(self, df_trending_up):
        """Histogram = MACD line - Signal line"""
        macd_val, signal_val, hist_val, _ = calculer_macd(df_trending_up)
        assert abs(hist_val - (macd_val - signal_val)) < 0.001


# ============================================================================
# TESTS ATR
# ============================================================================

class TestATR:
    def test_atr_positive(self, df_trending_up):
        """ATR doit être positif"""
        atr_val, atr_pct = calculer_atr(df_trending_up)
        assert atr_val is not None
        assert atr_val > 0
        assert atr_pct > 0

    def test_atr_pct_reasonable(self, df_trending_up):
        """ATR% doit être raisonnable (< 20% pour des données normales)"""
        atr_val, atr_pct = calculer_atr(df_trending_up)
        assert atr_pct < 20, f"ATR% irréaliste: {atr_pct}"

    def test_atr_empty(self, df_empty):
        """ATR sur DataFrame vide retourne None"""
        result = calculer_atr(df_empty)
        assert result == (None, None)

    def test_atr_too_short(self, df_too_short):
        """ATR avec trop peu de données retourne None"""
        result = calculer_atr(df_too_short)
        assert result == (None, None)

    def test_atr_python_float(self, df_trending_up):
        """ATR retourne des floats Python"""
        atr_val, atr_pct = calculer_atr(df_trending_up)
        assert isinstance(atr_val, float)
        assert isinstance(atr_pct, float)


# ============================================================================
# TESTS TREND
# ============================================================================

class TestTrend:
    def test_trend_up(self, df_trending_up):
        """Tendance haussière = UP"""
        assert calculer_trend(df_trending_up) == 'UP'

    def test_trend_down(self, df_trending_down):
        """Tendance baissière = DOWN"""
        assert calculer_trend(df_trending_down) == 'DOWN'

    def test_trend_range(self, df_range):
        """Marché en range = RANGE"""
        assert calculer_trend(df_range) == 'RANGE'

    def test_trend_empty(self, df_empty):
        """DataFrame vide = RANGE (défaut)"""
        assert calculer_trend(df_empty) == 'RANGE'

    def test_trend_none(self):
        """None = RANGE (défaut)"""
        assert calculer_trend(None) == 'RANGE'

    def test_trend_too_short(self, df_too_short):
        """Trop peu de données = RANGE"""
        assert calculer_trend(df_too_short) == 'RANGE'
