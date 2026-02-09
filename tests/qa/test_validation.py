"""
Tests QA: Validation des opportunités, seuils dynamiques, heures de marché.
"""
import sys
import os
import pytest
from unittest.mock import patch
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from trading_app.validation import valider_opportunite


# ============================================================================
# FIXTURES: Opportunités de référence
# ============================================================================

def make_opp(direction='LONG', entree=100, stop=99, tp1=102, tp2=104,
             symbole='AAPL', atr_pct=2.0, regime='NORMAL', conviction=4,
             ratio_rr='1:2', **kwargs):
    """Crée une opportunité de test valide par défaut"""
    opp = {
        'actif': 'Apple',
        'symbole': symbole,
        'direction': direction,
        'entree': entree,
        'stop': stop,
        'tp1': tp1,
        'tp2': tp2,
        'atr_pct': atr_pct,
        'regime_marche': regime,
        'conviction_score': conviction,
        'ratio_rr': ratio_rr,
        'trend_daily': 'UP',
        'trend_h4': 'UP',
        'trend_h1': 'UP',
        'alignement_tf': 3,
        'confluence_score': 7,
        'sentiment_score': 3,
        'strategie_entree': 'IMMEDIATE',
        'validite_minutes': 60,
        'heure_expiration': '16:00',
        'urgence': 'MOYENNE',
        'session_marche': 'US_OPEN',
        'trailing_stop': 0,
        'trailing_stop_pct': 0,
        'catalyseur': 'Test',
        'timing': 'Maintenant',
        'duree': '1-2h',
        'invalidation': 'Sous 99',
        'volume_relatif': 120,
        'rsi': 55,
        'macd_signal': 'BULLISH',
    }
    opp.update(kwargs)
    return opp


# ============================================================================
# TESTS SEUIL DYNAMIQUE (Reco 1 de l'audit ML)
# ============================================================================

class TestSeuilDynamique:
    """Vérifie que le seuil 0.7% a été remplacé par le seuil dynamique ATR × VIX"""

    @patch('trading_app.validation.get_paris_time')
    def test_seuil_calme_atr_2pct(self, mock_time):
        """En régime CALME avec ATR=2%, seuil = max(0.5, 2*0.30) = 0.6%"""
        mock_time.return_value = datetime(2025, 2, 10, 16, 0)  # Lundi 16h
        # gain=0.55%, risk=0.4%, RR=1.375 (>1.3 pour CALME)
        opp = make_opp(entree=100, tp1=100.55, stop=99.6, atr_pct=2.0,
                       regime='CALME', ratio_rr='1:1.4')
        valide, _, avertissements, erreurs = valider_opportunite(opp)
        # Gain cible = 0.55% < seuil dynamique(0.6%), devrait avertir
        assert any('Gain cible faible' in a for a in avertissements)

    @patch('trading_app.validation.get_paris_time')
    def test_seuil_volatile_atr_3pct(self, mock_time):
        """En régime VOLATILE avec ATR=3%, seuil = max(0.5, 3*0.25) = 0.75%"""
        mock_time.return_value = datetime(2025, 2, 10, 16, 0)
        # gain=0.70%, risk=0.35%, RR=2.0 (>1.8 pour VOLATILE)
        opp = make_opp(entree=100, tp1=100.70, stop=99.65, atr_pct=3.0,
                       regime='VOLATILE', ratio_rr='1:2')
        valide, _, avertissements, erreurs = valider_opportunite(opp)
        # Gain cible = 0.70% < seuil(0.75%), devrait avertir
        assert any('Gain cible faible' in a for a in avertissements)

    @patch('trading_app.validation.get_paris_time')
    def test_seuil_plancher_05pct(self, mock_time):
        """Le seuil minimum ne descend jamais sous 0.5%"""
        mock_time.return_value = datetime(2025, 2, 10, 16, 0)
        # ATR=1%, EXTREME => seuil = max(0.5, 1*0.20) = 0.5%
        # gain=0.45%, risk=0.2%, RR=2.25 (>2.0 pour EXTREME)
        opp = make_opp(entree=100, tp1=100.45, stop=99.8, atr_pct=1.0,
                       regime='EXTREME', ratio_rr='1:2.25')
        valide, _, avertissements, erreurs = valider_opportunite(opp)
        # Gain cible = 0.45% < seuil plancher(0.5%), devrait avertir
        assert any('Gain cible faible' in a for a in avertissements)

    @patch('trading_app.validation.get_paris_time')
    def test_seuil_fallback_sans_atr(self, mock_time):
        """Sans ATR, le seuil doit fallback à 0.7%"""
        mock_time.return_value = datetime(2025, 2, 10, 16, 0)
        # gain=0.65%, risk=0.4%, RR=1.625 (>1.5 pour NORMAL)
        opp = make_opp(entree=100, tp1=100.65, stop=99.6, atr_pct=0,
                       regime='NORMAL', ratio_rr='1:1.6')
        valide, _, avertissements, erreurs = valider_opportunite(opp)
        # Gain cible = 0.65% < fallback(0.7%), devrait avertir
        assert any('Gain cible faible' in a for a in avertissements)


