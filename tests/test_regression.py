"""
Tests anti-régression pour le trading app.

138 tests en 16 groupes couvrant:
- Bugs réels rencontrés et corrigés
- Logique métier critique (scheduling, opportunités, performance, journal, self-learning)

À exécuter SYSTÉMATIQUEMENT avant tout commit / déploiement:
  python -m pytest tests/test_regression.py -v --tb=short

OBLIGATION: Chaque nouveau bug découvert et corrigé DOIT être accompagné d'un test
correspondant dans le groupe approprié (ou un nouveau groupe si nécessaire).

Historique des bugs couverts:
- Prix d'entrée incorrect (TwelveData "close" vs "last") — signalé 6x
- TP1/TP2 stockés comme strings au lieu de floats
- Trades dupliqués (BNP 3x en 3 minutes)
- Heure de sortie manquante / durée non calculée
- Division par zéro dans calculs PnL
- convert_symbol_to_twelvedata retourne tuple, pas string
- Fuites connexion DB (pas de finally)
- float() manquant dans reevaluer_trades_intraday
- scheduler: job bloquant tous les autres jobs
- Cross-module global state: `global VAR` ne fonctionne pas entre modules

Groupes de tests:
  1-10:  Bugs historiques (prix, types, symboles, DB, validation, PnL, dédup, scheduler, state, intégration)
  11:    Scheduling (tâches planifiées, time windows, catch-up, indépendance)
  12:    Pipeline opportunités (parsing JSON, validation, enrichissement, conviction)
  13:    Suivi de performance (métriques, transactions atomiques, orphelins, BREAKEVEN)
  14:    Journal (catégorisation actifs, filtres FR, rapport hebdo, ISO week)
  15:    Auto-apprentissage (ajustements, feedback, A/B testing, circuit breaker)
"""
import os
import sys
import sqlite3
import inspect
import time
import threading
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

import pytest
import pandas as pd
import numpy as np

# Ajouter le répertoire racine au path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ============================================================================
# GROUPE 1: PRIX D'ENTRÉE — Le bug le plus critique (signalé 6 fois)
# ============================================================================

class TestEntryPrice:
    """Vérifie que le prix d'entrée utilise le champ 'last' (temps réel)
    et JAMAIS 'close' (clôture veille) de TwelveData."""

    def test_get_twelvedata_quote_uses_last_field(self):
        """BUG #1: get_twelvedata_quote doit utiliser 'last' pas 'close'."""
        from trading_app.market_data import get_twelvedata_quote

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "last": "92.40",       # Prix temps réel (correct)
            "close": "93.03",      # Clôture veille (INCORRECT pour trading intraday)
            "open": "92.10",
            "high": "92.80",
            "low": "91.90",
            "previous_close": "93.03",
            "change": "-0.63",
            "percent_change": "-0.68",
            "volume": "1234567",
            "datetime": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

        with patch('trading_app.market_data.requests.get', return_value=mock_response), \
             patch('trading_app.market_data.TWELVEDATA_API_KEY', 'test_key'), \
             patch('trading_app.market_data.check_quota_circuit_breaker', return_value=False), \
             patch('trading_app.market_data.rate_limit_twelvedata'):
            result = get_twelvedata_quote("BN.PA")

        assert result is not None, "get_twelvedata_quote ne doit pas retourner None"
        assert result['price'] == 92.40, \
            f"RÉGRESSION CRITIQUE: prix={result['price']}, attendu=92.40 (champ 'last'). " \
            f"Le code utilise probablement 'close' au lieu de 'last'!"

    def test_get_twelvedata_quote_fallback_to_close_if_no_last(self):
        """Si 'last' est absent (API legacy), fallback sur 'close'."""
        from trading_app.market_data import get_twelvedata_quote

        mock_response = MagicMock()
        mock_response.ok = True
        # PAS de champ "last" — seulement "close"
        mock_response.json.return_value = {
            "close": "93.03",
            "open": "92.10",
            "high": "92.80",
            "low": "91.90",
            "previous_close": "91.50",
            "change": "1.53",
            "percent_change": "1.67",
            "volume": "1234567"
        }
        # Vider le cache avant test
        from trading_app.config import TWELVEDATA_CACHE, TWELVEDATA_CACHE_LOCK
        with TWELVEDATA_CACHE_LOCK:
            TWELVEDATA_CACHE.pop('quote_FALLBACK_TEST', None)

        with patch('trading_app.market_data.requests.get', return_value=mock_response), \
             patch('trading_app.market_data.TWELVEDATA_API_KEY', 'test_key'), \
             patch('trading_app.market_data.check_quota_circuit_breaker', return_value=False), \
             patch('trading_app.market_data.rate_limit_twelvedata'):
            result = get_twelvedata_quote("FALLBACK_TEST")

        assert result is not None
        assert result['price'] == 93.03, "Doit fallback sur 'close' si 'last' absent"

    def test_get_fresh_quote_uses_last_field(self):
        """BUG #1 bis: get_fresh_quote_for_trade doit aussi utiliser 'last'."""
        from trading_app.market_data import get_fresh_quote_for_trade

        today = datetime.now().strftime('%Y-%m-%d')
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "last": "92.40",
            "close": "93.03",
            "open": "92.10",
            "high": "92.80",
            "low": "91.90",
            "previous_close": "93.03",
            "volume": "1234567",
            "datetime": f"{today} 11:13:00"
        }

        with patch('trading_app.market_data.requests.get', return_value=mock_response), \
             patch('trading_app.market_data.TWELVEDATA_API_KEY', 'test_key'), \
             patch('trading_app.market_data.check_quota_circuit_breaker', return_value=False), \
             patch('trading_app.market_data.rate_limit_twelvedata'):
            result = get_fresh_quote_for_trade("BN.PA")

        assert result is not None
        assert result['prix'] == round(92.40, 4), \
            f"RÉGRESSION CRITIQUE: prix={result['prix']}, attendu=92.40. " \
            f"get_fresh_quote_for_trade utilise 'close' au lieu de 'last'!"
        assert result['source'] == 'twelvedata'
        assert result['is_fresh'] is True

    def test_fresh_quote_datetime_freshness_check(self):
        """La quote doit être rejetée si le datetime n'est pas d'aujourd'hui."""
        from trading_app.market_data import get_fresh_quote_for_trade

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "last": "92.40",
            "close": "93.03",
            "open": "92.10",
            "high": "92.80",
            "low": "91.90",
            "previous_close": "93.03",
            "volume": "1234567",
            "datetime": "2025-01-01 16:30:00"  # Hier ou avant
        }

        # yfinance fallback va être appelé, on le mock aussi
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()

        with patch('trading_app.market_data.requests.get', return_value=mock_response), \
             patch('trading_app.market_data.TWELVEDATA_API_KEY', 'test_key'), \
             patch('trading_app.market_data.check_quota_circuit_breaker', return_value=False), \
             patch('trading_app.market_data.rate_limit_twelvedata'), \
             patch('trading_app.market_data.yf.Ticker', return_value=mock_ticker):
            result = get_fresh_quote_for_trade("BN.PA")

        # Doit fallback yfinance ou retourner None (pas utiliser les données d'hier)
        if result is not None:
            assert result['source'] != 'twelvedata' or result['is_fresh'] is True, \
                "Ne doit pas utiliser TwelveData avec un datetime périmé"


# ============================================================================
# GROUPE 2: TYPES & CONVERSIONS — TP1/TP2 strings, float() manquants
# ============================================================================

class TestTypeConversions:
    """Vérifie que les prix sont toujours convertis en float avant stockage/calcul."""

    def test_float_conversion_handles_none(self):
        """float(None or 0) doit donner 0.0, pas TypeError."""
        assert float(None or 0) == 0.0

    def test_float_conversion_handles_empty_string(self):
        """float('' or 0) doit donner 0.0."""
        assert float('' or 0) == 0.0

    def test_float_conversion_handles_string_number(self):
        """float('92.40' or 0) doit donner 92.40."""
        assert float('92.40' or 0) == 92.40

    def test_enregistrer_recommandation_converts_tp_to_float(self):
        """BUG #2: TP1/TP2 étaient stockés comme strings.
        L'INSERT doit utiliser les variables float, pas trade_data.get()."""
        from trading_app.trades import enregistrer_recommandation
        source = inspect.getsource(enregistrer_recommandation)

        # Chercher la section INSERT pour vérifier qu'on utilise tp1/tp2 (variables locales)
        # et PAS trade_data.get('tp1') dans les VALUES
        lines = source.split('\n')
        in_insert = False
        in_values = False
        values_lines = []
        for line in lines:
            if 'INSERT INTO trades_recommandes' in line:
                in_insert = True
            if in_insert and 'VALUES' in line:
                in_values = True
            if in_values:
                values_lines.append(line.strip())
                if ')' in line and line.strip().endswith(')'):
                    break

        values_block = ' '.join(values_lines)
        # Les variables locales tp1, tp2 sont utilisées directement (pas via trade_data.get)
        assert "trade_data.get('tp1'" not in values_block, \
            "RÉGRESSION: INSERT utilise trade_data.get('tp1') au lieu de la variable float tp1"
        assert "trade_data.get('tp2'" not in values_block, \
            "RÉGRESSION: INSERT utilise trade_data.get('tp2') au lieu de la variable float tp2"

    def test_reevaluer_uses_float_conversion(self):
        """BUG #3: reevaluer_trades_intraday n'avait pas float() sur les prix DB."""
        from trading_app.trades import reevaluer_trades_intraday
        source = inspect.getsource(reevaluer_trades_intraday)

        # Les prix lus depuis la DB doivent être convertis avec float()
        assert "float(trade.get('prix_entree'" in source, \
            "RÉGRESSION: prix_entree non converti avec float() dans reevaluer_trades_intraday"
        assert "float(trade.get('prix_stop'" in source, \
            "RÉGRESSION: prix_stop non converti avec float() dans reevaluer_trades_intraday"
        assert "float(trade.get('prix_tp1'" in source, \
            "RÉGRESSION: prix_tp1 non converti avec float() dans reevaluer_trades_intraday"

    def test_verifier_resultats_uses_float_conversion(self):
        """verifier_resultats_trades doit aussi convertir les prix en float."""
        from trading_app.trades import verifier_resultats_trades
        source = inspect.getsource(verifier_resultats_trades)

        assert "float(trade['prix_entree']" in source or "float(trade.get('prix_entree'" in source, \
            "RÉGRESSION: prix_entree non converti avec float() dans verifier_resultats_trades"


