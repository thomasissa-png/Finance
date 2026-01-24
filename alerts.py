"""
Module pour générer des alertes et signaux de trading
"""

import pandas as pd
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime
from indicators import TechnicalIndicators


@dataclass
class TradingSignal:
    """Représente un signal de trading"""
    symbol: str
    signal_type: str  # 'BUY', 'SELL', 'HOLD'
    strength: float  # 0.0 à 1.0
    reason: str
    price: float
    timestamp: datetime
    indicators: Dict[str, float]

    def __str__(self):
        emoji = "🟢" if self.signal_type == "BUY" else "🔴" if self.signal_type == "SELL" else "🟡"
        strength_bars = "█" * int(self.strength * 10)
        return (f"{emoji} {self.signal_type} {self.symbol} @ ${self.price:.2f}\n"
                f"   Force: {strength_bars} ({self.strength:.1%})\n"
                f"   Raison: {self.reason}")


class AlertGenerator:
    """Génère des alertes de trading basées sur l'analyse technique"""

    def __init__(self, config: Dict):
        self.config = config
        self.indicators = TechnicalIndicators()

    def analyze_stock(self, symbol: str, data: pd.DataFrame) -> Optional[TradingSignal]:
        """
        Analyse une action et génère un signal de trading

        Args:
            symbol: Symbole de l'action
            data: DataFrame avec les données historiques

        Returns:
            TradingSignal ou None
        """
        if data is None or len(data) < 50:
            return None

        # Calculer tous les indicateurs
        indicators_data = self._calculate_all_indicators(data)

        # Analyser les signaux
        signals = []

        if self.config['alerts']['rsi_signals']:
            rsi_signal = self._analyze_rsi(indicators_data)
            if rsi_signal:
                signals.append(rsi_signal)

        if self.config['alerts']['macd_signals']:
            macd_signal = self._analyze_macd(indicators_data)
            if macd_signal:
                signals.append(macd_signal)

        if self.config['alerts']['moving_average_crossover']:
            ma_signal = self._analyze_moving_averages(indicators_data)
            if ma_signal:
                signals.append(ma_signal)

        # Agréger les signaux
        final_signal = self._aggregate_signals(symbol, signals, indicators_data, data['Close'].iloc[-1])

        return final_signal

    def _calculate_all_indicators(self, data: pd.DataFrame) -> Dict:
        """Calcule tous les indicateurs techniques"""
        rsi_period = self.config['indicators']['rsi']['period']
        macd_fast = self.config['indicators']['macd']['fast_period']
        macd_slow = self.config['indicators']['macd']['slow_period']
        macd_signal = self.config['indicators']['macd']['signal_period']
        ma_short = self.config['indicators']['moving_averages']['short_period']
        ma_long = self.config['indicators']['moving_averages']['long_period']

        rsi = self.indicators.calculate_rsi(data, rsi_period)
        macd_line, signal_line, histogram = self.indicators.calculate_macd(data, macd_fast, macd_slow, macd_signal)
        short_ma, long_ma = self.indicators.calculate_moving_averages(data, ma_short, ma_long)
        support, resistance = self.indicators.calculate_support_resistance(data)
        volume_trend = self.indicators.calculate_volume_trend(data)
        momentum = self.indicators.calculate_price_momentum(data)

        return {
            'rsi': rsi.iloc[-1] if not rsi.empty else None,
            'rsi_prev': rsi.iloc[-2] if len(rsi) > 1 else None,
            'macd': macd_line.iloc[-1] if not macd_line.empty else None,
            'macd_signal': signal_line.iloc[-1] if not signal_line.empty else None,
            'macd_histogram': histogram.iloc[-1] if not histogram.empty else None,
            'macd_histogram_prev': histogram.iloc[-2] if len(histogram) > 1 else None,
            'short_ma': short_ma.iloc[-1] if not short_ma.empty else None,
            'long_ma': long_ma.iloc[-1] if not long_ma.empty else None,
            'short_ma_prev': short_ma.iloc[-2] if len(short_ma) > 1 else None,
            'long_ma_prev': long_ma.iloc[-2] if len(long_ma) > 1 else None,
            'current_price': data['Close'].iloc[-1],
            'support': support,
            'resistance': resistance,
            'volume_trend': volume_trend,
            'momentum': momentum
        }

    def _analyze_rsi(self, indicators: Dict) -> Optional[Dict]:
        """Analyse le RSI pour générer un signal"""
        rsi = indicators.get('rsi')
        if rsi is None:
            return None

        oversold = self.config['indicators']['rsi']['oversold']
        overbought = self.config['indicators']['rsi']['overbought']

        if rsi < oversold:
            strength = (oversold - rsi) / oversold
            return {
                'type': 'BUY',
                'strength': min(strength, 1.0),
                'reason': f'RSI en survente ({rsi:.1f})'
            }
        elif rsi > overbought:
            strength = (rsi - overbought) / (100 - overbought)
            return {
                'type': 'SELL',
                'strength': min(strength, 1.0),
                'reason': f'RSI en surachat ({rsi:.1f})'
            }

        return None

    def _analyze_macd(self, indicators: Dict) -> Optional[Dict]:
        """Analyse le MACD pour générer un signal"""
        macd = indicators.get('macd')
        macd_signal = indicators.get('macd_signal')
        histogram = indicators.get('macd_histogram')
        histogram_prev = indicators.get('macd_histogram_prev')

        if None in [macd, macd_signal, histogram, histogram_prev]:
            return None

        # Croisement haussier (MACD croise la ligne de signal vers le haut)
        if histogram_prev < 0 and histogram > 0:
            strength = min(abs(histogram) / abs(macd_signal), 1.0)
            return {
                'type': 'BUY',
                'strength': strength,
                'reason': 'MACD croisement haussier'
            }

        # Croisement baissier (MACD croise la ligne de signal vers le bas)
        elif histogram_prev > 0 and histogram < 0:
            strength = min(abs(histogram) / abs(macd_signal), 1.0)
            return {
                'type': 'SELL',
                'strength': strength,
                'reason': 'MACD croisement baissier'
            }

        return None

    def _analyze_moving_averages(self, indicators: Dict) -> Optional[Dict]:
        """Analyse les moyennes mobiles pour générer un signal"""
        short_ma = indicators.get('short_ma')
        long_ma = indicators.get('long_ma')
        short_ma_prev = indicators.get('short_ma_prev')
        long_ma_prev = indicators.get('long_ma_prev')

        if None in [short_ma, long_ma, short_ma_prev, long_ma_prev]:
            return None

        # Golden Cross (MA courte croise MA longue vers le haut)
        if short_ma_prev < long_ma_prev and short_ma > long_ma:
            strength = (short_ma - long_ma) / long_ma
            return {
                'type': 'BUY',
                'strength': min(abs(strength) * 10, 1.0),
                'reason': 'Golden Cross (MA20 > MA50)'
            }

        # Death Cross (MA courte croise MA longue vers le bas)
        elif short_ma_prev > long_ma_prev and short_ma < long_ma:
            strength = (long_ma - short_ma) / long_ma
            return {
                'type': 'SELL',
                'strength': min(abs(strength) * 10, 1.0),
                'reason': 'Death Cross (MA20 < MA50)'
            }

        return None

    def _aggregate_signals(self, symbol: str, signals: List[Dict], indicators: Dict, price: float) -> Optional[TradingSignal]:
        """Agrège plusieurs signaux en un signal final"""
        if not signals:
            return None

        # Compter les signaux d'achat et de vente
        buy_signals = [s for s in signals if s['type'] == 'BUY']
        sell_signals = [s for s in signals if s['type'] == 'SELL']

        # Déterminer le signal dominant
        if len(buy_signals) > len(sell_signals):
            signal_type = 'BUY'
            avg_strength = sum(s['strength'] for s in buy_signals) / len(buy_signals)
            reasons = ', '.join(s['reason'] for s in buy_signals)
        elif len(sell_signals) > len(buy_signals):
            signal_type = 'SELL'
            avg_strength = sum(s['strength'] for s in sell_signals) / len(sell_signals)
            reasons = ', '.join(s['reason'] for s in sell_signals)
        else:
            # Égalité - utiliser la force moyenne pour décider
            buy_strength = sum(s['strength'] for s in buy_signals) / len(buy_signals) if buy_signals else 0
            sell_strength = sum(s['strength'] for s in sell_signals) / len(sell_signals) if sell_signals else 0

            if buy_strength > sell_strength:
                signal_type = 'BUY'
                avg_strength = buy_strength
                reasons = ', '.join(s['reason'] for s in buy_signals)
            elif sell_strength > buy_strength:
                signal_type = 'SELL'
                avg_strength = sell_strength
                reasons = ', '.join(s['reason'] for s in sell_signals)
            else:
                signal_type = 'HOLD'
                avg_strength = 0.5
                reasons = 'Signaux contradictoires'

        # Vérifier le seuil minimum
        min_strength = self.config['alerts']['min_signal_strength']
        if avg_strength < min_strength and signal_type != 'HOLD':
            return None

        return TradingSignal(
            symbol=symbol,
            signal_type=signal_type,
            strength=avg_strength,
            reason=reasons,
            price=price,
            timestamp=datetime.now(),
            indicators={
                'RSI': indicators.get('rsi'),
                'MACD': indicators.get('macd'),
                'MA20': indicators.get('short_ma'),
                'MA50': indicators.get('long_ma'),
                'Support': indicators.get('support'),
                'Resistance': indicators.get('resistance'),
                'Momentum': indicators.get('momentum')
            }
        )
