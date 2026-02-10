"""
Script KPI avance pour evaluation quantitative des performances de trading.
Calcule: Sharpe, Sortino, Calmar, max drawdown, Kelly, streaks, equity curve.

Usage: python -m trading_app.scripts.kpi_report [--jours 90] [--json]
"""
import sqlite3
import json
import math
import sys
from datetime import timedelta, datetime
from collections import defaultdict

# Support both direct run and module import
try:
    from trading_app.config import DB_PATH, DB_TIMEOUT
    from trading_app.market_context import get_paris_time
except ImportError:
    DB_PATH = 'trading.db'
    DB_TIMEOUT = 10.0
    get_paris_time = lambda: datetime.now()


def calculer_kpis_avances(jours=90):
    """
    Calcule les KPIs quantitatifs professionnels sur N jours.

    Returns dict avec:
        - basiques: win_rate, profit_factor, expectancy
        - risque: sharpe, sortino, calmar, max_drawdown, ulcer_index
        - sizing: kelly_criterion, kelly_half
        - streaks: max_consecutive_wins/losses, current_streak
        - par_categorie: breakdown par asset class
        - par_direction: LONG vs SHORT
        - par_session: EU_OPEN, US_OPEN, etc.
        - equity_curve: liste de points (date, pnl_cumul)
        - completude: signaux emis vs resultats enregistres
    """
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    date_debut = (get_paris_time() - timedelta(days=jours)).strftime('%Y-%m-%d')

    # Tous les trades de la periode (emis)
    cursor.execute('''
        SELECT id, date, symbole, actif, direction, categorie_actif,
               session_marche, regime_marche, conviction_score, trade_grade,
               prix_entree, prix_stop, prix_tp1, prix_tp2, prix_sortie,
               resultat, pnl_pct, pnl_max, pnl_min,
               timestamp_reco, timestamp_sortie
        FROM trades_recommandes
        WHERE date >= ?
        ORDER BY date ASC, timestamp_reco ASC
    ''', (date_debut,))

    tous_trades = [dict(r) for r in cursor.fetchall()]
    conn.close()

    if not tous_trades:
        return {'erreur': 'Aucun trade sur la periode', 'jours': jours}

    # --- Completude du tracking ---
    total_emis = len(tous_trades)
    avec_resultat = [t for t in tous_trades if t['resultat'] is not None]
    sans_resultat = [t for t in tous_trades if t['resultat'] is None]
    expires = [t for t in tous_trades if t['resultat'] == 'EXPIRED']

    completude = {
        'signaux_emis': total_emis,
        'avec_resultat': len(avec_resultat),
        'sans_resultat': len(sans_resultat),
        'expires': len(expires),
        'taux_completion': round(len(avec_resultat) / total_emis * 100, 1) if total_emis > 0 else 0,
        'orphelins_ids': [t['id'] for t in sans_resultat]
    }

    # --- Filtrer les trades decisifs (avec PnL) ---
    trades_conclus = [t for t in avec_resultat
                      if t['resultat'] in ('TP1', 'TP2', 'STOP', 'WIN_FORCE', 'LOSS_FORCE', 'BREAKEVEN')]
    wins = [t for t in trades_conclus if t['resultat'] in ('TP1', 'TP2', 'WIN_FORCE')]
    losses = [t for t in trades_conclus if t['resultat'] in ('STOP', 'LOSS_FORCE')]

    if not trades_conclus:
        return {
            'erreur': 'Aucun trade conclu sur la periode',
            'completude': completude,
            'jours': jours
        }

    # --- Basiques ---
    pnls = [t['pnl_pct'] or 0 for t in trades_conclus]
    win_pnls = [t['pnl_pct'] or 0 for t in wins]
    loss_pnls = [t['pnl_pct'] or 0 for t in losses]

    nb_wins = len(wins)
    nb_losses = len(losses)
    nb_conclus = len(trades_conclus)
    win_rate = nb_wins / nb_conclus if nb_conclus > 0 else 0

    gains_bruts = sum(p for p in pnls if p > 0)
    pertes_brutes = abs(sum(p for p in pnls if p < 0))
    profit_factor = gains_bruts / pertes_brutes if pertes_brutes > 0 else float('inf')

    avg_win = sum(win_pnls) / nb_wins if nb_wins > 0 else 0
    avg_loss = abs(sum(loss_pnls) / nb_losses) if nb_losses > 0 else 0
    expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

    # --- Ratios de risque ---
    mean_return = sum(pnls) / len(pnls)
    std_return = math.sqrt(sum((p - mean_return) ** 2 for p in pnls) / len(pnls)) if len(pnls) > 1 else 0

    # Sharpe Ratio (annualise: ~250 jours trading, ~4 trades/jour = 1000 trades/an)
    trades_par_an = len(pnls) / max(jours, 1) * 250
    sharpe = (mean_return / std_return * math.sqrt(trades_par_an)) if std_return > 0 else 0

    # Sortino Ratio (only downside volatility)
    downside_returns = [p for p in pnls if p < 0]
    downside_std = math.sqrt(sum(p ** 2 for p in downside_returns) / len(pnls)) if downside_returns else 0
    sortino = (mean_return / downside_std * math.sqrt(trades_par_an)) if downside_std > 0 else 0

    # Equity curve et max drawdown
    equity_curve = []
    equity = 0
    peak = 0
    max_dd = 0
    max_dd_pct = 0
    dd_start = None
    max_dd_duration = 0
    current_dd_start = None
    ulcer_squares = []

    for t in trades_conclus:
        pnl = t['pnl_pct'] or 0
        equity += pnl
        equity_curve.append({
            'date': t['date'],
            'symbole': t['symbole'],
            'pnl': round(pnl, 2),
            'equity': round(equity, 2)
        })

        if equity > peak:
            peak = equity
            current_dd_start = None

        dd = peak - equity
        dd_pct = (dd / peak * 100) if peak > 0 else 0
        ulcer_squares.append(dd_pct ** 2)

        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd_pct

        if dd > 0 and current_dd_start is None:
            current_dd_start = len(equity_curve)

    # Ulcer Index
    ulcer_index = math.sqrt(sum(ulcer_squares) / len(ulcer_squares)) if ulcer_squares else 0

    # Calmar Ratio = return / max_drawdown
    total_return = equity
    calmar = total_return / max_dd if max_dd > 0 else float('inf') if total_return > 0 else 0

    # --- Kelly Criterion ---
    # f* = (p * b - q) / b  ou p=win_rate, q=1-p, b=avg_win/avg_loss
    if avg_loss > 0 and avg_win > 0:
        b = avg_win / avg_loss
        kelly = (win_rate * b - (1 - win_rate)) / b
        kelly_half = kelly / 2  # Kelly/2 = plus conservateur
    else:
        kelly = 0
        kelly_half = 0

    # --- Streaks ---
    max_consec_wins = 0
    max_consec_losses = 0
    current_wins = 0
    current_losses = 0
    current_streak_type = None
    current_streak_count = 0

    for t in trades_conclus:
        is_win = t['resultat'] in ('TP1', 'TP2', 'WIN_FORCE')
        if is_win:
            current_wins += 1
            current_losses = 0
            current_streak_type = 'WIN'
            current_streak_count = current_wins
        else:
            current_losses += 1
            current_wins = 0
            current_streak_type = 'LOSS'
            current_streak_count = current_losses
        max_consec_wins = max(max_consec_wins, current_wins)
        max_consec_losses = max(max_consec_losses, current_losses)

    # --- Breakdown par categorie ---
    par_categorie = defaultdict(lambda: {'nb': 0, 'wins': 0, 'pnl': 0})
    for t in trades_conclus:
        cat = t['categorie_actif'] or 'INCONNU'
        par_categorie[cat]['nb'] += 1
        if t['resultat'] in ('TP1', 'TP2', 'WIN_FORCE'):
            par_categorie[cat]['wins'] += 1
        par_categorie[cat]['pnl'] += t['pnl_pct'] or 0

    for cat in par_categorie:
        d = par_categorie[cat]
        d['win_rate'] = round(d['wins'] / d['nb'] * 100, 1) if d['nb'] > 0 else 0
        d['pnl'] = round(d['pnl'], 2)

    # --- Breakdown par direction ---
    par_direction = defaultdict(lambda: {'nb': 0, 'wins': 0, 'pnl': 0})
    for t in trades_conclus:
        direction = t['direction'] or 'LONG'
        par_direction[direction]['nb'] += 1
        if t['resultat'] in ('TP1', 'TP2', 'WIN_FORCE'):
            par_direction[direction]['wins'] += 1
        par_direction[direction]['pnl'] += t['pnl_pct'] or 0

    for d in par_direction:
        data = par_direction[d]
        data['win_rate'] = round(data['wins'] / data['nb'] * 100, 1) if data['nb'] > 0 else 0
        data['pnl'] = round(data['pnl'], 2)

    # --- Breakdown par resultat ---
    par_resultat = defaultdict(int)
    for t in avec_resultat:
        par_resultat[t['resultat']] += 1

    return {
        'jours': jours,
        'date_debut': date_debut,
        'basiques': {
            'total_conclus': nb_conclus,
            'wins': nb_wins,
            'losses': nb_losses,
            'win_rate': round(win_rate * 100, 1),
            'profit_factor': round(profit_factor, 2) if profit_factor != float('inf') else 'inf',
            'expectancy': round(expectancy, 3),
            'avg_win': round(avg_win, 2),
            'avg_loss': round(avg_loss, 2),
            'avg_win_loss_ratio': round(avg_win / avg_loss, 2) if avg_loss > 0 else 'inf',
            'pnl_total': round(sum(pnls), 2),
            'pnl_moyen': round(mean_return, 3),
        },
        'risque': {
            'sharpe_ratio': round(sharpe, 2),
            'sortino_ratio': round(sortino, 2),
            'calmar_ratio': round(calmar, 2) if calmar != float('inf') else 'inf',
            'max_drawdown_pct': round(max_dd, 2),
            'max_drawdown_pct_from_peak': round(max_dd_pct, 2),
            'ulcer_index': round(ulcer_index, 2),
            'volatilite_returns': round(std_return, 3),
        },
        'sizing': {
            'kelly_criterion': round(kelly, 3),
            'kelly_half': round(kelly_half, 3),
            'note': 'Kelly/2 recommande pour position sizing conservateur',
        },
        'streaks': {
            'max_consecutive_wins': max_consec_wins,
            'max_consecutive_losses': max_consec_losses,
            'current_streak': f"{current_streak_type} x{current_streak_count}" if current_streak_type else 'N/A',
        },
        'par_resultat': dict(par_resultat),
        'par_categorie': dict(par_categorie),
        'par_direction': dict(par_direction),
        'completude': completude,
        'equity_curve': equity_curve,
    }


