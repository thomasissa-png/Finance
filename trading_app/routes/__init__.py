"""
Routes Flask - Enregistrement des blueprints.
"""
from .pages import bp as pages_bp
from .api_market import bp as api_market_bp
from .api_trades import bp as api_trades_bp
from .api_journal import bp as api_journal_bp
from .api_analytics import bp as api_analytics_bp
from .api_adjustments import bp as api_adjustments_bp

def register_blueprints(app):
    """Enregistre tous les blueprints Flask."""
    app.register_blueprint(pages_bp)
    app.register_blueprint(api_market_bp)
    app.register_blueprint(api_trades_bp)
    app.register_blueprint(api_journal_bp)
    app.register_blueprint(api_analytics_bp)
    app.register_blueprint(api_adjustments_bp)