# ============================================================================
# TESTS VALIDATION R:R PAR REGIME VIX
# ============================================================================

class TestRatioRR:
    @patch('trading_app.validation.get_paris_time')
    def test_rr_insuffisant_calme(self, mock_time):
        """R:R < 1.3 en régime CALME doit bloquer"""
        mock_time.return_value = datetime(2025, 2, 10, 16, 0)
        opp = make_opp(entree=100, stop=99, tp1=101, regime='CALME', ratio_rr='1:1')
        valide, _, _, erreurs = valider_opportunite(opp)
        assert not valide
        assert any('R:R insuffisant' in e for e in erreurs)

    @patch('trading_app.validation.get_paris_time')
    def test_rr_insuffisant_extreme(self, mock_time):
        """R:R < 2.0 en régime EXTREME doit bloquer"""
        mock_time.return_value = datetime(2025, 2, 10, 16, 0)
        opp = make_opp(entree=100, stop=99, tp1=101.5, regime='EXTREME', ratio_rr='1:1.5')
        valide, _, _, erreurs = valider_opportunite(opp)
        assert not valide
        assert any('R:R insuffisant' in e for e in erreurs)


# ============================================================================
# TESTS HEURES DE MARCHÉ (BLOQUANT)
# ============================================================================

class TestHeuresMarche:
    @patch('trading_app.validation.get_paris_time')
    def test_us_stock_marche_ferme_matin(self, mock_time):
        """Action US avant 15h30 CET doit être bloquée"""
        mock_time.return_value = datetime(2025, 2, 10, 13, 0)  # 13h CET
        opp = make_opp(symbole='AAPL')
        valide, _, _, erreurs = valider_opportunite(opp)
        assert not valide
        assert any('Marché US fermé' in e for e in erreurs)

    @patch('trading_app.validation.get_paris_time')
    def test_eu_stock_marche_ferme_soir(self, mock_time):
        """Action EU après 17h30 CET doit être bloquée"""
        mock_time.return_value = datetime(2025, 2, 10, 18, 0)  # 18h CET
        opp = make_opp(symbole='MC.PA', actif='LVMH')
        valide, _, _, erreurs = valider_opportunite(opp)
        assert not valide
        assert any('Marché EU fermé' in e for e in erreurs)

    @patch('trading_app.validation.get_paris_time')
    def test_forex_toujours_ouvert(self, mock_time):
        """Forex ne doit pas être bloqué par les heures de marché"""
        mock_time.return_value = datetime(2025, 2, 10, 3, 0)  # 3h CET
        opp = make_opp(symbole='EURUSD=X', actif='EUR/USD')
        valide, _, avertissements, erreurs = valider_opportunite(opp)
        # Pas d'erreur marché fermé (peut avoir d'autres erreurs)
        marche_errors = [e for e in erreurs if 'Marché' in e and 'fermé' in e]
        assert len(marche_errors) == 0


# ============================================================================
# TESTS VALIDATION STRUCTURE
# ============================================================================

class TestValidationStructure:
    @patch('trading_app.validation.get_paris_time')
    def test_stop_mauvais_sens_long(self, mock_time):
        """Stop au-dessus de l'entrée en LONG doit être rejeté"""
        mock_time.return_value = datetime(2025, 2, 10, 16, 0)
        opp = make_opp(direction='LONG', entree=100, stop=101, tp1=103)
        valide, _, _, erreurs = valider_opportunite(opp)
        assert not valide

    @patch('trading_app.validation.get_paris_time')
    def test_stop_mauvais_sens_short(self, mock_time):
        """Stop en dessous de l'entrée en SHORT doit être rejeté"""
        mock_time.return_value = datetime(2025, 2, 10, 16, 0)
        opp = make_opp(direction='SHORT', entree=100, stop=99, tp1=97)
        valide, _, _, erreurs = valider_opportunite(opp)
        assert not valide

    @patch('trading_app.validation.get_paris_time')
    def test_champs_manquants(self, mock_time):
        """Champs critiques manquants = rejet"""
        mock_time.return_value = datetime(2025, 2, 10, 16, 0)
        opp = {'actif': 'Apple', 'direction': 'LONG'}  # Pas d'entree, stop, tp1
        valide, _, _, erreurs = valider_opportunite(opp)
        assert not valide

    @patch('trading_app.validation.get_paris_time')
    def test_prix_zero(self, mock_time):
        """Prix à 0 doit être rejeté"""
        mock_time.return_value = datetime(2025, 2, 10, 16, 0)
        opp = make_opp(entree=0, stop=0, tp1=0)
        valide, _, _, erreurs = valider_opportunite(opp)
        assert not valide

    @patch('trading_app.validation.get_paris_time')
    def test_opportunite_valide_complete(self, mock_time):
        """Opportunité complète et cohérente doit être validée"""
        mock_time.return_value = datetime(2025, 2, 10, 16, 0)
        opp = make_opp()
        valide, opp_validee, avertissements, erreurs = valider_opportunite(opp)
        assert valide, f"Devrait être valide. Erreurs: {erreurs}"
        assert opp_validee is not None
