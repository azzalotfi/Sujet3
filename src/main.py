"""
FastAPI Application - Oliveraies Anomalies Detection
"""
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List
import numpy as np

# Charger les variables d'environnement depuis .env
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

# Setup path
sys.path.insert(0, str(Path(__file__).parent))

from models import DiagnosticRequest, DiagnosticResponse, SystemType
from services.anomaly import detector
from services.satellite import fetch_ndvi_inputs
from services.weather import fetch_weather_context

# ============================================================================
# APP SETUP
# ============================================================================

app = FastAPI(
    title="Oliveraies Anomalies API",
    description="Détection d'anomalies NDVI pour oliveraies",
    version="1.0.0"
)

# Paths
DATA_DIR = Path(__file__).parent.parent / "Oliviers"
DASHBOARD_DIR = Path(__file__).parent / "dashboard"


def _infer_default_systeme(filename: str) -> str:
    """Infer conduite system from source filename when absent in payload."""
    if "intensif" in filename.lower() and "hyper" not in filename.lower():
        return "intensif"
    return "extensif"


def _normalize_polygon(raw_parcel: Dict[str, Any]) -> List[Dict[str, float]]:
    """Return polygon coordinates using either `polygone` or `coordinates` keys."""
    points = raw_parcel.get("polygone") or raw_parcel.get("coordinates") or []
    normalized = []
    for point in points:
        if isinstance(point, dict):
            lat = point.get("lat")
            lng = point.get("lng")
            if lat is not None and lng is not None:
                normalized.append({"lat": float(lat), "lng": float(lng)})
    return normalized


def _normalize_parcel(raw_parcel: Dict[str, Any], default_systeme: str) -> Dict[str, Any]:
    """Normalize heterogeneous parcel schemas into API internal format."""
    polygon = _normalize_polygon(raw_parcel)
    return {
        "id": raw_parcel.get("id") or raw_parcel.get("parcel_id"),
        "nom": raw_parcel.get("nom") or raw_parcel.get("name") or "Parcelle sans nom",
        "polygone": polygon,
        "systeme": raw_parcel.get("systeme") or default_systeme,
        "area_ha": float(raw_parcel.get("area_ha") or 0.0),
        "gouvernorat": raw_parcel.get("gouvernorat"),
        "proprietaire": raw_parcel.get("proprietaire") or raw_parcel.get("owner"),
        "source": raw_parcel,
    }

# Load data
PARCELLES_DATA = {}
try:
    for fname in ["parcelles_OlivierExtensif.json", "parcellesOliviersIntensifs.json"]:
        fpath = DATA_DIR / fname
        if fpath.exists():
            with open(fpath) as f:
                data = json.load(f)
                default_systeme = _infer_default_systeme(fname)
                for parcel in data.get("parcels", []):
                    normalized = _normalize_parcel(parcel, default_systeme)
                    if normalized["id"]:
                        PARCELLES_DATA[normalized["id"]] = normalized
    print(f"Loaded {len(PARCELLES_DATA)} parcelles")
except Exception as e:
    print(f"Error loading data: {e}")

# ============================================================================
# HEALTH & ROOT
# ============================================================================

@app.get("/health")
def health():
    """Health check avec statut des sources de données"""
    from services.gee import gee_status
    return {
        "status": "operational",
        "timestamp": datetime.now().isoformat(),
        "parcelles": len(PARCELLES_DATA),
        "data_sources": {
            "open_meteo": "active",
            "chirps_nasa_power": "active",
            "modis_lst_nasa_power": "active",
            "sentinel_hub": "active-with-fallback",
            "gee": gee_status(),
        }
    }

@app.get("/")
def root():
    """Root endpoint"""
    return {
        "app": "Oliveraies Anomalies API",
        "version": "1.0.0",
        "docs": "/docs",
        "dashboard": "/dashboard"
    }

# ============================================================================
# DIAGNOSTIC
# ============================================================================