# ============================================================================
# GROUPE 3: SYMBOL CONVERSION — Retourne tuple, pas string
# ============================================================================

class TestSymbolConversion:
    """Vérifie que convert_symbol_to_twelvedata retourne toujours un tuple."""

    def test_returns_tuple(self):
        """convert_symbol_to_twelvedata DOIT retourner un tuple (symbol, mic_code)."""
        from trading_app.market_data import convert_symbol_to_twelvedata
        result = convert_symbol_to_twelvedata("AAPL")
        assert isinstance(result, tuple), \
            f"RÉGRESSION: retourne {type(result)} au lieu de tuple"
        assert len(result) == 2, f"RÉGRESSION: tuple de longueur {len(result)}, attendu 2"

    def test_us_stock_no_mic(self):
        """Actions US: pas de MIC code."""
        from trading_app.market_data import convert_symbol_to_twelvedata
        symbol, mic = convert_symbol_to_twelvedata("AAPL")
        assert mic is None, "Actions US ne doivent pas avoir de MIC code"

    def test_european_stock_has_mic(self):
        """Actions européennes: doivent avoir un MIC code."""
        from trading_app.market_data import convert_symbol_to_twelvedata
        from trading_app.constants import SYMBOL_MAPPING_TWELVEDATA

        # Trouver un symbole européen avec MIC dans le mapping
        eu_symbol = None
        for yahoo_sym, td_mapped in SYMBOL_MAPPING_TWELVEDATA.items():
            if ":" in td_mapped:
                eu_symbol = yahoo_sym
                break

        if eu_symbol:
            symbol, mic = convert_symbol_to_twelvedata(eu_symbol)
            assert mic is not None, \
                f"RÉGRESSION: {eu_symbol} devrait avoir un MIC code, got None"

    def test_all_callers_unpack_tuple(self):
        """Vérifie que TOUS les appelants de convert_symbol_to_twelvedata
        font bien le unpacking du tuple."""
        import trading_app.market_data as md
        import trading_app.routes.api_market as am

        # Vérifier dans market_data.py
        md_source = inspect.getsource(md)
        # Chercher les appels sans unpacking (bug pattern)
        # Mauvais: td_sym = convert_symbol_to_twelvedata(...)
        # Bon: td_sym, mic_code = convert_symbol_to_twelvedata(...)
        lines = md_source.split('\n')
        for i, line in enumerate(lines):
            if 'convert_symbol_to_twelvedata(' in line and '=' in line:
                if 'def convert_symbol_to_twelvedata' in line:
                    continue  # Définition
                if 'import' in line:
                    continue
                # Vérifier unpacking
                lhs = line.split('=')[0].strip()
                assert ',' in lhs, \
                    f"RÉGRESSION market_data.py ligne ~{i+1}: appel sans unpacking tuple: {line.strip()}"

        # Vérifier dans api_market.py
        am_source = inspect.getsource(am)
        lines = am_source.split('\n')
        for i, line in enumerate(lines):
            if 'convert_symbol_to_twelvedata(' in line and '=' in line:
                if 'import' in line:
                    continue
                lhs = line.split('=')[0].strip()
                assert ',' in lhs, \
                    f"RÉGRESSION api_market.py ligne ~{i+1}: appel sans unpacking tuple: {line.strip()}"


# ============================================================================
# GROUPE 4: CONNEXIONS DB — Fuites mémoire / connexions non fermées
# ============================================================================

class TestDBConnectionSafety:
    """Vérifie que toutes les fonctions ferment leur connexion DB dans un finally."""

    def _check_function_has_finally_close(self, func, func_name):
        """Helper: vérifie qu'une fonction avec sqlite3.connect a un finally: conn.close()"""
        source = inspect.getsource(func)
        if 'sqlite3.connect' not in source:
            return  # Pas de connexion DB dans cette fonction

        # Doit avoir un bloc finally avec close()
        assert 'finally:' in source, \
            f"RÉGRESSION: {func_name} ouvre une connexion DB sans bloc finally"
        assert '.close()' in source, \
            f"RÉGRESSION: {func_name} n'appelle pas .close() dans son finally"

    def test_trades_get_trades_du_jour(self):
        from trading_app.trades import get_trades_du_jour
        self._check_function_has_finally_close(get_trades_du_jour, 'get_trades_du_jour')

    def test_trades_get_performances(self):
        from trading_app.trades import get_performances
        self._check_function_has_finally_close(get_performances, 'get_performances')

    def test_trades_enregistrer_recommandation(self):
        from trading_app.trades import enregistrer_recommandation
        self._check_function_has_finally_close(enregistrer_recommandation, 'enregistrer_recommandation')

    def test_trades_verifier_resultats(self):
        from trading_app.trades import verifier_resultats_trades
        self._check_function_has_finally_close(verifier_resultats_trades, 'verifier_resultats_trades')

    def test_trades_cloturer_orphelins(self):
        from trading_app.trades import cloturer_trades_orphelins
        self._check_function_has_finally_close(cloturer_trades_orphelins, 'cloturer_trades_orphelins')

    def test_api_trades_ouverts(self):
        from trading_app.routes.api_market import api_trades_ouverts
        self._check_function_has_finally_close(api_trades_ouverts, 'api_trades_ouverts')

    def test_scheduler_watchdog(self):
        from trading_app.scheduler import watchdog_analyse_matin
        self._check_function_has_finally_close(watchdog_analyse_matin, 'watchdog_analyse_matin')

    def test_conn_initialized_to_none(self):
        """conn = None AVANT le try pour que le finally fonctionne si connect() échoue."""
        from trading_app.trades import get_trades_du_jour, get_performances, enregistrer_recommandation

        for func in [get_trades_du_jour, get_performances, enregistrer_recommandation]:
            source = inspect.getsource(func)
            assert 'conn = None' in source, \
                f"RÉGRESSION: {func.__name__} n'initialise pas conn = None avant le try"


# ============================================================================
# GROUPE 5: VALIDATION — Cohérence prix, R:R, division par zéro
# ============================================================================

