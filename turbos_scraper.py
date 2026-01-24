"""
Module de scraping des Turbos Société Générale
FONCTION 2: Sélection automatique de turbos call/put
"""

import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import yaml
import time
import re


class TurboData:
    """Représente un turbo"""
    def __init__(self, data: Dict):
        self.isin = data.get('isin', '')
        self.name = data.get('name', '')
        self.underlying = data.get('underlying', '')
        self.type = data.get('type', '')  # 'CALL' ou 'PUT'
        self.leverage = data.get('leverage', 0)
        self.barrier = data.get('barrier', 0.0)
        self.strike = data.get('strike', 0.0)
        self.expiry = data.get('expiry', '')
        self.bid = data.get('bid', 0.0)
        self.ask = data.get('ask', 0.0)
        self.spread = data.get('spread', 0.0)
        self.current_price = data.get('current_price', 0.0)

    def calculate_barrier_distance(self, spot_price: float) -> float:
        """
        Calcule la distance à la barrière en %

        Args:
            spot_price: Prix actuel du sous-jacent

        Returns:
            Distance en pourcentage (positif = sécurité)
        """
        if self.type == 'CALL':
            # Pour un call, la barrière est en dessous
            return ((spot_price - self.barrier) / spot_price) * 100
        else:
            # Pour un put, la barrière est au-dessus
            return ((self.barrier - spot_price) / spot_price) * 100

    def days_to_expiry(self) -> int:
        """Calcule le nombre de jours avant échéance"""
        try:
            expiry_date = datetime.strptime(self.expiry, "%Y-%m-%d")
            return (expiry_date - datetime.now()).days
        except:
            return 0

    def __repr__(self):
        return f"Turbo {self.type} {self.underlying} - ISIN: {self.isin} - Levier: {self.leverage}x"