def afficher_rapport(kpis):
    """Affiche un rapport console formate."""
    if 'erreur' in kpis:
        print(f"\n{'='*60}")
        print(f"  RAPPORT KPI - {kpis.get('jours', '?')} jours")
        print(f"{'='*60}")
        print(f"  {kpis['erreur']}")
        if 'completude' in kpis:
            c = kpis['completude']
            print(f"  Signaux emis: {c['signaux_emis']}")
            print(f"  Sans resultat: {c['sans_resultat']}")
        return

    b = kpis['basiques']
    r = kpis['risque']
    s = kpis['sizing']
    st = kpis['streaks']
    c = kpis['completude']

    print(f"\n{'='*60}")
    print(f"  RAPPORT KPI QUANTITATIF - {kpis['jours']} jours")
    print(f"  Depuis: {kpis['date_debut']}")
    print(f"{'='*60}")

    print(f"\n--- COMPLETUDE DU TRACKING ---")
    print(f"  Signaux emis:     {c['signaux_emis']}")
    print(f"  Avec resultat:    {c['avec_resultat']}")
    print(f"  Sans resultat:    {c['sans_resultat']} {'(BIAIS!)' if c['sans_resultat'] > 0 else '(OK)'}")
    print(f"  Expires:          {c['expires']}")
    print(f"  Taux completion:  {c['taux_completion']}%")

    print(f"\n--- METRIQUES BASIQUES ---")
    print(f"  Trades conclus:   {b['total_conclus']} ({b['wins']}W / {b['losses']}L)")
    print(f"  Win rate:         {b['win_rate']}%")
    print(f"  Profit factor:    {b['profit_factor']}")
    print(f"  Expectancy:       {b['expectancy']}% par trade")
    print(f"  Avg win:          +{b['avg_win']}%")
    print(f"  Avg loss:         -{b['avg_loss']}%")
    print(f"  Win/Loss ratio:   {b['avg_win_loss_ratio']}")
    print(f"  PnL total:        {b['pnl_total']}%")

    print(f"\n--- METRIQUES DE RISQUE ---")
    print(f"  Sharpe ratio:     {r['sharpe_ratio']}")
    print(f"  Sortino ratio:    {r['sortino_ratio']}")
    print(f"  Calmar ratio:     {r['calmar_ratio']}")
    print(f"  Max drawdown:     {r['max_drawdown_pct']}%")
    print(f"  Ulcer index:      {r['ulcer_index']}")
    print(f"  Volatilite:       {r['volatilite_returns']}%")

    print(f"\n--- POSITION SIZING (KELLY) ---")
    print(f"  Kelly criterion:  {s['kelly_criterion']} ({s['kelly_criterion']*100:.1f}% du capital)")
    print(f"  Kelly/2:          {s['kelly_half']} ({s['kelly_half']*100:.1f}% du capital)")

    print(f"\n--- STREAKS ---")
    print(f"  Max wins consec:  {st['max_consecutive_wins']}")
    print(f"  Max losses consec:{st['max_consecutive_losses']}")
    print(f"  Streak actuel:    {st['current_streak']}")

    print(f"\n--- BREAKDOWN PAR RESULTAT ---")
    for res, nb in sorted(kpis['par_resultat'].items(), key=lambda x: -x[1]):
        print(f"  {res:15s}   {nb}")

    print(f"\n--- BREAKDOWN PAR CATEGORIE ---")
    for cat, data in sorted(kpis['par_categorie'].items(), key=lambda x: -x[1]['pnl']):
        print(f"  {cat:15s}   {data['nb']} trades, WR={data['win_rate']}%, PnL={data['pnl']}%")

    print(f"\n--- BREAKDOWN PAR DIRECTION ---")
    for dir_, data in kpis['par_direction'].items():
        print(f"  {dir_:15s}   {data['nb']} trades, WR={data['win_rate']}%, PnL={data['pnl']}%")

    print(f"\n{'='*60}")


if __name__ == '__main__':
    jours_arg = 90
    output_json = False

    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == '--jours' and i < len(sys.argv) - 1:
            jours_arg = int(sys.argv[i + 1])
        if arg == '--json':
            output_json = True

    kpis = calculer_kpis_avances(jours=jours_arg)

    if output_json:
        # Remove equity_curve for JSON output (too verbose)
        kpis_clean = {k: v for k, v in kpis.items() if k != 'equity_curve'}
        print(json.dumps(kpis_clean, indent=2, default=str))
    else:
        afficher_rapport(kpis)
