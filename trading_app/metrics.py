"""
Métriques de performance prédictive: precision, profit factor, calibration conviction/grade.
"""
import sqlite3
from datetime import timedelta

from .config import DB_PATH, DB_TIMEOUT, logger
from .market_context import get_paris_time


def calculer_metriques_prediction(jours=30):
    """
    Calcule les métriques de qualité prédictive sur les N derniers jours.

    Returns dict:
        precision: % trades gagnants / trades conclus
        profit_factor: gains bruts / pertes brutes
        total_trades: nombre de trades conclus
        win_rate_par_grade: {A: 0.xx, B: 0.xx, ...}
        calibration_conviction: {1: {nb, win_rate, avg_pnl}, ...}
        gains_bruts / pertes_brutes: en % cumulé
        pnl_par_regime: {CALME: {nb, win_rate, pnl}, ...}
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        date_debut = (get_paris_time() - timedelta(days=jours)).strftime('%Y-%m-%d')

        cursor.execute('''
            SELECT resultat, pnl_pct, trade_grade, conviction_score,
                   grade_setup_score, regime_marche, categorie_actif
            FROM trades_recommandes
            WHERE date >= ? AND resultat IS NOT NULL
        ''', (date_debut,))

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return None

        trades = [dict(r) for r in rows]

        # Trades gagnants / perdants
        wins = [t for t in trades if t['resultat'] in ('TP1', 'TP2', 'WIN_FORCE')]
        losses = [t for t in trades if t['resultat'] in ('STOP', 'LOSS_FORCE')]
        total_conclus = len(wins) + len(losses)

        if total_conclus == 0:
            return None

        precision = len(wins) / total_conclus

        # Profit Factor = sum(gains) / abs(sum(pertes))
        gains_bruts = sum(t['pnl_pct'] for t in wins if t['pnl_pct'])
        pertes_brutes = abs(sum(t['pnl_pct'] for t in losses if t['pnl_pct']))
        profit_factor = round(gains_bruts / pertes_brutes, 2) if pertes_brutes > 0 else 99.0

        # Win rate par grade (A/B/C/D)
        win_rate_par_grade = {}
        for grade in ['A', 'B', 'C', 'D']:
            grade_wins = [t for t in wins if t['trade_grade'] == grade]
            grade_losses = [t for t in losses if t['trade_grade'] == grade]
            grade_conclus = len(grade_wins) + len(grade_losses)
            if grade_conclus > 0:
                win_rate_par_grade[grade] = {
                    'nb': grade_conclus,
                    'win_rate': round(len(grade_wins) / grade_conclus, 3),
                    'avg_pnl': round(
                        sum(t['pnl_pct'] for t in grade_wins + grade_losses if t['pnl_pct']) / grade_conclus, 2
                    )
                }

        # Calibration conviction (1-5) : conviction haute doit gagner plus
        calibration_conviction = {}
        for conv in range(1, 6):
            conv_wins = [t for t in wins if t['conviction_score'] == conv]
            conv_losses = [t for t in losses if t['conviction_score'] == conv]
            conv_conclus = len(conv_wins) + len(conv_losses)
            if conv_conclus > 0:
                all_conv = conv_wins + conv_losses
                calibration_conviction[conv] = {
                    'nb': conv_conclus,
                    'win_rate': round(len(conv_wins) / conv_conclus, 3),
                    'avg_pnl': round(
                        sum(t['pnl_pct'] for t in all_conv if t['pnl_pct']) / conv_conclus, 2
                    )
                }

        # PnL par régime marché
        pnl_par_regime = {}
        for regime in ['CALME', 'NORMAL', 'VOLATILE', 'EXTREME']:
            reg_wins = [t for t in wins if t['regime_marche'] == regime]
            reg_losses = [t for t in losses if t['regime_marche'] == regime]
            reg_conclus = len(reg_wins) + len(reg_losses)
            if reg_conclus > 0:
                all_reg = reg_wins + reg_losses
                pnl_par_regime[regime] = {
                    'nb': reg_conclus,
                    'win_rate': round(len(reg_wins) / reg_conclus, 3),
                    'pnl_total': round(sum(t['pnl_pct'] for t in all_reg if t['pnl_pct']), 2)
                }

        return {
            'precision': round(precision, 3),
            'profit_factor': profit_factor,
            'total_trades': total_conclus,
            'wins': len(wins),
            'losses': len(losses),
            'gains_bruts': round(gains_bruts, 2),
            'pertes_brutes': round(pertes_brutes, 2),
            'win_rate_par_grade': win_rate_par_grade,
            'calibration_conviction': calibration_conviction,
            'pnl_par_regime': pnl_par_regime,
            'jours_analyse': jours
        }

    except Exception as e:
        logger.error(f"Erreur calcul métriques prédiction: {e}")
        return None


def generer_contexte_metriques_pour_prompt(jours=14):
    """
    Génère un bloc de texte à injecter dans le prompt Claude avec les métriques
    de performance récentes, pour permettre l'auto-correction.

    Returns: str (vide si pas assez de données)
    """
    metriques = calculer_metriques_prediction(jours=jours)

    if not metriques or metriques['total_trades'] < 10:
        return ""

    lines = []
    lines.append(f"\n=== PERFORMANCE RÉCENTE ({jours} jours, {metriques['total_trades']} trades) ===")
    lines.append(f"Precision: {metriques['precision']:.0%} | Profit Factor: {metriques['profit_factor']:.1f}")
    lines.append(f"Gains bruts: +{metriques['gains_bruts']:.1f}% | Pertes brutes: -{metriques['pertes_brutes']:.1f}%")

    # Win rate par grade
    if metriques['win_rate_par_grade']:
        grades_txt = []
        for grade in ['A', 'B', 'C', 'D']:
            if grade in metriques['win_rate_par_grade']:
                g = metriques['win_rate_par_grade'][grade]
                grades_txt.append(f"{grade}={g['win_rate']:.0%}({g['nb']})")
        if grades_txt:
            lines.append(f"Win rate par grade: {' | '.join(grades_txt)}")

    # Calibration conviction
    if metriques['calibration_conviction']:
        conv_txt = []
        for conv in range(1, 6):
            if conv in metriques['calibration_conviction']:
                c = metriques['calibration_conviction'][conv]
                conv_txt.append(f"Conv{conv}={c['win_rate']:.0%}({c['nb']})")
        if conv_txt:
            lines.append(f"Calibration conviction: {' | '.join(conv_txt)}")

    # Alertes auto-correctives
    if metriques['profit_factor'] < 1.0:
        lines.append("⚠️ PROFIT FACTOR < 1 : Sois PLUS SÉLECTIF. Augmente conviction minimum à 4. Réduis le nombre d'opportunités.")

    if metriques['precision'] < 0.4:
        lines.append("⚠️ PRECISION < 40% : Trop de trades perdants. Privilégie uniquement les setups avec alignement_tf >= 2 et confluence >= 7.")

    # Vérifier calibration : conviction 5 doit battre conviction 3
    cal = metriques['calibration_conviction']
    if 5 in cal and 3 in cal:
        if cal[5]['win_rate'] < cal[3]['win_rate'] and cal[5]['nb'] >= 3:
            lines.append("⚠️ CONVICTION MAL CALIBRÉE : Tes trades conviction 5 gagnent MOINS que les conviction 3. Réévalue tes critères de haute conviction.")

    # Meilleur/pire régime
    if metriques['pnl_par_regime']:
        best = max(metriques['pnl_par_regime'].items(), key=lambda x: x[1]['pnl_total'])
        worst = min(metriques['pnl_par_regime'].items(), key=lambda x: x[1]['pnl_total'])
        if worst[1]['pnl_total'] < 0:
            lines.append(f"📊 Meilleur régime: {best[0]} (PnL +{best[1]['pnl_total']:.1f}%) | Pire: {worst[0]} (PnL {worst[1]['pnl_total']:.1f}%)")

    return "\n".join(lines)
