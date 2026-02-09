"""
Tests QA: Robustesse des endpoints API — validation input, edge cases.
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from trading_app.config import app
from trading_app.routes import register_blueprints

# Register blueprints once at module level (not in fixture)
_blueprints_registered = False

@pytest.fixture
def client():
    """Client de test Flask"""
    global _blueprints_registered
    if not _blueprints_registered:
        try:
            register_blueprints(app)
        except AssertionError:
            pass  # Already registered via app.py import
        _blueprints_registered = True
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


# ============================================================================
# TESTS INPUT VALIDATION: int() parameters
# ============================================================================

class TestInputValidation:
    """Vérifie que les paramètres non-numériques ne crashent pas l'app"""

    def test_news_historique_jours_invalide(self, client):
        """?jours=abc ne doit pas retourner 500"""
        resp = client.get('/api/news/historique?jours=abc')
        assert resp.status_code != 500

    def test_journal_quotidien_limite_invalide(self, client):
        """?limite=abc ne doit pas retourner 500"""
        resp = client.get('/api/journal-quotidien?limite=abc')
        assert resp.status_code != 500

    def test_journal_quotidien_page_invalide(self, client):
        """?page=abc ne doit pas retourner 500"""
        resp = client.get('/api/journal-quotidien?page=abc')
        assert resp.status_code != 500

    def test_trades_historique_page_invalide(self, client):
        """?page=abc ne doit pas retourner 500"""
        resp = client.get('/api/trades-historique?page=abc')
        assert resp.status_code != 500

    def test_trades_historique_limit_invalide(self, client):
        """?limit=abc ne doit pas retourner 500"""
        resp = client.get('/api/trades-historique?limit=abc')
        assert resp.status_code != 500

    def test_equity_curve_limite_invalide(self, client):
        """?limite=abc ne doit pas retourner 500"""
        resp = client.get('/api/equity-curve?limite=abc')
        # Flask's type=int returns default on invalid input, so should be 200
        assert resp.status_code == 200

    def test_metrics_jours_invalide(self, client):
        """?jours=abc ne doit pas retourner 500"""
        resp = client.get('/api/metrics?jours=abc')
        assert resp.status_code != 500

    def test_ajustements_historique_limite_invalide(self, client):
        """?limite=abc ne doit pas retourner 500"""
        resp = client.get('/api/ajustements-historique?limite=abc')
        assert resp.status_code != 500


# ============================================================================
# TESTS ENDPOINTS: réponse correcte
# ============================================================================

class TestEndpointsBasic:
    """Vérifie que chaque endpoint retourne au minimum un JSON valide"""

    def test_status(self, client):
        resp = client.get('/api/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'success' in data or 'status' in data

    def test_metrics(self, client):
        resp = client.get('/api/metrics')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'success' in data

    def test_metrics_jours_param(self, client):
        resp = client.get('/api/metrics?jours=7')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True

    def test_regime_marche(self, client):
        resp = client.get('/api/regime-marche')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get('success') is True

    def test_contexte_trading(self, client):
        resp = client.get('/api/contexte-trading')
        assert resp.status_code == 200

    def test_trades_jour(self, client):
        resp = client.get('/api/trades-jour')
        assert resp.status_code == 200

    def test_trades_ouverts(self, client):
        resp = client.get('/api/trades/ouverts')
        assert resp.status_code == 200

    def test_performances(self, client):
        resp = client.get('/api/performances')
        assert resp.status_code == 200

    def test_stats_avancees(self, client):
        resp = client.get('/api/stats-avancees')
        assert resp.status_code == 200

    def test_derniere_analyse(self, client):
        resp = client.get('/api/derniere-analyse')
        assert resp.status_code == 200

    def test_criteres_dynamiques(self, client):
        resp = client.get('/api/criteres-dynamiques')
        assert resp.status_code == 200

    def test_index_page(self, client):
        resp = client.get('/')
        assert resp.status_code == 200

    def test_trading_page(self, client):
        resp = client.get('/trading')
        assert resp.status_code == 200

    def test_memoire_page(self, client):
        resp = client.get('/memoire')
        assert resp.status_code == 200

    def test_ajustements_page(self, client):
        resp = client.get('/ajustements')
        assert resp.status_code == 200


# ============================================================================
# TESTS EDGE CASES: pagination extrême
# ============================================================================

class TestPaginationEdgeCases:
    def test_page_negative(self, client):
        """page=-1 ne doit pas crasher"""
        resp = client.get('/api/trades-historique?page=-1')
        assert resp.status_code == 200

    def test_page_tres_grande(self, client):
        """page=999999 retourne une liste vide, pas un crash"""
        resp = client.get('/api/trades-historique?page=999999')
        assert resp.status_code == 200

    def test_limit_zero(self, client):
        """limit=0 ne doit pas crasher"""
        resp = client.get('/api/trades-historique?limit=0')
        assert resp.status_code == 200

    def test_limit_tres_grand(self, client):
        """limit=999999 doit être cappé par le max interne"""
        resp = client.get('/api/trades-historique?limit=999999')
        assert resp.status_code == 200