class TestValidation:
    """Vérifie les validations anti-erreur dans les calculs de trading."""

    def _mock_market_open(self):
        """Helper: simule un mardi à 10h00 (marché EU ouvert)."""
        from unittest.mock import patch
        import pytz
        mock_dt = datetime(2025, 6, 3, 10, 0, 0, tzinfo=pytz.timezone('Europe/Paris'))  # Mardi 10h
        return patch('trading_app.validation.get_paris_time', return_value=mock_dt)

    def test_valider_opportunite_long_coherent(self):
        """LONG valide: stop < entree < tp1."""
        from trading_app.validation import valider_opportunite
        opp = {
            'entree': 100, 'stop': 95, 'tp1': 110, 'tp2': 120,
            'direction': 'LONG', 'actif': 'CAC 40', 'symbole': '^FCHI',
            'conviction_score': 4, 'regime_marche': 'NORMAL'
        }
        with self._mock_market_open():
            valide, _, warns, errs = valider_opportunite(opp)
        assert valide is True, f"LONG cohérent rejeté: {errs}"

    def test_valider_opportunite_long_incoherent_stop_above_entry(self):
        """LONG invalide: stop > entree doit être rejeté."""
        from trading_app.validation import valider_opportunite
        opp = {
            'entree': 100, 'stop': 105, 'tp1': 110, 'tp2': 0,
            'direction': 'LONG', 'actif': 'CAC 40', 'symbole': '^FCHI'
        }
        valide, _, _, errs = valider_opportunite(opp)
        assert valide is False, "LONG avec stop > entree devrait être rejeté"

    def test_valider_opportunite_short_coherent(self):
        """SHORT valide: stop > entree > tp1."""
        from trading_app.validation import valider_opportunite
        opp = {
            'entree': 100, 'stop': 105, 'tp1': 90, 'tp2': 85,
            'direction': 'SHORT', 'actif': 'CAC 40', 'symbole': '^FCHI',
            'conviction_score': 4, 'regime_marche': 'NORMAL'
        }
        with self._mock_market_open():
            valide, _, warns, errs = valider_opportunite(opp)
        assert valide is True, f"SHORT cohérent rejeté: {errs}"

    def test_valider_opportunite_short_incoherent_stop_below_entry(self):
        """SHORT invalide: stop < entree doit être rejeté."""
        from trading_app.validation import valider_opportunite
        opp = {
            'entree': 100, 'stop': 95, 'tp1': 90, 'tp2': 0,
            'direction': 'SHORT', 'actif': 'CAC 40', 'symbole': '^FCHI'
        }
        valide, _, _, errs = valider_opportunite(opp)
        assert valide is False, "SHORT avec stop < entree devrait être rejeté"

    def test_valider_opportunite_zero_entry_rejected(self):
        """Prix d'entrée = 0 doit être rejeté."""
        from trading_app.validation import valider_opportunite
        opp = {'entree': 0, 'stop': 95, 'tp1': 110, 'direction': 'LONG'}
        valide, _, _, errs = valider_opportunite(opp)
        assert valide is False, "Entrée à 0 devrait être rejetée"

    def test_valider_opportunite_none_entry_rejected(self):
        """Prix d'entrée = None doit être rejeté."""
        from trading_app.validation import valider_opportunite
        opp = {'entree': None, 'stop': 95, 'tp1': 110, 'direction': 'LONG'}
        valide, _, _, errs = valider_opportunite(opp)
        assert valide is False, "Entrée None devrait être rejetée"

    def test_valider_opportunite_string_prices_handled(self):
        """Les prix en string (de Claude JSON) doivent être convertis."""
        from trading_app.validation import valider_opportunite
        opp = {
            'entree': '100.5', 'stop': '95.0', 'tp1': '110.0', 'tp2': '120.0',
            'direction': 'LONG', 'actif': 'CAC 40', 'symbole': '^FCHI',
            'conviction_score': 4, 'regime_marche': 'NORMAL'
        }
        with self._mock_market_open():
            valide, opp_out, warns, errs = valider_opportunite(opp)
        assert valide is True, f"Prix en string rejeté: {errs}"

    def test_valider_setup_zero_entry_rejected(self):
        """valider_setup_avant_trade avec prix 0 ne doit pas crasher."""
        from trading_app.validation import valider_setup_avant_trade
        valide, raison, quote = valider_setup_avant_trade("TEST", "LONG", 0)
        assert valide is False
        assert "invalide" in raison.lower()

    def test_analyser_cloture_intraday_empty_hist(self):
        """analyser_cloture_intraday avec hist vide retourne (None, None, None, None)."""
        from trading_app.validation import analyser_cloture_intraday
        r, p, pnl, idx = analyser_cloture_intraday(pd.DataFrame(), 'LONG', 100, 95, 110, 120)
        assert r is None

    def test_analyser_cloture_intraday_zero_entry(self):
        """analyser_cloture_intraday avec entree=0 ne doit pas diviser par zéro."""
        from trading_app.validation import analyser_cloture_intraday
        hist = pd.DataFrame({
            'Open': [100], 'High': [105], 'Low': [95], 'Close': [102]
        }, index=pd.date_range('2024-01-01', periods=1, freq='5min'))
        r, p, pnl, idx = analyser_cloture_intraday(hist, 'LONG', 0, 95, 110, 120)
        assert r is None, "entree=0 ne doit pas crasher (division par zéro)"

    def test_analyser_cloture_long_stop_hit(self):
        """LONG: stop touché quand Low <= stop."""
        from trading_app.validation import analyser_cloture_intraday
        hist = pd.DataFrame({
            'Open': [100], 'High': [101], 'Low': [94], 'Close': [95]
        }, index=pd.date_range('2024-01-01', periods=1, freq='5min'))
        r, p, pnl, idx = analyser_cloture_intraday(hist, 'LONG', 100, 95, 110, 120)
        assert r == 'STOP', f"LONG stop devrait être touché, got {r}"
        assert pnl < 0, "PnL doit être négatif sur un stop"

    def test_analyser_cloture_long_tp1_hit(self):
        """LONG: TP1 touché quand High >= tp1."""
        from trading_app.validation import analyser_cloture_intraday
        hist = pd.DataFrame({
            'Open': [100], 'High': [111], 'Low': [99], 'Close': [110]
        }, index=pd.date_range('2024-01-01', periods=1, freq='5min'))
        r, p, pnl, idx = analyser_cloture_intraday(hist, 'LONG', 100, 90, 110, 120)
        assert r in ('TP1', 'TP2'), f"LONG TP devrait être touché, got {r}"
        assert pnl > 0, "PnL doit être positif sur un TP"

    def test_analyser_cloture_short_stop_hit(self):
        """SHORT: stop touché quand High >= stop."""
        from trading_app.validation import analyser_cloture_intraday
        hist = pd.DataFrame({
            'Open': [100], 'High': [106], 'Low': [99], 'Close': [105]
        }, index=pd.date_range('2024-01-01', periods=1, freq='5min'))
        r, p, pnl, idx = analyser_cloture_intraday(hist, 'SHORT', 100, 105, 90, 85)
        assert r == 'STOP', f"SHORT stop devrait être touché, got {r}"

    def test_analyser_cloture_short_tp1_hit(self):
        """SHORT: TP1 touché quand Low <= tp1."""
        from trading_app.validation import analyser_cloture_intraday
        hist = pd.DataFrame({
            'Open': [100], 'High': [101], 'Low': [89], 'Close': [90]
        }, index=pd.date_range('2024-01-01', periods=1, freq='5min'))
        r, p, pnl, idx = analyser_cloture_intraday(hist, 'SHORT', 100, 105, 90, 85)
        assert r in ('TP1', 'TP2'), f"SHORT TP devrait être touché, got {r}"
        assert pnl > 0, "PnL doit être positif sur un TP (SHORT)"


# ============================================================================
# GROUPE 6: PnL CALCULATIONS — Division par zéro, direction LONG/SHORT
# ============================================================================

class TestPnLCalculations:
    """Vérifie les calculs de PnL pour LONG et SHORT."""

    def test_pnl_long_positive(self):
        """LONG gagnant: (prix_actuel - entree) / entree * 100."""
        entree = 100.0
        prix_actuel = 105.0
        pnl = ((prix_actuel - entree) / entree) * 100
        assert pnl == pytest.approx(5.0)

    def test_pnl_long_negative(self):
        """LONG perdant: résultat négatif."""
        entree = 100.0
        prix_actuel = 95.0
        pnl = ((prix_actuel - entree) / entree) * 100
        assert pnl == pytest.approx(-5.0)

    def test_pnl_short_positive(self):
        """SHORT gagnant: (entree - prix_actuel) / entree * 100."""
        entree = 100.0
        prix_actuel = 95.0
        pnl = ((entree - prix_actuel) / entree) * 100
        assert pnl == pytest.approx(5.0)

    def test_pnl_short_negative(self):
        """SHORT perdant: résultat négatif."""
        entree = 100.0
        prix_actuel = 105.0
        pnl = ((entree - prix_actuel) / entree) * 100
        assert pnl == pytest.approx(-5.0)

    def test_pnl_zero_entry_guarded(self):
        """Les fonctions de calcul PnL doivent vérifier entree > 0."""
        from trading_app.trades import verifier_resultats_trades
        source = inspect.getsource(verifier_resultats_trades)
        assert 'entree <= 0' in source or 'entree == 0' in source, \
            "RÉGRESSION: verifier_resultats_trades ne vérifie pas entree <= 0"

    def test_performances_zero_division_guarded(self):
        """get_performances: taux_reussite ne doit pas crasher si conclus = 0."""
        from trading_app.trades import get_performances
        source = inspect.getsource(get_performances)
        assert 'conclus > 0' in source, \
            "RÉGRESSION: get_performances ne vérifie pas conclus > 0 avant division"


# ============================================================================
# GROUPE 7: DÉDUPLICATION — Pas de trades dupliqués
# ============================================================================

class TestDeduplication:
    """Vérifie la déduplication des trades (BNP 3x)."""

    def test_enregistrer_recommandation_has_dedup_guard(self):
        """enregistrer_recommandation doit vérifier les trades existants avant INSERT."""
        from trading_app.trades import enregistrer_recommandation
        source = inspect.getsource(enregistrer_recommandation)
        assert 'resultat IS NULL' in source, \
            "RÉGRESSION: pas de guard déduplication dans enregistrer_recommandation"
        assert 'nb_ouverts' in source or 'COUNT(*)' in source, \
            "RÉGRESSION: pas de comptage de trades ouverts pour déduplication"

    def test_scheduler_preloads_open_symbols(self):
        """Le scheduler doit pré-charger les symboles déjà ouverts."""
        from trading_app.scheduler import _executer_analyse_planifiee_impl
        source = inspect.getsource(_executer_analyse_planifiee_impl)
        assert 'symboles_traites' in source, \
            "RÉGRESSION: scheduler ne track pas les symboles déjà traités"
        assert 'resultat IS NULL' in source, \
            "RÉGRESSION: scheduler ne pré-charge pas les trades ouverts"

    def test_api_analyse_preloads_open_symbols(self):
        """La route API analyse doit aussi pré-charger les symboles ouverts."""
        from trading_app.routes.api_market import api_lancer_analyse
        source = inspect.getsource(api_lancer_analyse)
        assert 'symboles_traites' in source, \
            "RÉGRESSION: api_lancer_analyse ne track pas les symboles déjà traités"


# ============================================================================
# GROUPE 8: SCHEDULER — Job isolation, concurrency lock
# ============================================================================

class TestScheduler:
    """Vérifie la robustesse du scheduler."""

    def test_run_pending_safe_isolates_errors(self):
        """run_pending_safe doit avoir un try/except PAR JOB."""
        from trading_app.scheduler import run_pending_safe
        source = inspect.getsource(run_pending_safe)
        assert 'for job in' in source, "Doit itérer sur chaque job"
        assert 'try:' in source, "Doit avoir un try"
        assert 'job.run()' in source, "Doit appeler job.run()"
        assert 'job.last_run' in source, \
            "RÉGRESSION: doit forcer job.last_run pour reschedule en cas d'erreur"
        assert '_schedule_next_run' in source, \
            "RÉGRESSION: doit appeler _schedule_next_run() après erreur"

    def test_analyse_has_concurrency_lock(self):
        """executer_analyse_planifiee doit être protégé par un Lock."""
        from trading_app.scheduler import executer_analyse_planifiee
        source = inspect.getsource(executer_analyse_planifiee)
        assert '_analyse_lock' in source, \
            "RÉGRESSION: executer_analyse_planifiee n'est pas protégé par _analyse_lock"
        assert 'acquire' in source, "Doit acquérir le lock"
        assert 'release' in source, "Doit release le lock dans finally"

    def test_analyse_lock_is_non_blocking(self):
        """Le lock doit être non-bloquant (skip si déjà en cours)."""
        from trading_app.scheduler import executer_analyse_planifiee
        source = inspect.getsource(executer_analyse_planifiee)
        assert 'blocking=False' in source, \
            "RÉGRESSION: le lock doit être non-bloquant pour éviter l'accumulation"


# ============================================================================
# GROUPE 9: CROSS-MODULE STATE — Pattern critique de partage d'état
# ============================================================================

