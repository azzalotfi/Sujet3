"""
Tests unitaires pour l'API Oliveraies Anomalies
Exécuter avec: pytest tests/test_api.py -v
"""

import pytest
import json
from fastapi.testclient import TestClient
import sys
import os

# Ajouter src au path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from main import app

client = TestClient(app)


class TestHealth:
    """Tests endpoint health"""
    
    def test_health_check(self):
        """Vérifier que l'API répond"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "operational"
        assert "timestamp" in data
    
    def test_root_endpoint(self):
        """Vérifier endpoint racine"""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "app" in data
        assert "endpoints" in data


class TestDiagnostic:
    """Tests diagnostic endpoint"""
    
    def test_diagnostic_valid_request(self):
        """Tester diagnostic avec requête valide"""
        payload = {
            "oliveraie": {
                "id": "test_001",
                "nom": "Test Parcelle",
                "polygone": [{"lat": 35.29, "lng": 10.61}],
                "systeme": "extensif",
                "area_ha": 50
            },
            "date": "2026-05-02"
        }
        
        response = client.post("/api/diagnostic-anomalie", json=payload)
        assert response.status_code == 200
        data = response.json()
        
        # Vérifier structure réponse
        assert "statut" in data
        assert data["statut"] in ["vert", "orange", "rouge"]
        assert "anomaly_score" in data
        assert 0 <= data["anomaly_score"] <= 100
        assert "ndvi_observe" in data
        assert "ndvi_attendu" in data
        assert "explication" in data
        assert "recommandation" in data
    
    def test_diagnostic_invalid_date(self):
        """Tester avec date invalide"""
        payload = {
            "oliveraie": {
                "id": "test_001",
                "nom": "Test",
                "polygone": [{"lat": 35.29, "lng": 10.61}],
                "systeme": "extensif",
                "area_ha": 50
            },
            "date": "invalid-date"
        }
        
        response = client.post("/api/diagnostic-anomalie", json=payload)
        assert response.status_code == 400
    
    def test_diagnostic_intensif_system(self):
        """Tester avec système intensif"""
        payload = {
            "oliveraie": {
                "id": "test_intensif",
                "nom": "Parcelle Intensive",
                "polygone": [{"lat": 36.44, "lng": 10.01}],
                "systeme": "intensif",
                "area_ha": 74
            },
            "date": "2026-05-02"
        }
        
        response = client.post("/api/diagnostic-anomalie", json=payload)
        assert response.status_code == 200
        data = response.json()
        
        # Vérifier que c'est cohérent
        assert "statut" in data
        assert len(data["ndvi_observe"]) == 21
        assert len(data["ndvi_attendu"]) == 21


class TestParcelles:
    """Tests endpoint parcelles"""
    
    def test_list_parcelles(self):
        """Lister toutes les parcelles"""
        response = client.get("/api/parcelles")
        assert response.status_code == 200
        data = response.json()
        assert "total" in data
        assert "parcelles" in data
        assert isinstance(data["parcelles"], list)
    
    def test_parcelle_sante(self):
        """Tester endpoint santé parcelle"""
        # D'abord lister pour obtenir un ID
        response = client.get("/api/parcelles")
        parcelles = response.json()["parcelles"]
        
        if parcelles:
            parcel_id = parcelles[0]["id"]
            response = client.get(f"/api/sante/{parcel_id}")
            assert response.status_code == 200
            data = response.json()
            assert data["parcel_id"] == parcel_id
            assert "statut" in data
            assert "anomaly_score" in data


class TestDashboard:
    """Tests dashboard"""
    
    def test_dashboard_available(self):
        """Vérifier que le dashboard est accessible"""
        response = client.get("/dashboard")
        # 200 si fichier existe, 404 sinon
        assert response.status_code in [200, 404]


class TestAnomalyDetector:
    """Tests module détection anomalies"""
    
    def test_anomaly_detector_baseline(self):
        """Tester calcul baseline saisonnier"""
        from datetime import datetime
        from services.anomaly import detector
        
        # Créer données historiques test
        historique = []
        for _ in range(5):
            annee_data = [0.45 + 0.15 * (i/365) for i in range(365)]
            historique.append(annee_data)
        
        date_target = datetime(2026, 5, 2)
        baseline, std = detector.baseline_saisonnier(historique, date_target)
        
        assert 0 <= baseline <= 1
        assert std >= 0
    
    def test_anomaly_score_calculation(self):
        """Tester calcul score anomalie"""
        from services.anomaly import detector
        
        ndvi_observe = [0.40, 0.38, 0.35, 0.33, 0.32]
        ndvi_attendu = [0.50, 0.51, 0.50, 0.49, 0.48]
        
        score = detector.calculer_anomaly_score(ndvi_observe, ndvi_attendu)
        
        assert 0 <= score <= 100
        # Score élevé car grande différence
        assert score > 50
    
    def test_seuils_dynamiques(self):
        """Tester calcul seuils dynamiques"""
        from services.anomaly import detector
        
        scores = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        seuils = detector.calculer_seuils_dynamiques(scores)
        
        assert "vert" in seuils
        assert "orange" in seuils
        assert "rouge" in seuils
        assert seuils["vert"] < seuils["orange"] < seuils["rouge"]


if __name__ == "__main__":
    # Exécuter: python -m pytest tests/test_api.py -v
    pytest.main([__file__, "-v"])
