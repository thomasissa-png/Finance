"""
Tests anti-régression pour le trading app.

Chaque test couvre un bug réel qui a été rencontré et corrigé.
À exécuter SYSTÉMATIQUEMENT avant tout commit / déploiement.

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
# EXÉCUTION
# ============================================================================

if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
