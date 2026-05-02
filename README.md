# 🌳 Oliveraies - Système de Détection d'Anomalies NDVI

Détection précoce du stress hydrique et des anomalies sur oliveraies Tunisiennes via analyse satellite NDVI en temps réel.

---

## 🚀 Démarrage Rapide (5 minutes)

### 1. Vérifier l'installation Python
```bash
cd c:\projet 3
python --version
pip list | findstr fastapi
```

### 2. Lancer l'API
```bash
# Démarrer le serveur FastAPI
uvicorn src.main:app --reload --port 8000
```

✅ L'API est accessible:
- **API REST**: http://localhost:8000/docs (Swagger UI)
- **Dashboard**: http://localhost:8000/dashboard
- **Health**: http://localhost:8000/health

### 3. Tester un diagnostic
```bash
# Via curl
curl -X POST http://localhost:8000/api/diagnostic-anomalie \
  -H "Content-Type: application/json" \
  -d '{
    "oliveraie": {
      "id": "test_001",
      "nom": "Test Parcelle",
      "polygone": [{"lat": 35.29, "lng": 10.61}],
      "systeme": "extensif",
      "area_ha": 50
    },
    "date": "2026-05-02"
  }'
```

---

## 📋 Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Frontend (Leaflet)                │
│              Dashboard Interactive - /dashboard     │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP REST
                       ▼
┌─────────────────────────────────────────────────────┐
│              FastAPI Backend - :8000                │
├─────────────────────────────────────────────────────┤
│ Routes:                                             │
│  POST /api/diagnostic-anomalie  → Diagnostic complet│
│  GET  /api/parcelles            → Liste oliveraies  │
│  GET  /api/sante/{parcel_id}    → État parcelle    │
│  GET  /health                   → Health check     │
│  GET  /docs                     → Swagger UI       │
└──────────────────────┬──────────────────────────────┘
                       │
    ┌──────────────────┼──────────────────┐
    ▼                  ▼                  ▼
┌────────────┐  ┌────────────┐  ┌─────────────────┐
│  Données   │  │ Détection  │  │  APIs Externes  │
│ Oliveraies │  │ Anomalies  │  ├─────────────────┤
│  (JSON)    │  │  (NumPy)   │  │ Sentinel-2 (GEE)│
└────────────┘  └────────────┘  │ Open-Meteo      │
                                 │ CHIRPS (Pluie)  │
                                 └─────────────────┘
```

---

## 📁 Structure du Projet

```
c:\projet 3\
├── src/
│   ├── main.py                  # 🎯 FastAPI app principale
│   ├── models.py                # 📝 Pydantic schemas
│   ├── services/
│   │   ├── anomaly.py           # 🔍 Détection anomalies (NumPy)
│   │   ├── satellite.py         # 📡 Sentinel-2/GEE (TODO)
│   │   └── weather.py           # 🌤️ Open-Meteo API (TODO)
│   └── dashboard/
│       ├── index.html           # 🗺️ Leaflet interface
│       ├── app.js               # 🔧 Frontend logic
│       └── style.css            # 🎨 Styling
│
├── tests/                        # 🧪 Unit tests
├── Oliviers/                     # 📊 Données oliveraies (JSON)
│   ├── parcelles_OlivierExtensif.json
│   └── parcellesOliviersIntensifs.json
│
├── .env                          # ⚙️ Configuration
├── requirements.txt              # 📦 Dependencies
├── INSTALLATION_SUMMARY.md       # 📋 Installation
├── AUDIT_TECHNOLOGIES.md         # 🏗️ Architecture
└── README.md                     # 📖 This file
```

---

## 🔧 Commandes Disponibles

### Développement
```bash
# Lancer avec hot-reload
uvicorn src.main:app --reload --port 8000

# Lancer avec logging débogué
uvicorn src.main:app --reload --log-level debug

# Accéder à la documentation interactive
# → http://localhost:8000/docs
```

### Production
```bash
# Démarrer sans hot-reload
uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 4

# Avec Gunicorn (Unix only)
gunicorn -w 4 -b 0.0.0.0:8000 src.main:app
```

### Tests
```bash
# Exécuter tous les tests
pytest tests/

# Avec coverage
pytest --cov=src tests/
```

---

## 📊 Endpoints API

### 1. Diagnostic Complet
```http
POST /api/diagnostic-anomalie
Content-Type: application/json