class TurbosScraper:
    """Scrape et sélectionne les turbos Société Générale"""

    def __init__(self, config_path: str = "config.yaml"):
        """Initialise le scraper"""
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        self.config = config['turbos']
        self.enabled = self.config.get('enabled', True)
        self.base_url = self.config.get('base_url', '')
        self.underlying_mapping = self.config.get('underlying_mapping', {})
        self.cache: Dict[str, List[TurboData]] = {}
        self.cache_timestamp: Dict[str, datetime] = {}

    def find_turbos_for_asset(self, symbol: str, signal_type: str, spot_price: float) -> List[TurboData]:
        """
        Trouve les turbos disponibles pour un actif

        Args:
            symbol: Symbole de l'actif (ex: MC.PA)
            signal_type: Type de signal ('BUY' ou 'SELL')
            spot_price: Prix actuel du sous-jacent

        Returns:
            Liste de turbos disponibles
        """
        if not self.enabled:
            return []

        # Vérifier si le symbole est supporté
        if symbol not in self.underlying_mapping:
            return []

        underlying_name = self.underlying_mapping[symbol]

        # Déterminer le type de turbo recherché
        turbo_type = 'CALL' if signal_type == 'BUY' else 'PUT'

        # Vérifier le cache
        cache_key = f"{symbol}_{turbo_type}"
        if self._is_cache_valid(cache_key):
            turbos = self.cache.get(cache_key, [])
        else:
            # Scraper les turbos
            turbos = self._scrape_turbos(underlying_name, turbo_type)
            self.cache[cache_key] = turbos
            self.cache_timestamp[cache_key] = datetime.now()

        # Filtrer selon les critères
        filtered_turbos = self._filter_turbos(turbos, spot_price)

        return filtered_turbos

    def select_best_turbo(self, turbos: List[TurboData], spot_price: float) -> Optional[TurboData]:
        """
        Sélectionne le meilleur turbo parmi une liste

        Args:
            turbos: Liste de turbos disponibles
            spot_price: Prix actuel du sous-jacent

        Returns:
            Meilleur turbo ou None
        """
        if not turbos:
            return None

        # Scoring des turbos
        scored_turbos = []
        for turbo in turbos:
            score = self._score_turbo(turbo, spot_price)
            scored_turbos.append((score, turbo))

        # Trier par score décroissant
        scored_turbos.sort(key=lambda x: x[0], reverse=True)

        return scored_turbos[0][1] if scored_turbos else None

    def _is_cache_valid(self, cache_key: str) -> bool:
        """Vérifie si le cache est encore valide"""
        if cache_key not in self.cache_timestamp:
            return False

        cache_duration = self.config.get('cache', {}).get('duration_minutes', 60)
        age = datetime.now() - self.cache_timestamp[cache_key]

        return age.total_seconds() < (cache_duration * 60)

    def _scrape_turbos(self, underlying: str, turbo_type: str) -> List[TurboData]:
        """
        Scrape les turbos du site SG (VERSION SIMULÉE)

        NOTE: Cette fonction simule le scraping car l'accès réel au site SG
        nécessiterait de connaître la structure exacte du site.

        Args:
            underlying: Nom du sous-jacent
            turbo_type: 'CALL' ou 'PUT'

        Returns:
            Liste de turbos (simulés pour le moment)
        """
        # SIMULATION: En production, cette fonction ferait un vrai scraping
        # Pour l'instant, on retourne des turbos simulés pour démonstration

        print(f"  ℹ️  Recherche de turbos {turbo_type} sur {underlying}...")
        print(f"  ⚠️  Mode simulation activé (scraping réel à implémenter)")

        # Turbos simulés pour démonstration
        simulated_turbos = []

        # Générer quelques turbos simulés
        leverages = [5, 8, 10, 12, 15]
        for i, lev in enumerate(leverages):
            turbo = TurboData({
                'isin': f'FR001400{i:04d}',
                'name': f'{underlying} Turbo {turbo_type} {lev}X',
                'underlying': underlying,
                'type': turbo_type,
                'leverage': lev,
                'barrier': 0.0,  # Sera calculé plus tard
                'strike': 0.0,
                'expiry': (datetime.now() + timedelta(days=90 + i*30)).strftime("%Y-%m-%d"),
                'bid': 1.50 - (i * 0.1),
                'ask': 1.55 - (i * 0.1),
                'spread': 0.05,
                'current_price': 1.52 - (i * 0.1)
            })
            simulated_turbos.append(turbo)

        return simulated_turbos

    def _filter_turbos(self, turbos: List[TurboData], spot_price: float) -> List[TurboData]:
        """
        Filtre les turbos selon les critères de sélection

        Args:
            turbos: Liste de turbos
            spot_price: Prix actuel du sous-jacent

        Returns:
            Liste de turbos filtrés
        """
        criteria = self.config['selection']

        filtered = []
        for turbo in turbos:
            # Filtre levier
            if not (criteria['leverage_min'] <= turbo.leverage <= criteria['leverage_max']):
                continue

            # Filtre échéance
            if turbo.days_to_expiry() < criteria['min_days_to_expiry']:
                continue

            # Filtre spread
            if turbo.spread > 0:
                spread_pct = (turbo.spread / turbo.current_price) * 100
                if spread_pct > criteria['max_spread_percent']:
                    continue

            # TODO: Filtre distance barrière (nécessite de calculer la barrière)

            filtered.append(turbo)

        return filtered

    def _score_turbo(self, turbo: TurboData, spot_price: float) -> float:
        """
        Calcule un score pour un turbo

        Args:
            turbo: Turbo à scorer
            spot_price: Prix actuel du sous-jacent

        Returns:
            Score (plus élevé = meilleur)
        """
        score = 0.0
        criteria = self.config['selection']

        # Score levier (bonus si proche de l'optimal)
        optimal_leverage = criteria['leverage_optimal']
        leverage_diff = abs(turbo.leverage - optimal_leverage)
        leverage_score = max(0, 50 - leverage_diff * 5)
        score += leverage_score

        # Score échéance (plus longue = meilleur, mais pas trop)
        days = turbo.days_to_expiry()
        if 60 <= days <= 180:
            expiry_score = 30
        elif 30 <= days < 60 or 180 < days <= 365:
            expiry_score = 20
        else:
            expiry_score = 10
        score += expiry_score

        # Score spread (plus faible = meilleur)
        if turbo.current_price > 0:
            spread_pct = (turbo.spread / turbo.current_price) * 100
            spread_score = max(0, 20 - spread_pct * 40)
            score += spread_score

        return score

    def format_turbo_recommendation(self, turbo: TurboData, spot_price: float, expected_gain_pct: float = 1.0) -> str:
        """
        Formate une recommandation de turbo pour affichage

        Args:
            turbo: Turbo recommandé
            spot_price: Prix actuel du sous-jacent
            expected_gain_pct: Gain attendu sur le sous-jacent (%)

        Returns:
            Texte formaté
        """
        leveraged_gain = expected_gain_pct * turbo.leverage
        days_left = turbo.days_to_expiry()

        text = f"""
  🚀 TURBO RECOMMANDÉ:
     Type: {turbo.type} ({'Haussier' if turbo.type == 'CALL' else 'Baissier'})
     ISIN: {turbo.isin}
     Nom: {turbo.name}
     Levier: {turbo.leverage}x
     Prix: {turbo.current_price:.2f}€ (Bid: {turbo.bid:.2f}€ / Ask: {turbo.ask:.2f}€)
     Spread: {turbo.spread:.2f}€
     Échéance: {turbo.expiry} ({days_left} jours)

     💡 Si {turbo.underlying} fait {expected_gain_pct:+.1f}%
        → Turbo fait environ {leveraged_gain:+.1f}% (effet levier {turbo.leverage}x)

     ⚠️  ATTENTION: Produit à effet de levier - Risque de perte totale
"""
        return text