@app.post("/api/diagnostic-anomalie", response_model=DiagnosticResponse)
def diagnostic_anomalie(request: DiagnosticRequest):
    """Diagnostic complet d'anomalie"""
    try:
        # Parse date
        try:
            date_target = datetime.strptime(request.date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD")
        
        # Generate NDVI data
        dates = [(date_target - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(20, -1, -1)]
        ndvi_inputs = fetch_ndvi_inputs(
            oliveraie=request.oliveraie.model_dump(),
            date_target=date_target,
            systeme=request.oliveraie.systeme,
            window_days=21,
            historique_years=5,
        )
        ndvi_observe = ndvi_inputs.ndvi_recent
        ndvi_historique = ndvi_inputs.ndvi_historique
        meteo = fetch_weather_context(date_target, request.oliveraie.model_dump(), window_days=21)
        
        # Diagnostiquer
        result = detector.diagnostiquer(
            ndvi_recent=ndvi_observe,
            ndvi_historique=ndvi_historique,
            date_cible=date_target,
            systeme=request.oliveraie.systeme,
            meteo=meteo,
            oliveraie=request.oliveraie.model_dump(),
        )
        
        return DiagnosticResponse(
            statut=result["statut"],
            anomaly_score=result["anomaly_score"],
            ndvi_observe=result["ndvi_observe"],
            ndvi_attendu=result["ndvi_attendu"],
            dates=dates,
            explication=result["explication"],
            recommandation=result["recommandation"],
            metadata={
                "baseline": result["baseline"],
                "ecart_pct": result.get("ecart_pct"),
                "seuils": result.get("seuils", {}),
                "ndvi_source_recent": ndvi_inputs.source_recent,
                "ndvi_source_historique": ndvi_inputs.source_historique,
                "weather_context": result.get("weather_context", {}),
                "timestamp": datetime.now().isoformat()
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error: {str(e)}")

@app.get("/api/parcelles")
def list_parcelles():
    """List all parcelles"""
    return {
        "total": len(PARCELLES_DATA),
        "parcelles": [
            {
                "id": p.get("id"),
                "nom": p.get("nom"),
                "area_ha": p.get("area_ha"),
                "systeme": p.get("systeme"),
                "polygone": p.get("polygone", []),
            }
            for p in PARCELLES_DATA.values()
        ]
    }

@app.get("/api/sante/{parcel_id}")
def sante_parcelle(parcel_id: str):
    """Santé d'une parcelle"""
    if parcel_id not in PARCELLES_DATA:
        raise HTTPException(404, f"Parcelle {parcel_id} not found")
    
    parcel = PARCELLES_DATA[parcel_id]
    systeme = parcel.get("systeme", "extensif")
    date_target = datetime.now()
    ndvi_inputs = fetch_ndvi_inputs(
        oliveraie=parcel,
        date_target=date_target,
        systeme=systeme,
        window_days=21,
        historique_years=5,
    )
    ndvi_observe = ndvi_inputs.ndvi_recent
    ndvi_historique = ndvi_inputs.ndvi_historique
    meteo = fetch_weather_context(date_target, parcel, window_days=21)
    
    result = detector.diagnostiquer(
        ndvi_recent=ndvi_observe,
        ndvi_historique=ndvi_historique,
        date_cible=date_target,
        systeme=systeme,
        meteo=meteo,
        oliveraie=parcel,
    )
    
    return {
        "parcel_id": parcel_id,
        "nom": parcel.get("nom"),
        "statut": result["statut"],
        "anomaly_score": result["anomaly_score"],
        "ndvi_recent": ndvi_observe[-1],
        "updated_at": datetime.now().isoformat()
    }

# ============================================================================
# FRONTEND
# ============================================================================

@app.get("/dashboard")
def dashboard():
    """Dashboard Leaflet"""
    dashboard_path = DASHBOARD_DIR / "index.html"
    if dashboard_path.exists():
        return FileResponse(dashboard_path)
    raise HTTPException(404, "Dashboard not found")

# Mount static
if DASHBOARD_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")

# ============================================================================
# HELPERS
# ============================================================================

def generate_ndvi(date_target: datetime, systeme: str) -> list:
    """Generate realistic NDVI"""
    base = {"extensif": 0.42, "intensif": 0.55, "hyper-intensif": 0.68}.get(systeme, 0.45)
    jour = date_target.timetuple().tm_yday
    saisonnier = 0.15 * np.sin(2 * np.pi * jour / 365)
    bruit = np.random.normal(0, 0.03, 21)
    ndvi = [base + saisonnier + b for b in bruit]
    return [max(0, min(1, v)) for v in ndvi]

def generate_historique(date_target: datetime, systeme: str, years: int = 5) -> list:
    """Generate NDVI historique"""
    base = {"extensif": 0.42, "intensif": 0.55, "hyper-intensif": 0.68}.get(systeme, 0.45)
    historique = []
    for _ in range(years):
        annee = []
        for jour in range(365):
            saisonnier = 0.15 * np.sin(2 * np.pi * jour / 365)
            bruit = np.random.normal(0, 0.02)
            ndvi_val = base + saisonnier + bruit
            annee.append(max(0, min(1, ndvi_val)))
        historique.append(annee)
    return historique

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