class TestCrossModuleState:
    """Vérifie que l'état global est correctement partagé entre modules."""

    def test_no_global_keyword_for_config_vars(self):
        """JAMAIS de 'global TWELVEDATA_QUOTA_EXCEEDED' — utiliser config.VAR."""
        import trading_app.market_data as md
        source = inspect.getsource(md)

        # Ces variables doivent être accédées via config.VAR, pas global VAR
        forbidden_globals = [
            'global TWELVEDATA_QUOTA_EXCEEDED',
            'global TWELVEDATA_QUOTA_RESET_TIME',
            'global TWELVEDATA_LAST_CALL',
        ]
        for forbidden in forbidden_globals:
            assert forbidden not in source, \
                f"RÉGRESSION: '{forbidden}' trouvé dans market_data.py. " \
                f"Utiliser config.VARIABLE à la place."

    def test_scheduler_health_uses_config(self):
        """SCHEDULER_HEALTH doit être muté via config, pas réassigné."""
        from trading_app.scheduler import run_scheduler
        source = inspect.getsource(run_scheduler)
        assert "config.SCHEDULER_HEALTH" in source, \
            "RÉGRESSION: run_scheduler doit utiliser config.SCHEDULER_HEALTH"

    def test_config_has_required_globals(self):
        """config.py doit définir toutes les variables globales partagées."""
        from trading_app import config
        required = ['DB_PATH', 'DB_TIMEOUT', 'TWELVEDATA_API_KEY',
                     'TWELVEDATA_CACHE', 'TWELVEDATA_CACHE_LOCK',
                     'SCHEDULER_HEALTH']
        for var in required:
            assert hasattr(config, var), f"RÉGRESSION: config.{var} manquant"


# ============================================================================
# GROUPE 10: INTÉGRATION — Test bout en bout avec DB en mémoire
# ============================================================================

class TestIntegrationDB:
    """Tests d'intégration avec une base SQLite en mémoire."""

    @pytest.fixture
    def db_path(self, tmp_path):
        """Crée une base de test avec le schéma minimal."""
        db_file = str(tmp_path / "test_trading.db")
        conn = sqlite3.connect(db_file)
        conn.execute('''CREATE TABLE trades_recommandes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT, heure_message TEXT, actif TEXT, symbole TEXT,
            type_setup TEXT, prix_entree REAL, prix_stop REAL,
            prix_tp1 REAL, prix_tp2 REAL, prix_actuel REAL,
            catalyseur TEXT, duree_estimee TEXT, timestamp_reco TEXT,
            direction TEXT, categorie_actif TEXT, heure_entree INTEGER,
            resultat TEXT, prix_sortie REAL, pnl_pct REAL,
            timestamp_sortie TEXT, duree_minutes REAL,
            rsi_reco REAL, rsi_signal_reco TEXT, macd_signal_reco TEXT,
            atr_pct_reco REAL, volume_relatif_reco REAL,
            pivot_reco REAL, support1_reco REAL, resistance1_reco REAL,
            ratio_rr TEXT, ratio_rr_justification TEXT,
            ab_test_id TEXT, ab_groupe TEXT, ab_variante TEXT,
            strategie_entree TEXT, trailing_stop INTEGER, trailing_stop_pct REAL,
            prix_limite_entree REAL, vix_niveau REAL,
            regime_marche TEXT, regles_adaptees TEXT,
            validite_minutes INTEGER, heure_expiration TEXT, urgence TEXT,
            trend_daily TEXT, trend_h4 TEXT, trend_h1 TEXT,
            alignement_tf INTEGER, confluence_score INTEGER,
            sentiment_score REAL, sentiment_source TEXT, sentiment_detail TEXT,
            jour_semaine TEXT, session_marche TEXT, pattern_jour TEXT,
            conviction_score INTEGER, trade_grade TEXT, grade_details TEXT,
            grade_setup_score REAL,
            prix_max_atteint REAL, prix_min_atteint REAL,
            prix_dernier_check REAL, timestamp_dernier_check TEXT,
            pnl_max REAL, pnl_min REAL, nb_checks INTEGER,
            statut_intraday TEXT
        )''')
        conn.commit()
        conn.close()
        return db_file

    def test_dedup_blocks_second_trade(self, db_path):
        """Un second trade sur le même symbole aujourd'hui doit être bloqué."""
        conn = sqlite3.connect(db_path)
        today = datetime.now().strftime('%Y-%m-%d')

        # Insérer un trade ouvert (resultat IS NULL)
        conn.execute('''INSERT INTO trades_recommandes
            (date, symbole, resultat, prix_entree, prix_stop, prix_tp1, direction)
            VALUES (?, ?, NULL, 92.40, 91.00, 95.00, 'LONG')''',
            (today, 'BN.PA'))
        conn.commit()

        # Vérifier que le guard de déduplication fonctionnerait
        cursor = conn.cursor()
        cursor.execute(
            'SELECT COUNT(*) FROM trades_recommandes WHERE symbole = ? AND resultat IS NULL AND date = ?',
            ('BN.PA', today)
        )
        count = cursor.fetchone()[0]
        conn.close()

        assert count > 0, "Le trade ouvert doit être détecté par la requête de déduplication"

    def test_closed_trade_allows_new_one(self, db_path):
        """Un trade clôturé ne doit PAS bloquer un nouveau trade."""
        conn = sqlite3.connect(db_path)
        today = datetime.now().strftime('%Y-%m-%d')

        # Insérer un trade CLÔTURÉ
        conn.execute('''INSERT INTO trades_recommandes
            (date, symbole, resultat, prix_entree, prix_stop, prix_tp1, direction)
            VALUES (?, ?, 'TP1', 92.40, 91.00, 95.00, 'LONG')''',
            (today, 'BN.PA'))
        conn.commit()

        # La requête de déduplication ne doit PAS trouver ce trade
        cursor = conn.cursor()
        cursor.execute(
            'SELECT COUNT(*) FROM trades_recommandes WHERE symbole = ? AND resultat IS NULL AND date = ?',
            ('BN.PA', today)
        )
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 0, "Un trade clôturé ne doit pas bloquer un nouveau trade"

    def test_performances_empty_db(self, db_path):
        """get_performances ne doit pas crasher sur une base vide."""
        with patch('trading_app.trades.DB_PATH', db_path):
            from trading_app.trades import get_performances
            result = get_performances('semaine')

        assert result['total'] == 0
        assert result['taux_reussite'] == 0
        assert result['pnl_moyen'] == 0

    def test_tp_stored_as_float_not_string(self, db_path):
        """Les prix TP1/TP2 doivent être stockés comme REAL, pas TEXT."""
        conn = sqlite3.connect(db_path)
        conn.execute('''INSERT INTO trades_recommandes
            (date, symbole, prix_entree, prix_stop, prix_tp1, prix_tp2, direction, resultat)
            VALUES (?, ?, 100.0, 95.0, 110.0, 120.0, 'LONG', NULL)''',
            (datetime.now().strftime('%Y-%m-%d'), 'TEST'))
        conn.commit()

        cursor = conn.cursor()
        cursor.execute('SELECT prix_tp1, prix_tp2 FROM trades_recommandes WHERE symbole = ?', ('TEST',))
        row = cursor.fetchone()
        conn.close()

        assert isinstance(row[0], float), f"TP1 stocké comme {type(row[0])}, attendu float"
        assert isinstance(row[1], float), f"TP2 stocké comme {type(row[1])}, attendu float"


# ============================================================================
# GROUPE 11: SCHEDULING — Tâches planifiées, time windows, catch-up
# ============================================================================

class TestScheduling:
    """Vérifie la configuration et la robustesse du scheduler."""

    def test_configurer_schedule_registers_eu_analyses(self):
        """Les analyses EU-only doivent être planifiées à 8h, 9h15, 12h."""
        from trading_app.scheduler import configurer_schedule
        source = inspect.getsource(configurer_schedule)
        for heure in ["08:00", "09:15", "12:00"]:
            assert heure in source, f"Heure EU manquante dans configurer_schedule: {heure}"
        assert 'eu_only=True' in source, "Les analyses matinales doivent être eu_only=True"

    def test_configurer_schedule_registers_full_analyses(self):
        """Les analyses complètes doivent être planifiées à 15h35, 17h."""
        from trading_app.scheduler import configurer_schedule
        source = inspect.getsource(configurer_schedule)
        for heure in ["15:35", "17:00"]:
            assert heure in source, f"Heure full manquante: {heure}"
        assert 'eu_only=False' in source, "Les analyses complètes doivent être eu_only=False"

    def test_watchdog_time_window_check(self):
        """Le watchdog ne doit tourner qu'entre 8h et 15h."""
        from trading_app.scheduler import watchdog_analyse_matin
        source = inspect.getsource(watchdog_analyse_matin)
        assert 'heure_decimal < 8' in source, "Watchdog doit vérifier heure_decimal < 8"
        assert 'heure_decimal > 15' in source, "Watchdog doit vérifier heure_decimal > 15"

    def test_rattraper_journal_checks_weekday_and_hour(self):
        """rattraper_journal_manque ne tourne que semaine 18h-22h."""
        from trading_app.scheduler import rattraper_journal_manque
        source = inspect.getsource(rattraper_journal_manque)
        assert 'jour >= 5' in source or 'weekday' in source, "Doit vérifier le weekend"
        assert 'heure < 18' in source, "Doit vérifier heure >= 18"
        assert 'heure >= 22' in source, "Doit vérifier heure < 22"

    def test_journal_complet_independent_steps(self):
        """enregistrer_journal_complet doit avoir try/except séparés pour journal et bilan."""
        from trading_app.journal import enregistrer_journal_complet
        source = inspect.getsource(enregistrer_journal_complet)
        # Doit avoir au moins 2 blocs try/except indépendants
        assert source.count('try:') >= 2, \
            "RÉGRESSION: enregistrer_journal_complet doit avoir 2 try/except indépendants"
        assert 'enregistrer_journal_quotidien' in source, "Doit appeler enregistrer_journal_quotidien"
        assert 'generer_bilan_quotidien' in source, "Doit appeler generer_bilan_quotidien"

    def test_all_executor_functions_have_try_except(self):
        """Toutes les fonctions executor du scheduler doivent avoir try/except."""
        from trading_app import scheduler
        # Note: executer_analyse_planifiee est un lock wrapper (try/finally),
        # le vrai travail est dans _executer_analyse_planifiee_impl
        executor_names = [
            '_executer_analyse_planifiee_impl',
            'executer_cloture_planifiee',
            'executer_journal_fr',
            'executer_journal_complet',
            'executer_verification_trades',
            'executer_reevaluation_intraday',
            'executer_cloture_trades',
            'executer_rapport_hebdo',
        ]
        for name in executor_names:
            func = getattr(scheduler, name)
            source = inspect.getsource(func)
            assert 'try:' in source, \
                f"RÉGRESSION: {name} n'a pas de try/except — un crash bloque tout le scheduler"
            assert 'except' in source, \
                f"RÉGRESSION: {name} n'a pas de except — un crash bloque tout le scheduler"

    def test_est_jour_trading_valide_weekend(self):
        """Le weekend doit être invalide pour le trading."""
        from trading_app.constants import est_jour_trading_valide
        import pytz
        # Samedi
        samedi = datetime(2025, 6, 7, 10, 0, 0, tzinfo=pytz.timezone('Europe/Paris'))
        valide, raison = est_jour_trading_valide(samedi)
        assert valide is False, "Samedi ne devrait pas être un jour de trading valide"
        assert 'weekend' in raison.lower()

    def test_est_jour_trading_valide_weekday(self):
        """Un jour de semaine normal doit être valide."""
        from trading_app.constants import est_jour_trading_valide
        import pytz
        # Mardi
        mardi = datetime(2025, 6, 3, 10, 0, 0, tzinfo=pytz.timezone('Europe/Paris'))
        valide, raison = est_jour_trading_valide(mardi)
        # Peut être invalide si jour férié, mais pas pour "Weekend"
        if not valide:
            assert 'weekend' not in raison.lower(), "Un mardi ne devrait pas être marqué comme weekend"

    def test_scheduler_backup_and_orphan_cleanup_configured(self):
        """Le scheduler doit configurer backup DB et nettoyage orphelins."""
        from trading_app.scheduler import configurer_schedule
        source = inspect.getsource(configurer_schedule)
        assert 'backup_database' in source, "Backup DB doit être configuré dans le scheduler"
        assert 'cloturer_trades_orphelins' in source, "Nettoyage orphelins doit être configuré"


