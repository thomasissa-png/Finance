"""
Tests QA: Module metrics.py — métriques prédictives.
"""
import sys
import os
import sqlite3
import pytest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from trading_app.config import DB_PATH
from trading_app.metrics import calculer_metriques_prediction, generer_contexte_metriques_pour_prompt


@pytest.fixture
def db_with_trades(tmp_path, monkeypatch):
    """Crée une DB temporaire avec des trades de test"""
    db_path = str(tmp_path / "test_trading.db")
    monkeypatch.setattr('trading_app.metrics.DB_PATH', db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE trades_recommandes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE,
            resultat TEXT,
            pnl_pct REAL,
            trade_grade TEXT,
            conviction_score INTEGER,
            grade_setup_score INTEGER,
            regime_marche TEXT,
            categorie_actif TEXT
        )
    ''')

    today = datetime.now().strftime('%Y-%m-%d')
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    # 6 wins, 4 losses = 60% precision
    trades = [
        (today, 'TP1', 1.2, 'A', 5, 90, 'NORMAL', 'action_us'),
        (today, 'TP1', 0.8, 'A', 4, 85, 'NORMAL', 'action_us'),
        (today, 'TP2', 2.1, 'B', 4, 75, 'CALME', 'indice'),
        (yesterday, 'TP1', 0.9, 'B', 3, 70, 'NORMAL', 'action_fr'),
        (yesterday, 'WIN_FORCE', 0.5, 'C', 3, 55, 'VOLATILE', 'forex'),
        (yesterday, 'TP1', 1.0, 'C', 3, 60, 'CALME', 'commodite'),
        (today, 'STOP', -0.7, 'B', 4, 72, 'NORMAL', 'action_us'),
        (today, 'STOP', -0.8, 'C', 3, 52, 'VOLATILE', 'forex'),
        (yesterday, 'LOSS_FORCE', -1.2, 'D', 2, 40, 'EXTREME', 'indice'),
        (yesterday, 'STOP', -0.6, 'C', 3, 55, 'NORMAL', 'action_fr'),
    ]

    for t in trades:
        cursor.execute('''
            INSERT INTO trades_recommandes (date, resultat, pnl_pct, trade_grade,
                conviction_score, grade_setup_score, regime_marche, categorie_actif)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', t)

    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def db_empty(tmp_path, monkeypatch):
    """DB vide"""
    db_path = str(tmp_path / "test_empty.db")
    monkeypatch.setattr('trading_app.metrics.DB_PATH', db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE trades_recommandes (
            id INTEGER PRIMARY KEY, date DATE, resultat TEXT, pnl_pct REAL,
            trade_grade TEXT, conviction_score INTEGER, grade_setup_score INTEGER,
            regime_marche TEXT, categorie_actif TEXT
        )
    ''')
    conn.commit()
    conn.close()
    return db_path


class TestCalculerMetriques:
    def test_precision_correct(self, db_with_trades):
        """Precision = 6 wins / 10 conclus = 0.6"""
        m = calculer_metriques_prediction(jours=30)
        assert m is not None
        assert m['precision'] == 0.6
        assert m['wins'] == 6
        assert m['losses'] == 4

    def test_profit_factor_correct(self, db_with_trades):
        """Profit factor = gains bruts / pertes brutes"""
        m = calculer_metriques_prediction(jours=30)
        gains = 1.2 + 0.8 + 2.1 + 0.9 + 0.5 + 1.0  # 6.5
        pertes = abs(-0.7 + -0.8 + -1.2 + -0.6)  # 3.3
        expected_pf = round(gains / pertes, 2)
        assert m['profit_factor'] == expected_pf

    def test_win_rate_par_grade(self, db_with_trades):
        """Win rate par grade: A=100%, B=66%, C=50%, D=0%"""
        m = calculer_metriques_prediction(jours=30)
        assert 'A' in m['win_rate_par_grade']
        assert m['win_rate_par_grade']['A']['win_rate'] == 1.0  # 2/2

    def test_calibration_conviction(self, db_with_trades):
        """Les trades conviction 5 doivent avoir un win rate calculé"""
        m = calculer_metriques_prediction(jours=30)
        assert 5 in m['calibration_conviction']
        assert m['calibration_conviction'][5]['nb'] == 1

    def test_db_vide_retourne_none(self, db_empty):
        """DB sans trades retourne None"""
        m = calculer_metriques_prediction(jours=30)
        assert m is None

    def test_pnl_par_regime(self, db_with_trades):
        """PnL par régime calculé correctement"""
        m = calculer_metriques_prediction(jours=30)
        assert 'NORMAL' in m['pnl_par_regime']
        assert m['pnl_par_regime']['NORMAL']['nb'] > 0


class TestGenererContextePrompt:
    def test_pas_assez_de_trades(self, db_empty):
        """Avec moins de 10 trades, retourne chaîne vide"""
        result = generer_contexte_metriques_pour_prompt(jours=30)
        assert result == ""

    def test_contexte_avec_trades(self, db_with_trades):
        """Avec 10 trades, retourne un contexte non-vide"""
        result = generer_contexte_metriques_pour_prompt(jours=30)
        assert len(result) > 0
        assert 'Precision' in result
        assert 'Profit Factor' in result

    def test_alerte_profit_factor_bas(self, tmp_path, monkeypatch):
        """Si profit factor < 1, alerte auto-corrective"""
        db_path = str(tmp_path / "test_pf_bas.db")
        monkeypatch.setattr('trading_app.metrics.DB_PATH', db_path)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE trades_recommandes (
                id INTEGER PRIMARY KEY, date DATE, resultat TEXT, pnl_pct REAL,
                trade_grade TEXT, conviction_score INTEGER, grade_setup_score INTEGER,
                regime_marche TEXT, categorie_actif TEXT
            )
        ''')
        today = datetime.now().strftime('%Y-%m-%d')
        # 3 wins petits, 7 grosses pertes => PF < 1
        for _ in range(3):
            cursor.execute('''INSERT INTO trades_recommandes
                (date, resultat, pnl_pct, trade_grade, conviction_score,
                 grade_setup_score, regime_marche, categorie_actif)
                VALUES (?,?,?,?,?,?,?,?)''',
                (today, 'TP1', 0.3, 'C', 3, 50, 'NORMAL', 'action_us'))
        for _ in range(7):
            cursor.execute('''INSERT INTO trades_recommandes
                (date, resultat, pnl_pct, trade_grade, conviction_score,
                 grade_setup_score, regime_marche, categorie_actif)
                VALUES (?,?,?,?,?,?,?,?)''',
                (today, 'STOP', -0.8, 'D', 2, 40, 'NORMAL', 'action_us'))
        conn.commit()
        conn.close()

        result = generer_contexte_metriques_pour_prompt(jours=30)
        assert 'PROFIT FACTOR < 1' in result
