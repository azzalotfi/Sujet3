"""
Pydantic models for API validation and documentation
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum


class SystemType(str, Enum):
    """Système de conduite des oliveraies"""
    EXTENSIF = "extensif"
    INTENSIF = "intensif"
    HYPER_INTENSIF = "hyper-intensif"


class Coordinate(BaseModel):
    """Coordonnée GPS"""
    lat: float = Field(..., description="Latitude WGS84")
    lng: float = Field(..., description="Longitude WGS84")


class Oliveraie(BaseModel):
    """Modèle représentant une parcelle d'oliveraie"""
    id: str = Field(..., description="Identifiant unique")
    nom: Optional[str] = None
    polygone: List[Coordinate] = Field(..., description="Polygon GeoJSON")
    systeme: SystemType = Field(default=SystemType.EXTENSIF)
    area_ha: float = Field(..., description="Surface en hectares")
    gouvernorat: Optional[str] = None
    proprietaire: Optional[str] = None


class DiagnosticRequest(BaseModel):
    """Requête pour diagnostic d'anomalie"""
    oliveraie: Oliveraie = Field(..., description="Parcelle à analyser")
    date: str = Field(..., description="Date au format YYYY-MM-DD")


class DiagnosticResponse(BaseModel):
    """Réponse diagnostic d'anomalie"""
    statut: str = Field(..., description="Statut: vert/orange/rouge")
    anomaly_score: float = Field(..., description="Score d'anomalie 0-100")
    ndvi_observe: List[float] = Field(..., description="NDVI mesuré (3 semaines)")
    ndvi_attendu: List[float] = Field(..., description="NDVI attendu baseline")
    dates: List[str] = Field(..., description="Dates correspondantes")
    explication: str = Field(..., description="Texte explicatif")
    recommandation: str = Field(..., description="Action recommandée")
    metadata: Optional[Dict[str, Any]] = None


class ParcelleSante(BaseModel):
    """État de santé d'une parcelle"""
    parcel_id: str
    nom: str
    statut: str  # vert, orange, rouge
    anomaly_score: float
    derniere_mise_a_jour: datetime
    ndvi_recent: float