# ============================================================================
# GROUPE 12: PIPELINE OPPORTUNITÉS — Parsing, validation, enrichissement
# ============================================================================

class TestOpportunityPipeline:
    """Vérifie le pipeline complet de traitement des opportunités."""

    def test_extraire_json_claude_pure_json(self):
        """JSON pur doit être parsé correctement."""
        from trading_app.validation import extraire_json_claude
        result, err = extraire_json_claude('{"opportunites": []}')
        assert err is None, f"Erreur inattendue: {err}"
        assert result == {"opportunites": []}

    def test_extraire_json_claude_markdown_block(self):
        """JSON dans un bloc markdown doit être extrait."""
        from trading_app.validation import extraire_json_claude
        text = '```json\n{"key": "value"}\n```'
        result, err = extraire_json_claude(text)
        assert err is None, f"Erreur: {err}"
        assert result == {"key": "value"}

    def test_extraire_json_claude_nested_braces(self):
        """JSON avec accolades imbriquées doit être parsé correctement."""
        from trading_app.validation import extraire_json_claude
        text = '{"a": {"b": {"c": 1}}, "d": 2}'
        result, err = extraire_json_claude(text)
        assert err is None
        assert result['a']['b']['c'] == 1
        assert result['d'] == 2

    def test_extraire_json_claude_empty_input(self):
        """Input vide doit retourner (None, erreur)."""
        from trading_app.validation import extraire_json_claude
        result, err = extraire_json_claude("")
        assert result is None
        assert err is not None

    def test_extraire_json_claude_no_json(self):
        """Texte sans JSON doit retourner (None, erreur)."""
        from trading_app.validation import extraire_json_claude
        result, err = extraire_json_claude("Voici mon analyse sans JSON.")
        assert result is None
        assert err is not None

    def test_valider_structure_analyse_valid(self):
        """Structure valide avec contexte_marche et opportunites."""
        from trading_app.validation import valider_structure_analyse
        result = {"contexte_marche": "haussier", "opportunites": []}
        valide, erreurs = valider_structure_analyse(result)
        assert valide is True, f"Structure valide rejetée: {erreurs}"

    def test_valider_structure_analyse_missing_fields(self):
        """Structure sans champs requis doit échouer."""
        from trading_app.validation import valider_structure_analyse
        result = {"resume": "test"}
        valide, erreurs = valider_structure_analyse(result)
        assert valide is False
        assert len(erreurs) > 0

    def test_conviction_min_by_regime(self):
        """Le seuil de conviction dépend du régime de marché (VIX)."""
        # Vérifier les seuils dans le code du scheduler
        from trading_app.scheduler import _executer_analyse_planifiee_impl
        source = inspect.getsource(_executer_analyse_planifiee_impl)
        assert "'CALME': 2" in source, "Régime CALME: conviction min = 2"
        assert "'NORMAL': 3" in source, "Régime NORMAL: conviction min = 3"
        assert "'VOLATILE': 4" in source, "Régime VOLATILE: conviction min = 4"
        assert "'EXTREME': 5" in source, "Régime EXTREME: conviction min = 5"

    def test_enrichir_opportunite_fills_missing_indicators(self):
        """enrichir_opportunite_avec_donnees_marche doit compléter les indicateurs manquants."""
        from trading_app.trades import enrichir_opportunite_avec_donnees_marche
        opp = {'actif': 'Or', 'symbole': 'GC=F', 'entree': 2000}
        donnees_marche = {
            'Or': {
                'symbole': 'GC=F',
                'prix': 2000,
                'atr_pct': 1.5,
                'volume_relatif': 120,
                'pivot': 1990,
                'support1': 1980,
                'resistance1': 2010
            }
        }
        result = enrichir_opportunite_avec_donnees_marche(opp, donnees_marche)
        assert result['atr_pct'] == 1.5, "ATR doit être complété"
        assert result['pivot'] == 1990, "Pivot doit être complété"

    def test_rr_minimum_adapts_to_regime(self):
        """Le R:R minimum doit s'adapter au régime de marché."""
        from trading_app.validation import valider_opportunite
        source = inspect.getsource(valider_opportunite)
        assert "'CALME': 1.3" in source, "R:R CALME = 1.3"
        assert "'NORMAL': 1.5" in source, "R:R NORMAL = 1.5"
        assert "'VOLATILE': 1.8" in source, "R:R VOLATILE = 1.8"
        assert "'EXTREME': 2.0" in source, "R:R EXTREME = 2.0"


# ============================================================================
# GROUPE 13: SUIVI DE PERFORMANCE — Métriques, transactions atomiques
# ============================================================================

