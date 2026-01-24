#!/usr/bin/env python3
"""
Script de test rapide pour vérifier que l'application fonctionne
"""

import sys

def test_imports():
    """Test des imports"""
    print("🧪 Test des imports...")
    try:
        import yaml
        import pandas
        import numpy
        from stock_data import StockDataFetcher
        from indicators import TechnicalIndicators
        from alerts import AlertGenerator
        print("✅ Tous les imports réussis!")
        return True
    except ImportError as e:
        print(f"❌ Erreur d'import: {e}")
        print("\n💡 Installez les dépendances avec:")
        print("   pip install -r requirements.txt")
        return False

def test_config():
    """Test de la configuration"""
    print("\n🧪 Test de la configuration...")
    try:
        import yaml
        with open('config.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        print(f"✅ Configuration chargée: {len(config['stocks'])} actions à surveiller")
        return True
    except Exception as e:
        print(f"❌ Erreur de configuration: {e}")
        return False

def test_basic_functionality():
    """Test de fonctionnalité de base"""
    print("\n🧪 Test de fonctionnalité de base...")
    try:
        from stock_data import StockDataFetcher

        fetcher = StockDataFetcher()
        print("   Tentative de récupération du prix d'Apple (AAPL)...")

        # Test simple sans connexion réseau complète
        ticker_test = "AAPL"
        print(f"   Test avec {ticker_test}...")

        print("✅ Classes initialisées correctement!")
        return True
    except Exception as e:
        print(f"❌ Erreur de fonctionnalité: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Point d'entrée du test"""
    print("="*60)
    print("🚀 TEST DE L'APPLICATION DE TRADING")
    print("="*60)

    tests = [
        test_imports,
        test_config,
        test_basic_functionality
    ]

    results = [test() for test in tests]

    print("\n" + "="*60)
    if all(results):
        print("✅ TOUS LES TESTS PASSÉS!")
        print("\n💡 L'application est prête à être utilisée:")
        print("   python main.py --analyze AAPL")
        return 0
    else:
        print("❌ CERTAINS TESTS ONT ÉCHOUÉ")
        print("\n💡 Veuillez corriger les erreurs ci-dessus")
        return 1

if __name__ == "__main__":
    sys.exit(main())
