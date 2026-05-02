#!/usr/bin/env python3
"""
Script de démarrage rapide pour l'API
Exécuter: python run_api.py
"""

import os
import sys
import subprocess
from pathlib import Path

# Ajouter src au path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root / "src"))

def main():
    print("=" * 60)
    print("Oliveraies Anomalies API - Demarrage")
    print("=" * 60)
    print()
    
    # Vérifier Python
    print(f"Python {sys.version.split()[0]} OK")
    
    # Vérifier packages
    try:
        import fastapi
        import uvicorn
        import numpy
        print("FastAPI, Uvicorn, NumPy - OK")
    except ImportError as e:
        print(f"Erreur import: {e}")
        print("  Executer: pip install -r requirements.txt")
        return 1
    
    # Vérifier données
    data_files = [
        project_root / "Oliviers" / "parcelles_OlivierExtensif.json",
        project_root / "Oliviers" / "parcellesOliviersIntensifs.json"
    ]
    
    data_loaded = sum(1 for f in data_files if f.exists())
    print(f"Donnees oliveraies: {data_loaded}/2 fichiers")
    
    print()
    print("-" * 60)
    print("Demarrage du serveur...")
    print("-" * 60)
    print()
    print("API disponible sur:")
    print("   - Dashboard:     http://localhost:8000/dashboard")
    print("   - API Docs:      http://localhost:8000/docs")
    print("   - Health:        http://localhost:8000/health")
    print()
    print("Appuyez sur Ctrl+C pour arreter")
    print()
    
    # Démarrer uvicorn
    os.chdir(str(project_root))
    # Passer les variables d'env GEE au sous-process uvicorn
    env = os.environ.copy()
    if "GEE_PROJECT" not in env and os.environ.get("GEE_PROJECT"):
        env["GEE_PROJECT"] = os.environ["GEE_PROJECT"]
    subprocess.run([
        sys.executable, "-m", "uvicorn",
        "src.main:app",
        "--reload",
        "--host", "0.0.0.0",
        "--port", "8000",
        "--log-level", "info"
    ], env=env)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