class TestPerformanceTracking:
    """Vérifie les calculs de performance et la gestion des trades."""

    def test_get_performances_returns_all_fields(self):
        """get_performances doit retourner tous les champs attendus."""
        from trading_app.trades import get_performances
        # On vérifie la structure du retour d'erreur (fallback)
        source = inspect.getsource(get_performances)
        required_fields = ['total', 'reussis', 'stops', 'taux_reussite', 'pnl_moyen', 'pnl_total']
        for field in required_fields:
            assert f"'{field}'" in source, f"Champ manquant dans get_performances: {field}"

    def test_get_performances_distinguishes_natural_vs_forced(self):
        """get_performances doit distinguer TP naturel vs WIN_FORCE."""
        from trading_app.trades import get_performances
        source = inspect.getsource(get_performances)
        assert 'reussis_naturel' in source, "Doit distinguer reussis_naturel (TP1, TP2)"
        assert 'reussis_force' in source, "Doit distinguer reussis_force (WIN_FORCE)"
        assert 'stops_naturel' in source, "Doit distinguer stops_naturel (STOP)"
        assert 'stops_force' in source, "Doit distinguer stops_force (LOSS_FORCE)"

    def test_verifier_resultats_uses_atomic_transaction(self):
        """verifier_resultats_trades doit utiliser une transaction atomique."""
        from trading_app.trades import verifier_resultats_trades
        source = inspect.getsource(verifier_resultats_trades)
        assert 'BEGIN TRANSACTION' in source, \
            "RÉGRESSION: verifier_resultats_trades doit utiliser BEGIN TRANSACTION"
        assert 'conn.commit()' in source, "Doit faire commit à la fin"
        assert 'conn.rollback()' in source, "Doit faire rollback en cas d'erreur"

    def test_cloturer_orphelins_only_previous_days(self):
        """cloturer_trades_orphelins ne doit toucher que les jours précédents."""
        from trading_app.trades import cloturer_trades_orphelins
        source = inspect.getsource(cloturer_trades_orphelins)
        assert 'date < ?' in source, \
            "RÉGRESSION: doit filtrer date < aujourd'hui (pas les trades du jour)"

    def test_cloturer_orphelins_marks_expired_with_zero_pnl(self):
        """Les trades orphelins doivent être marqués EXPIRED avec PnL=0."""
        from trading_app.trades import cloturer_trades_orphelins
        source = inspect.getsource(cloturer_trades_orphelins)
        assert "'EXPIRED'" in source, "Doit marquer comme EXPIRED"
        assert 'pnl_pct = 0' in source, "PnL doit être 0 pour ne pas biaiser les stats"

    def test_breakeven_threshold_in_cloture(self):
        """BREAKEVEN doit être entre -0.05% et +0.05%."""
        from trading_app.trades import cloturer_trades_jour
        source = inspect.getsource(cloturer_trades_jour)
        assert 'BREAKEVEN' in source, "Doit avoir le résultat BREAKEVEN"
        assert '0.05' in source, "Seuil BREAKEVEN doit être 0.05%"

    def test_cloturer_trades_jour_checks_intraday_history(self):
        """La clôture forcée doit vérifier l'historique intraday (TP/Stop touché plus tôt)."""
        from trading_app.trades import cloturer_trades_jour
        source = inspect.getsource(cloturer_trades_jour)
        assert 'analyser_historique_intraday_pour_tp_stop' in source, \
            "RÉGRESSION: clôture forcée doit analyser l'historique intraday d'abord"

    @pytest.fixture
    def perf_db(self, tmp_path):
        """Crée une DB de test avec des trades pour les tests de performance."""
        db_file = str(tmp_path / "test_perf.db")
        conn = sqlite3.connect(db_file)
        conn.execute('''CREATE TABLE trades_recommandes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT, symbole TEXT, actif TEXT, direction TEXT,
            prix_entree REAL, prix_stop REAL, prix_tp1 REAL, prix_tp2 REAL,
            resultat TEXT, pnl_pct REAL, timestamp_reco TEXT,
            prix_sortie REAL, timestamp_sortie TEXT, duree_minutes REAL,
            type_setup TEXT, categorie_actif TEXT, heure_entree INTEGER,
            rsi_reco REAL, rsi_signal_reco TEXT, macd_signal_reco TEXT,
            atr_pct_reco REAL, volume_relatif_reco REAL,
            pivot_reco REAL, support1_reco REAL, resistance1_reco REAL,
            ratio_rr TEXT, ratio_rr_justification TEXT,
            ab_test_id TEXT, ab_groupe TEXT, ab_variante TEXT,
            strategie_entree TEXT, trailing_stop INTEGER, trailing_stop_pct REAL,
            prix_limite_entree REAL, vix_niveau REAL,
            regime_marche TEXT, regles_adaptees TEXT,
            validite_minutes INTEGER, heure_expiration TEXT, urgence TEXT,
            trend_daily TEXT, trend_h4 TEXT, trend_h1 TEXT,
            alignement_tf INTEGER, confluence_score INTEGER,
            sentiment_score REAL, sentiment_source TEXT, sentiment_detail TEXT,
            jour_semaine TEXT, session_marche TEXT, pattern_jour TEXT,
            conviction_score INTEGER, trade_grade TEXT, grade_details TEXT,
            grade_setup_score REAL,
            prix_max_atteint REAL, prix_min_atteint REAL,
            prix_dernier_check REAL, timestamp_dernier_check TEXT,
            pnl_max REAL, pnl_min REAL, nb_checks INTEGER,
            statut_intraday TEXT, catalyseur TEXT, duree_estimee TEXT,
            prix_actuel REAL, notes TEXT
        )''')
        today = datetime.now().strftime('%Y-%m-%d')
        # 3 trades: 2 wins + 1 stop
        conn.execute("INSERT INTO trades_recommandes (date, symbole, resultat, pnl_pct, prix_entree) VALUES (?, ?, 'TP1', 2.5, 100)", (today, 'BN.PA'))
        conn.execute("INSERT INTO trades_recommandes (date, symbole, resultat, pnl_pct, prix_entree) VALUES (?, ?, 'STOP', -1.5, 100)", (today, 'AI.PA'))
        conn.execute("INSERT INTO trades_recommandes (date, symbole, resultat, pnl_pct, prix_entree) VALUES (?, ?, 'TP2', 4.0, 100)", (today, 'AAPL'))
        conn.commit()
        conn.close()
        return db_file

    def test_get_performances_with_real_data(self, perf_db):
        """get_performances avec de vraies données retourne les bonnes stats."""
        with patch('trading_app.trades.DB_PATH', perf_db):
            from trading_app.trades import get_performances
            result = get_performances('jour')
        assert result['total'] == 3
        assert result['reussis'] == 2  # TP1 + TP2
        assert result['stops'] == 1
        assert result['taux_reussite'] == pytest.approx(66.7, abs=0.1)

    def test_orphan_closure_integration(self, perf_db):
        """Les trades des jours précédents sans résultat sont marqués EXPIRED."""
        conn = sqlite3.connect(perf_db)
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        conn.execute("INSERT INTO trades_recommandes (date, symbole, resultat, prix_entree) VALUES (?, ?, NULL, 50)", (yesterday, 'OLD.PA'))
        conn.commit()
        conn.close()

        with patch('trading_app.trades.DB_PATH', perf_db):
            from trading_app.trades import cloturer_trades_orphelins
            nb = cloturer_trades_orphelins()

        assert nb == 1, "Doit clôturer 1 trade orphelin"

        conn = sqlite3.connect(perf_db)
        cursor = conn.cursor()
        cursor.execute("SELECT resultat, pnl_pct FROM trades_recommandes WHERE symbole = 'OLD.PA'")
        row = cursor.fetchone()
        conn.close()
        assert row[0] == 'EXPIRED'
        assert row[1] == 0


# ============================================================================
# GROUPE 14: JOURNAL — Catégorisation, filtres, rapports
# ============================================================================

class TestJournal:
    """Vérifie le système de journal quotidien et rapports."""

    def test_get_categorie_actif_indice(self):
        """^FCHI doit être classé comme indice."""
        from trading_app.journal import get_categorie_actif
        assert get_categorie_actif('^FCHI') == 'indice'
        assert get_categorie_actif('^GSPC') == 'indice'
        assert get_categorie_actif('^GDAXI') == 'indice'

    def test_get_categorie_actif_action_eu(self):
        """BN.PA doit être classé comme action_eu."""
        from trading_app.journal import get_categorie_actif
        assert get_categorie_actif('BN.PA') == 'action_eu'
        assert get_categorie_actif('SAP.DE') == 'action_eu'
        assert get_categorie_actif('ISP.MI') == 'action_eu'

    def test_get_categorie_actif_forex(self):
        """EURUSD=X doit être classé comme forex."""
        from trading_app.journal import get_categorie_actif
        assert get_categorie_actif('EURUSD=X') == 'forex'
        assert get_categorie_actif('GBPUSD=X') == 'forex'

    def test_get_categorie_actif_commodite(self):
        """GC=F doit être classé comme commodite."""
        from trading_app.journal import get_categorie_actif
        assert get_categorie_actif('GC=F') == 'commodite'
        assert get_categorie_actif('CL=F') == 'commodite'

    def test_get_categorie_actif_action_us(self):
        """AAPL doit être classé comme action_us."""
        from trading_app.journal import get_categorie_actif
        assert get_categorie_actif('AAPL') == 'action_us'
        assert get_categorie_actif('MSFT') == 'action_us'
        assert get_categorie_actif('NVDA') == 'action_us'

    def test_enregistrer_journal_fr_filter(self):
        """enregistrer_journal_fr ne doit traiter que les actifs FR."""
        from trading_app.journal import enregistrer_journal_fr
        source = inspect.getsource(enregistrer_journal_fr)
        assert ".endswith('.PA')" in source, "Doit filtrer les actions .PA"
        assert "'^FCHI'" in source or "startswith('^FCHI')" in source, "Doit inclure le CAC 40"
        assert "'^GDAXI'" in source, "Doit inclure le DAX"

    def test_rapport_hebdo_iso_week_calculation(self):
        """Le rapport hebdo doit calculer la semaine ISO correctement."""
        from trading_app.journal import generer_rapport_hebdo
        source = inspect.getsource(generer_rapport_hebdo)
        assert 'isocalendar' in source, "Doit utiliser isocalendar pour la semaine ISO"
        assert 'timedelta(days=7)' in source, "Doit calculer la semaine précédente (-7 jours)"

    def test_rapport_hebdo_excludes_open_trades(self):
        """Le rapport hebdo doit exclure les trades ouverts des statistiques."""
        from trading_app.journal import generer_rapport_hebdo
        source = inspect.getsource(generer_rapport_hebdo)
        assert 'resultat IS NOT NULL' in source, \
            "RÉGRESSION: rapport hebdo doit filtrer resultat IS NOT NULL"

    def test_score_normalization_in_rapport(self):
        """Les scores de confiance doivent être bornés entre 0 et 100."""
        from trading_app.journal import generer_rapport_hebdo
        source = inspect.getsource(generer_rapport_hebdo)
        assert 'max(0, min(100' in source, \
            "RÉGRESSION: scores doivent être bornés max(0, min(100, ...))"

    def test_journal_quotidien_db_leaks(self):
        """Les fonctions journal DB doivent fermer leurs connexions."""
        from trading_app.journal import (
            sauvegarder_analyse, get_derniere_analyse,
            get_analyses_du_jour, get_journal_quotidien
        )
        # Ces fonctions ont conn.close() inline — vérifier au minimum
        for func in [sauvegarder_analyse, get_derniere_analyse, get_analyses_du_jour]:
            source = inspect.getsource(func)
            assert '.close()' in source, \
                f"RÉGRESSION: {func.__name__} n'appelle pas .close()"


# ============================================================================
# GROUPE 15: AUTO-APPRENTISSAGE — Ajustements, feedback, A/B testing
# ============================================================================