{
  "oliveraie": {
    "id": "O_2026_001",
    "nom": "Parcelle Nord",
    "polygone": [
      {"lat": 35.29, "lng": 10.61},
      {"lat": 35.30, "lng": 10.62}
    ],
    "systeme": "extensif",
    "area_ha": 95.2
  },
  "date": "2026-05-02"
}
```

**Response:**
```json
{
  "statut": "orange",
  "anomaly_score": 34.5,
  "ndvi_observe": [0.42, 0.41, 0.38, ...],
  "ndvi_attendu": [0.48, 0.50, 0.49, ...],
  "dates": ["2026-04-11", "2026-04-12", ...],
  "explication": "NDVI 12% en dessous attendu...",
  "recommandation": "Inspection visuelle dans 3-5 jours...",
  "metadata": {...}
}
```

### 2. Lister les Parcelles
```http
GET /api/parcelles
```

### 3. Santé d'une Parcelle
```http
GET /api/sante/{parcel_id}
```

### 4. Health Check
```http
GET /health
```

### 5. Dashboard
```http
GET /dashboard
```

---

## 🔑 Configuration

Éditer `.env`:
```bash
# API
API_HOST=0.0.0.0
API_PORT=8000

# Google Earth Engine (optionnel)
EE_PROJECT_ID=your-project-id

# Copernicus (optionnel)
COPERNICUS_USER=email@example.com
COPERNICUS_PASSWORD=password
```

---

## 🎯 Prochaines Étapes

### Phase 1: En développement ✅
- [x] API FastAPI minimale
- [x] Modèles Pydantic
- [x] Détection anomalies (NumPy)
- [x] Dashboard Leaflet basique

### Phase 2: À implémenter
- [ ] Intégration Sentinel-2 (sentinelhub)
- [ ] Intégration Google Earth Engine
- [ ] Intégration Open-Meteo
- [ ] Persistance PostgreSQL (optionnel)
- [ ] Tests unitaires
- [ ] Documentation API complète

### Phase 3: Production
- [ ] Containerization Docker
- [ ] CI/CD pipeline
- [ ] Monitoring & Logging
- [ ] Performance optimization

---

## 🐛 Troubleshooting

### Erreur: "ModuleNotFoundError: No module named 'src'"
```bash
# Ajouter au PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:c:\projet 3"

# OU: exécuter depuis c:\projet 3
cd c:\projet 3
uvicorn src.main:app --reload
```

### Erreur: "Address already in use :8000"
```bash
# Changer le port
uvicorn src.main:app --port 8001

# OU: tuer le processus
lsof -i :8000  # Unix
netstat -ano | findstr :8000  # Windows
```

### Erreur: "No JSON data found"
```bash
# Vérifier que les fichiers oliveraies existent
ls -la Oliviers/parcelles*.json
```

---

## 📚 Documentation

- **API Docs**: http://localhost:8000/docs (Swagger)
- **Installation**: [INSTALLATION_SUMMARY.md](INSTALLATION_SUMMARY.md)
- **Architecture**: [AUDIT_TECHNOLOGIES.md](AUDIT_TECHNOLOGIES.md)
- **Limitations**: [PACKAGES_LIMITATION.md](PACKAGES_LIMITATION.md)

---

## 🤝 Contributing

Pour ajouter des features:
1. Créer une branche: `git checkout -b feature/ma-feature`
2. Commiter: `git commit -am "Ajout ma-feature"`
3. Push: `git push origin feature/ma-feature`
4. Créer une Pull Request

---

## 📝 Licence

MIT License - Voir [LICENSE](LICENSE) pour détails

---

## 📞 Support

Pour les problèmes:
1. Consulter [Troubleshooting](#-troubleshooting)
2. Vérifier [AUDIT_TECHNOLOGIES.md](AUDIT_TECHNOLOGIES.md)
3. Créer une issue si problème non résolu

---

## 🎨 Dashboard

Le dashboard Leaflet est accessible à:
- **URL**: http://localhost:8000/dashboard
- **Fonctionnalités**:
  - 🗺️ Carte interactive avec toutes les parcelles
  - 📊 Graphiques NDVI observé vs attendu
  - 🔴🟡🟢 Codage couleur par statut
  - 📋 Diagnostic détaillé par parcelle
  - 📥 Export données (TODO)

---

**Version**: 1.0.0  
**Last Updated**: 2026-05-02  
**Status**: MVP Ready 🚀
