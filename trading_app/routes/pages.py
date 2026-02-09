"""Routes pages (templates HTML)."""
from flask import Blueprint, render_template
from ..config import logger

bp = Blueprint('pages', __name__)

@bp.route('/')
def index():
    """Page d'accueil - Dashboard principal"""
    try:
        return render_template('index.html')
    except Exception as e:
        logger.error(f"Erreur page index: {e}")
        return f"Erreur de chargement: {e}", 500

@bp.route('/trading')
def trading():
    """Page d'aide au trading"""
    try:
        return render_template('trading.html')
    except Exception as e:
        logger.error(f"Erreur page trading: {e}")
        return f"Erreur de chargement: {e}", 500

@bp.route('/memoire')
def memoire():
    """Page mémoire - Journal et performances"""
    try:
        return render_template('memoire.html')
    except Exception as e:
        logger.error(f"Erreur page memoire: {e}")
        return f"Erreur de chargement: {e}", 500

@bp.route('/journal')
def journal():
    """Page journal automatique"""
    try:
        return render_template('journal.html')
    except Exception as e:
        logger.error(f"Erreur page journal: {e}")
        return f"Erreur de chargement: {e}", 500

@bp.route('/journal/<symbole>')
def journal_actif(symbole):
    """Page journal d'un actif spécifique"""
    try:
        return render_template('journal_actif.html', symbole=symbole)
    except Exception as e:
        logger.error(f"Erreur page journal_actif {symbole}: {e}")
        return f"Erreur de chargement: {e}", 500

@bp.route('/ajustements')
def ajustements():
    """Page de gestion des ajustements proposés par Claude"""
    try:
        return render_template('ajustements.html')
    except Exception as e:
        logger.error(f"Erreur page ajustements: {e}")
        return f"Erreur de chargement: {e}", 500