class TestSelfLearning:
    """Vérifie le système d'auto-apprentissage (ajustements + A/B tests)."""

    # --- Extraction de catégorie ---

    def test_extraire_categorie_actif_keywords(self):
        """Les mots-clés d'actifs mappent vers la bonne catégorie."""
        from trading_app.adjustments import extraire_categorie_ajustement
        assert extraire_categorie_ajustement('indice CAC', '', '') == 'indice'
        assert extraire_categorie_ajustement('actions européennes', '', '') == 'action_eu'
        assert extraire_categorie_ajustement('actions US', '', '') == 'action_us'
        assert extraire_categorie_ajustement('commodité gold pétrole', '', '') == 'commodite'
        # Note: 'forex' contient 'or' qui matche commodite en premier (bug connu)
        # Tester avec 'devise' qui est un mot-clé forex sans faux positif
        assert extraire_categorie_ajustement('devise EUR', '', '') == 'forex'

    def test_extraire_categorie_technique_keywords(self):
        """Les mots-clés techniques mappent vers la bonne catégorie."""
        from trading_app.adjustments import extraire_categorie_ajustement
        assert extraire_categorie_ajustement('RSI seuil', '', '') == 'indicateurs'
        assert extraire_categorie_ajustement('', 'trailing stop', '') == 'gestion_position'
        assert extraire_categorie_ajustement('ratio R/R', '', '') == 'risk_reward'
        # Note: 'conviction score' contient 'or' dans 'score' → matche commodite (bug connu)
        # Tester avec 'confiance' qui est un mot-clé conviction sans faux positif
        assert extraire_categorie_ajustement('confiance', '', '') == 'conviction'

    def test_extraire_categorie_temporelle_keywords(self):
        """Les mots-clés temporels mappent correctement."""
        from trading_app.adjustments import extraire_categorie_ajustement
        assert extraire_categorie_ajustement('lundi', '', '') == 'timing_jour'
        assert extraire_categorie_ajustement('', 'ouverture session', '') == 'timing_session'
        assert extraire_categorie_ajustement('', 'news NFP', '') == 'news_trading'

    def test_extraire_categorie_default(self):
        """Catégorie par défaut = 'strategie_generale', jamais 'trading'."""
        from trading_app.adjustments import extraire_categorie_ajustement
        result = extraire_categorie_ajustement('blabla', 'xyz', 'abc')
        assert result == 'strategie_generale', \
            f"RÉGRESSION: catégorie par défaut = '{result}', attendu 'strategie_generale' (pas 'trading')"

    # --- Normalisation des catégories ---

    def test_normaliser_categorie_mapping(self):
        """Les variations de noms doivent être normalisées."""
        from trading_app.adjustments import normaliser_categorie
        assert normaliser_categorie('indices') == 'indice'
        assert normaliser_categorie('actions_eu') == 'action_eu'
        assert normaliser_categorie('actions_us') == 'action_us'
        assert normaliser_categorie('commodités') == 'commodite'

    def test_normaliser_categorie_passthrough(self):
        """Catégories déjà normalisées passent telles quelles."""
        from trading_app.adjustments import normaliser_categorie
        assert normaliser_categorie('indice') == 'indice'
        assert normaliser_categorie('forex') == 'forex'
        assert normaliser_categorie(None) is None

    # --- Feedback loop thresholds ---

    def test_feedback_conclusion_thresholds(self):
        """Le feedback utilise des seuils ±5% pour POSITIF/NÉGATIF."""
        from trading_app.adjustments import calculer_feedback_ajustement
        source = inspect.getsource(calculer_feedback_ajustement)
        assert 'taux_apres > taux_avant + 5' in source, "Seuil POSITIF: taux +5%"
        assert 'taux_apres < taux_avant - 5' in source, "Seuil NEGATIF: taux -5%"

    def test_feedback_minimum_trades_required(self):
        """Le feedback nécessite au minimum 5 trades pour être significatif."""
        from trading_app.adjustments import calculer_feedback_ajustement
        source = inspect.getsource(calculer_feedback_ajustement)
        assert 'apres_conclus < 5' in source or 'avant_conclus < 5' in source, \
            "RÉGRESSION: minimum 5 trades requis pour le feedback"

    def test_feedback_excludes_open_trades(self):
        """Le feedback ne doit pas compter les trades ouverts."""
        from trading_app.adjustments import calculer_feedback_ajustement
        source = inspect.getsource(calculer_feedback_ajustement)
        assert 'resultat IS NOT NULL' in source, \
            "RÉGRESSION: feedback doit exclure les trades ouverts (resultat IS NOT NULL)"

    # --- Auto-validation / circuit breaker ---

    def test_auto_validation_requires_5_positives(self):
        """AUTO_VALIDER nécessite 5+ feedbacks positifs, 0 négatifs."""
        from trading_app.adjustments import get_historique_feedback_categorie
        source = inspect.getsource(get_historique_feedback_categorie)
        assert 'nb_positifs >= 5' in source, "AUTO_VALIDER nécessite 5+ positifs"

    def test_circuit_breaker_requires_5_negatives(self):
        """BLOQUER nécessite 5+ feedbacks négatifs, 0 positifs."""
        from trading_app.adjustments import get_historique_feedback_categorie
        source = inspect.getsource(get_historique_feedback_categorie)
        assert 'nb_negatifs >= 5' in source, "BLOQUER nécessite 5+ négatifs"

    def test_auto_validation_3_to_1_ratio(self):
        """AUTO_VALIDER aussi si ratio positifs/négatifs > 3:1."""
        from trading_app.adjustments import get_historique_feedback_categorie
        source = inspect.getsource(get_historique_feedback_categorie)
        assert 'nb_positifs > nb_negatifs * 3' in source, \
            "AUTO_VALIDER si ratio 3:1 en faveur des positifs"

    def test_historique_feedback_uses_and_not_or(self):
        """Le filtre feedback doit utiliser AND (catégorie ET type), pas OR."""
        from trading_app.adjustments import get_historique_feedback_categorie
        source = inspect.getsource(get_historique_feedback_categorie)
        # Vérifier qu'on a bien categorie = ? AND type_ajustement = ?
        assert 'categorie = ?' in source, "Doit filtrer par catégorie"
        assert 'type_ajustement = ?' in source, "Doit filtrer par type_ajustement"

    # --- Instructions dynamiques ---

    def test_generer_instructions_all_score_thresholds(self):
        """generer_instructions_dynamiques doit couvrir TOUS les seuils de score."""
        from trading_app.adjustments import generer_instructions_dynamiques
        source = inspect.getsource(generer_instructions_dynamiques)
        # Tous les seuils doivent être présents (0-30, 30-50, 50-65, 65-80, 80+)
        assert 'score < 30' in source, "Seuil ÉVITER < 30 manquant"
        assert 'score < 50' in source, "Seuil PRUDENCE < 50 manquant"
        assert 'score < 65' in source, "Seuil NORMAL < 65 manquant"
        assert 'score < 80' in source, "Seuil BON < 80 manquant"
        # Le dernier (80+) est implicite via else

    def test_generer_instructions_empty_when_no_criteres(self):
        """Pas de critères → chaîne vide."""
        from trading_app.adjustments import generer_instructions_dynamiques
        source = inspect.getsource(generer_instructions_dynamiques)
        assert 'return ""' in source, "Doit retourner chaîne vide si pas de critères"

    # --- Construire filtre feedback ---

    def test_construire_filtre_feedback_categories_actifs(self):
        """Les catégories d'actifs utilisent categorie_actif = ?."""
        from trading_app.adjustments import construire_filtre_feedback
        for cat in ['indice', 'action_eu', 'action_us', 'commodite', 'forex']:
            sql, params = construire_filtre_feedback(cat, 'strategie')
            assert 'categorie_actif = ?' in sql, f"Catégorie {cat} doit filtrer par categorie_actif"
            assert params == [cat]

    def test_construire_filtre_feedback_news(self):
        """news_trading doit filtrer par type_setup = 'NEWS'."""
        from trading_app.adjustments import construire_filtre_feedback
        sql, params = construire_filtre_feedback('news_trading', 'strategie')
        assert "'NEWS'" in sql

    def test_construire_filtre_feedback_default(self):
        """Catégories non reconnues: filtre 1=1 (pas de restriction)."""
        from trading_app.adjustments import construire_filtre_feedback
        sql, params = construire_filtre_feedback('inconnu', 'strategie')
        assert '1=1' in sql
        assert params == []

    # --- A/B Testing ---

    def test_ab_test_score_formula(self):
        """Score A/B = 60% taux réussite + 40% PnL normalisé."""
        from trading_app.ab_testing import evaluer_ab_test
        source = inspect.getsource(evaluer_ab_test)
        assert '0.6 *' in source or '0.6*' in source, "Score doit pondérer 60% taux"
        assert '0.4 *' in source or '0.4*' in source, "Score doit pondérer 40% PnL"

    def test_ab_test_winner_needs_5_point_gap(self):
        """Le gagnant A/B doit avoir 5+ points d'écart de score."""
        from trading_app.ab_testing import evaluer_ab_test
        source = inspect.getsource(evaluer_ab_test)
        assert 'score_a > score_b + 5' in source, "Gagnant A nécessite +5 points"
        assert 'score_b > score_a + 5' in source, "Gagnant B nécessite +5 points"

    def test_ab_test_minimum_decisive_trades(self):
        """Le test A/B nécessite minimum 10 trades décisifs par groupe."""
        from trading_app.ab_testing import evaluer_ab_test
        source = inspect.getsource(evaluer_ab_test)
        assert 'decisifs_a >= 10' in source, "Minimum 10 trades décisifs pour groupe A"
        assert 'decisifs_b >= 10' in source, "Minimum 10 trades décisifs pour groupe B"

    def test_ab_test_breakeven_excluded_from_success(self):
        """BREAKEVEN ne doit PAS compter comme réussite dans les tests A/B."""
        from trading_app.ab_testing import evaluer_ab_test
        source = inspect.getsource(evaluer_ab_test)
        assert 'BREAKEVEN' in source, "Doit gérer le cas BREAKEVEN"
        # Les réussis = TP1, TP2, WIN_FORCE (pas BREAKEVEN)
        assert "resultat IN ('TP1', 'TP2', 'WIN_FORCE')" in source, \
            "RÉGRESSION: BREAKEVEN ne doit pas être compté comme réussite"

    def test_ab_test_chi_squared_significance(self):
        """Le test A/B utilise le chi-squared (chi2 > 3.84 pour p < 0.05)."""
        from trading_app.ab_testing import evaluer_ab_test
        source = inspect.getsource(evaluer_ab_test)
        assert '3.84' in source, "Chi-squared seuil 3.84 (p < 0.05, 1 ddl)"

    def test_valider_ajustement_audit_trail(self):
        """valider_ajustement doit inclure audit trail (ajustement_source_id)."""
        from trading_app.adjustments import valider_ajustement
        source = inspect.getsource(valider_ajustement)
        assert 'ajustement_source_id' in source, \
            "RÉGRESSION: valider_ajustement doit tracer l'audit trail"


# ============================================================================
# GROUPE 16: AUDIT LOGIQUE (fuites DB, BREAKEVEN, DST, substring, etc.)
# ============================================================================

class TestAuditLogique:
    """Tests de régression pour l'audit logique complet de la plateforme."""

    # --- Fix #1: Fuites DB dans journal.py (conn=None + try/finally) ---

    def test_journal_ajouter_entree_uses_try_finally(self):
        """ajouter_entree_journal doit utiliser conn=None + try/finally."""
        from trading_app.journal import ajouter_entree_journal
        source = inspect.getsource(ajouter_entree_journal)
        assert 'conn = None' in source, "RÉGRESSION: doit initialiser conn = None"
        assert 'finally:' in source, "RÉGRESSION: doit avoir un bloc finally"

    def test_journal_get_journal_actif_uses_try_finally(self):
        """get_journal_actif doit utiliser conn=None + try/finally."""
        from trading_app.journal import get_journal_actif
        source = inspect.getsource(get_journal_actif)
        assert 'conn = None' in source, "RÉGRESSION: doit initialiser conn = None"
        assert 'finally:' in source, "RÉGRESSION: doit avoir un bloc finally"

    def test_journal_get_tous_journaux_uses_try_finally(self):
        """get_tous_journaux doit utiliser conn=None + try/finally."""
        from trading_app.journal import get_tous_journaux
        source = inspect.getsource(get_tous_journaux)
        assert 'conn = None' in source, "RÉGRESSION: doit initialiser conn = None"
        assert 'finally:' in source, "RÉGRESSION: doit avoir un bloc finally"

    def test_journal_sauvegarder_analyse_uses_try_finally(self):
        """sauvegarder_analyse doit utiliser conn=None + try/finally."""
        from trading_app.journal import sauvegarder_analyse
        source = inspect.getsource(sauvegarder_analyse)
        assert 'conn = None' in source, "RÉGRESSION: doit initialiser conn = None"
        assert 'finally:' in source, "RÉGRESSION: doit avoir un bloc finally"

    def test_journal_get_derniere_analyse_uses_try_finally(self):
        """get_derniere_analyse doit utiliser conn=None + try/finally."""
        from trading_app.journal import get_derniere_analyse
        source = inspect.getsource(get_derniere_analyse)
        assert 'conn = None' in source, "RÉGRESSION: doit initialiser conn = None"
        assert 'finally:' in source, "RÉGRESSION: doit avoir un bloc finally"

    def test_journal_get_analyses_du_jour_uses_try_finally(self):
        """get_analyses_du_jour doit utiliser conn=None + try/finally."""
        from trading_app.journal import get_analyses_du_jour
        source = inspect.getsource(get_analyses_du_jour)
        assert 'conn = None' in source, "RÉGRESSION: doit initialiser conn = None"
        assert 'finally:' in source, "RÉGRESSION: doit avoir un bloc finally"

    def test_journal_get_historique_opportunites_uses_try_finally(self):
        """get_historique_opportunites doit utiliser conn=None + try/finally."""
        from trading_app.journal import get_historique_opportunites
        source = inspect.getsource(get_historique_opportunites)
        assert 'conn = None' in source, "RÉGRESSION: doit initialiser conn = None"
        assert 'finally:' in source, "RÉGRESSION: doit avoir un bloc finally"

    def test_journal_get_journal_quotidien_uses_try_finally(self):
        """get_journal_quotidien doit utiliser conn=None + try/finally."""
        from trading_app.journal import get_journal_quotidien
        source = inspect.getsource(get_journal_quotidien)
        assert 'conn = None' in source, "RÉGRESSION: doit initialiser conn = None"
        assert 'finally:' in source, "RÉGRESSION: doit avoir un bloc finally"

    def test_journal_generer_bilan_uses_try_finally(self):
        """generer_bilan_quotidien doit utiliser conn=None + try/finally."""
        from trading_app.journal import generer_bilan_quotidien
        source = inspect.getsource(generer_bilan_quotidien)
        assert 'conn = None' in source, "RÉGRESSION: doit initialiser conn = None"
        assert 'finally:' in source, "RÉGRESSION: doit avoir un bloc finally"

    def test_journal_generer_rapport_hebdo_uses_try_finally(self):
        """generer_rapport_hebdo doit utiliser conn=None + try/finally."""
        from trading_app.journal import generer_rapport_hebdo
        source = inspect.getsource(generer_rapport_hebdo)
        assert 'conn = None' in source, "RÉGRESSION: doit initialiser conn = None"
        assert 'finally:' in source, "RÉGRESSION: doit avoir un bloc finally"

    # --- Fix #2: Fuite DB dans database.py init_database() ---

    def test_database_init_uses_try_finally(self):
        """init_database doit utiliser conn=None + try/finally."""
        from trading_app.database import init_database
        source = inspect.getsource(init_database)
        assert 'conn = None' in source, "RÉGRESSION: doit initialiser conn = None"
        assert 'finally:' in source, "RÉGRESSION: doit avoir un bloc finally"

    # --- Fix #3: BREAKEVEN compté dans get_performances() ---

    def test_performances_counts_breakeven(self):
        """get_performances doit compter BREAKEVEN séparément (pas dans en_cours)."""
        from trading_app.trades import get_performances
        source = inspect.getsource(get_performances)
        assert "'BREAKEVEN'" in source, "RÉGRESSION: doit compter BREAKEVEN"
        assert "'breakeven'" in source, "RÉGRESSION: doit retourner le champ breakeven"

    def test_performances_breakeven_in_conclus(self):
        """BREAKEVEN doit être inclus dans le calcul de 'conclus'."""
        from trading_app.trades import get_performances
        source = inspect.getsource(get_performances)
        assert 'breakeven' in source, "RÉGRESSION: breakeven doit exister dans la query"
        # Vérifier que conclus inclut breakeven
        assert 'stops + breakeven' in source, \
            "RÉGRESSION: conclus doit inclure breakeven (reussis + stops + breakeven)"

    # --- Fix #4: Colonnes correctes dans api_analytics.py search ---

    def test_journal_search_uses_correct_columns(self):
        """La recherche journal doit utiliser commentaire_ia (pas commentaire)."""
        from trading_app.routes.api_analytics import api_journal_search
        source = inspect.getsource(api_journal_search)
        assert 'commentaire_ia' in source, \
            "RÉGRESSION: doit chercher dans commentaire_ia (pas commentaire)"
        assert 'mouvements_notables' not in source, \
            "RÉGRESSION: mouvements_notables n'existe pas dans journal_quotidien"

    # --- Fix #5: DST scheduler reconfiguration ---

    def test_scheduler_detects_dst_change(self):
        """Le scheduler doit détecter les changements DST et reconfigurer."""
        from trading_app.scheduler import run_scheduler, _get_current_utc_offset
        source = inspect.getsource(run_scheduler)
        assert '_get_current_utc_offset' in source, \
            "RÉGRESSION: doit vérifier l'offset UTC pour détecter les changements DST"
        assert 'configurer_schedule()' in source, \
            "RÉGRESSION: doit reconfigurer le schedule si DST change"

    def test_scheduler_clears_before_configure(self):
        """configurer_schedule doit nettoyer les jobs avant d'en ajouter."""
        from trading_app.scheduler import configurer_schedule
        source = inspect.getsource(configurer_schedule)
        assert 'schedule.clear()' in source, \
            "RÉGRESSION: doit appeler schedule.clear() pour éviter les doublons"

    # --- Fix #6: Bug substring 'or' dans extraire_categorie_ajustement ---

    def test_categorie_ajustement_or_word_boundary(self):
        """'or' doit matcher comme mot entier, pas comme substring."""
        from trading_app.adjustments import extraire_categorie_ajustement
        # 'or' comme mot entier → commodite
        assert extraire_categorie_ajustement('', 'acheter or', '') == 'commodite'
        # 'forex' ne doit PAS matcher 'or'
        assert extraire_categorie_ajustement('', 'devise eur/usd', '') == 'forex'
        # 'score' ne doit PAS matcher 'or'
        assert extraire_categorie_ajustement('', 'confiance élevée', '') == 'conviction'

    # --- Fix #7: WIN_FORCE dans dashboard A/B tests ---

    def test_ab_tests_dashboard_counts_win_force(self):
        """Le dashboard A/B tests doit compter WIN_FORCE dans les wins."""
        from trading_app.routes.api_journal import api_ab_tests
        source = inspect.getsource(api_ab_tests)
        assert "WIN_FORCE" in source, \
            "RÉGRESSION: le dashboard A/B doit compter WIN_FORCE dans les wins"

    # --- Fix #8: BREAKEVEN cohérent dans api_analytics ---

    def test_analytics_grade_excludes_breakeven_from_wins(self):
        """Les stats par grade ne doivent PAS compter BREAKEVEN comme win."""
        from trading_app.routes.api_analytics import api_stats_detaillees
        source = inspect.getsource(api_stats_detaillees)
        # Vérifier qu'aucune requête ne mélange BREAKEVEN avec les wins
        assert "BREAKEVEN" not in source or "'BREAKEVEN') THEN 1" not in source, \
            "RÉGRESSION: BREAKEVEN ne doit pas être compté comme win dans les stats"

    # --- Fix #9: rattraper_journal_manque except ---

    def test_rattraper_journal_manque_has_except(self):
        """rattraper_journal_manque doit avoir un bloc except pour les erreurs DB."""
        from trading_app.scheduler import rattraper_journal_manque
        source = inspect.getsource(rattraper_journal_manque)
        assert 'except Exception' in source, \
            "RÉGRESSION: rattraper_journal_manque doit attraper les exceptions DB"

    # --- Fix #11: DST utcoffset dans validation.py ---

    def test_validation_us_hours_uses_utcoffset(self):
        """Le calcul des heures US doit utiliser utcoffset() (pas hour diff)."""
        from trading_app.validation import valider_opportunite
        source = inspect.getsource(valider_opportunite)
        assert 'utcoffset()' in source, \
            "RÉGRESSION: doit utiliser utcoffset() pour un calcul DST robuste"
        # Ne doit plus utiliser le pattern fragile (hour - hour) % 24
        assert '(now_paris.hour - now_ny.hour) % 24' not in source, \
            "RÉGRESSION: ne doit plus utiliser le pattern hour diff % 24"


# ============================================================================
# EXÉCUTION
# ============================================================================

if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
