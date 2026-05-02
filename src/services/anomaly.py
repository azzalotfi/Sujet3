"""
Service de détection d'anomalies utilisant NumPy
Approche: Baseline saisonnier + Écart normalisé + Seuils dynamiques
"""
import numpy as np
from datetime import datetime
from typing import List, Dict, Tuple
import json

from services.weather import fetch_weather_context


class AnomalyDetector:
    """Détecteur d'anomalies NDVI sans dépendances externes"""
    
    def __init__(self, ndvi_historique_path: str = None):
        """
        Initialize detector
        
        Args:
            ndvi_historique_path: Chemin vers données historiques JSON
        """
        self.historique = {}
        if ndvi_historique_path:
            self.charger_historique(ndvi_historique_path)
    
    def charger_historique(self, path: str):
        """Charger données historiques NDVI"""
        try:
            with open(path) as f:
                self.historique = json.load(f)
        except Exception as e:
            print(f"Erreur chargement historique: {e}")
    
    def baseline_saisonnier(self, 
                           ndvi_historique: List[List[float]], 
                           date_cible: datetime,
                           fenetre_jours: int = 30) -> Tuple[float, float]:
        """
        Calculer le NDVI attendu basé sur la saisonnalité
        
        Args:
            ndvi_historique: Liste de NDVI par jour des années précédentes
            date_cible: Date pour laquelle calculer le baseline
            fenetre_jours: Fenêtre glissante (jours)
        
        Returns:
            (ndvi_attendu, écart_type)
        """
        if not ndvi_historique or all(not isinstance(annee, list) or len(annee) == 0 for annee in ndvi_historique):
            return 0.5, 0.05

        jour_annee = date_cible.timetuple().tm_yday
        
        # Fenêtre saisonnière: ±fenetre_jours autour du même jour
        debut = max(1, jour_annee - fenetre_jours)
        fin = min(365, jour_annee + fenetre_jours)
        
        # Collecte les NDVI de la même saison (années précédentes)
        ndvi_saison = []
        for annee_data in ndvi_historique:
            if isinstance(annee_data, list) and len(annee_data) > fin:
                ndvi_saison.extend(annee_data[debut:fin])
        
        if not ndvi_saison:
            return 0.5, 0.05  # Valeurs par défaut
        
        ndvi_saison = np.array(ndvi_saison)
        return float(np.mean(ndvi_saison)), float(np.std(ndvi_saison))

    def _serie_attendue_saisonniere(
        self,
        ndvi_historique: List[List[float]],
        date_cible: datetime,
        window_days: int = 21,
        fenetre_saison: int = 20,
    ) -> List[float]:
        """Construire une série NDVI attendue sans aléatoire.

        Pour chaque jour de la fenêtre, on prend la moyenne des valeurs
        historiques autour du même jour de l'année.
        """
        if not ndvi_historique:
            return [0.5] * window_days

        expected: List[float] = []
        day_end = max(1, min(365, date_cible.timetuple().tm_yday))
        day_start = max(1, day_end - window_days + 1)

        for day in range(day_start, day_end + 1):
            values = []
            left = max(1, day - fenetre_saison)
            right = min(365, day + fenetre_saison)

            for year in ndvi_historique:
                if not isinstance(year, list) or len(year) < right:
                    continue
                values.extend(year[left - 1:right])

            if values:
                expected.append(float(np.mean(values)))
            elif expected:
                expected.append(expected[-1])
            else:
                expected.append(0.5)

        if len(expected) < window_days:
            fill = expected[-1] if expected else 0.5
            expected = [fill] * (window_days - len(expected)) + expected

        return [max(0.0, min(1.0, v)) for v in expected[-window_days:]]

    def _ajuster_ndvi_attendu(
        self,
        ndvi_attendu: List[float],
        systeme: str,
        meteo: Dict,
    ) -> List[float]:
        """Ajuster la série attendue selon système de conduite et météo."""
        arr = np.array(ndvi_attendu, dtype=float)

        # Effet système (calibrage simple pour séparer extensif/intensif/HI)
        system_gain = {
            "extensif": 1.0,
            "intensif": 1.12,
            "hyper-intensif": 1.22,
        }.get(systeme, 1.0)

        precipitation_21d = float(meteo.get("precipitation_21d_mm", 0.0) or 0.0)
        temperature_mean = float(meteo.get("temperature_mean_c", 25.0) or 25.0)
        water_stress_index = float(meteo.get("water_stress_index", 0.0) or 0.0)

        weather_penalty = 0.0
        if precipitation_21d < 18.0:
            weather_penalty += min(0.14, (18.0 - precipitation_21d) / 100.0)
        if temperature_mean > 30.0:
            weather_penalty += min(0.08, (temperature_mean - 30.0) / 60.0)
        weather_penalty += min(0.08, water_stress_index * 0.08)

        adjusted = arr * system_gain * (1.0 - weather_penalty)
        adjusted = np.clip(adjusted, 0.0, 1.0)
        return [float(v) for v in adjusted]
    
    def calculer_anomaly_score(self, 
                              ndvi_observe: List[float], 
                              ndvi_attendu: List[float]) -> float:
        """
        Calculer score d'anomalie normalisé
        
        Args:
            ndvi_observe: NDVI observé sur fenêtre 3 semaines
            ndvi_attendu: NDVI attendu baseline
        
        Returns:
            Score 0-100 (0=normal, 100=anomalie max)
        """
        ndvi_observe = np.array(ndvi_observe)
        ndvi_attendu = np.array(ndvi_attendu)
        
        if ndvi_observe.size == 0 or ndvi_attendu.size == 0:
            return 0.0

        # Le stress agronomique est surtout lié à un NDVI observé en dessous attendu.
        deficit = np.maximum(0.0, ndvi_attendu - ndvi_observe)
        deficit_mean = float(np.mean(deficit))

        dispersion = float(np.std(ndvi_attendu))
        if dispersion < 0.015:
            dispersion = 0.015

        z_like = deficit_mean / dispersion
        score = min(100.0, z_like * 28.0)
        return float(score)

    def _scores_historiques_parcelle(
        self,
        ndvi_historique: List[List[float]],
        date_cible: datetime,
        systeme: str,
        meteo: Dict,
        window_days: int = 21,
    ) -> List[float]:
        """Construire une distribution de scores historiques pour seuils dynamiques.

        Chaque année joue le rôle de "observé" et les autres années forment l'attendu.
        """
        if not ndvi_historique or len(ndvi_historique) < 3:
            return [8.0, 12.0, 16.0, 22.0, 30.0, 38.0]

        day_end = max(1, min(365, date_cible.timetuple().tm_yday))
        day_start = max(1, day_end - window_days + 1)
        scores: List[float] = []

        for idx, year in enumerate(ndvi_historique):
            if not isinstance(year, list) or len(year) < day_end:
                continue

            observed = [float(v) for v in year[day_start - 1:day_end]]
            if len(observed) != window_days:
                continue

            others = [y for j, y in enumerate(ndvi_historique) if j != idx and isinstance(y, list)]
            if not others:
                continue

            expected = self._serie_attendue_saisonniere(
                ndvi_historique=others,
                date_cible=date_cible,
                window_days=window_days,
                fenetre_saison=20,
            )
            expected = self._ajuster_ndvi_attendu(expected, systeme=systeme, meteo=meteo)
            scores.append(self.calculer_anomaly_score(observed, expected))

        if len(scores) < 6:
            # Fallback: seuils absolus calibres sur la variabilite NDVI normale
            # en Tunisie pour chaque systeme de conduite
            fallbacks = {
                "extensif": [8.0, 12.0, 17.0, 24.0, 32.0, 42.0],
                "intensif": [6.0, 10.0, 15.0, 22.0, 30.0, 40.0],
                "hyper-intensif": [5.0, 9.0, 13.0, 20.0, 28.0, 38.0],
            }
            scores.extend(fallbacks.get(systeme, fallbacks["extensif"]))
        return scores
    
    def calculer_seuils_dynamiques(self, 
                                   scores_historiques: List[float]) -> Dict[str, float]:
        """
        Calculer seuils d'alerte basés sur quantiles
        
        Args:
            scores_historiques: Scores d'anomalie historiques
        
        Returns:
            Seuils {vert, orange, rouge}
        """
        scores = np.array(scores_historiques, dtype=float)
        if scores.size == 0:
            scores = np.array([10.0, 15.0, 22.0, 30.0, 40.0], dtype=float)
        
        seuil_vert = min(float(np.percentile(scores, 50)), 38.0)
        seuil_orange = min(float(np.percentile(scores, 80)), 62.0)
        seuil_rouge = min(float(np.percentile(scores, 93)), 78.0)
        # Garantir l'ordre strict vert < orange < rouge
        seuil_orange = max(seuil_orange, seuil_vert + 2.0)
        seuil_rouge = max(seuil_rouge, seuil_orange + 2.0)
        return {"vert": seuil_vert, "orange": seuil_orange, "rouge": seuil_rouge}
    
    def determiner_statut(self, score: float, seuils: Dict[str, float]) -> str:
        """Déterminer statut (vert/orange/rouge) selon score"""
        if score <= seuils["vert"]:
            return "vert"
        elif score < seuils["rouge"]:
            return "orange"
        else:
            return "rouge"
    
    def _analyser_causes(
        self,
        ecart_pct: float,
        meteo: Dict,
        systeme: str,
        ndvi_observe: List[float],
    ) -> Tuple[str, str]:
        """Identifier la ou les causes probables du décrochage NDVI.

        Utilise en priorité:
        - CHIRPS (precipitation_21d_mm overridden) si disponible
        - MODIS LST (lst_mean_c, heat_stress_days) si disponible

        Returns:
            (cause_label, detail_meteorologique)
        """
        prec = float(meteo.get("precipitation_21d_mm", 0.0) or 0.0) if meteo else 0.0
        temp = float(meteo.get("temperature_mean_c", 25.0) or 25.0) if meteo else 25.0
        wsi = float(meteo.get("water_stress_index", 0.0) or 0.0) if meteo else 0.0

        # MODIS LST enrichissement
        modis = meteo.get("modis_lst", {}) if meteo else {}
        lst_mean = float(modis.get("lst_mean_c", temp) or temp)
        lst_max = float(modis.get("lst_max_c", temp + 4) or temp + 4)
        heat_stress_days = int(modis.get("heat_stress_days", 0) or 0)
        very_hot_days = int(modis.get("very_hot_days", 0) or 0)
        has_modis = bool(modis)

        # CHIRPS enrichissement
        chirps = meteo.get("chirps", {}) if meteo else {}
        dry_days = int(chirps.get("dry_days", 0) or 0)
        max_daily_mm = float(chirps.get("max_daily_mm", 0.0) or 0.0)
        has_chirps = bool(chirps)

        # Critères de stress (LST prioritaire sur T2m quand MODIS disponible)
        effective_temp = lst_mean if has_modis else temp
        secheresse = prec < 10.0
        pluie_normale = prec >= 18.0
        canicule = effective_temp > 35.0 or (has_modis and heat_stress_days >= 5)
        canicule_severe = has_modis and (very_hot_days >= 3 or lst_max > 48.0)
        chaleur_moderee = 30.0 < effective_temp <= 35.0
        stress_hydrique_fort = wsi > 0.6
        secheresse_persistante = has_chirps and dry_days >= 14

        # Tendance NDVI
        if ndvi_observe and len(ndvi_observe) >= 14:
            tendance_chute = float(np.mean(ndvi_observe[-7:])) < float(np.mean(ndvi_observe[-14:-7])) - 0.02
        else:
            tendance_chute = ecart_pct < -10.0

        # Sources utilisees dans le detail
        sources_label = []
        if has_chirps:
            sources_label.append(f"CHIRPS {prec:.0f}mm")
        else:
            sources_label.append(f"Pluie {prec:.0f}mm")
        if has_modis:
            sources_label.append(f"MODIS LST moy {lst_mean:.1f}C max {lst_max:.0f}C ({heat_stress_days}j stress)")
        else:
            sources_label.append(f"Temp {temp:.1f}C")
        sources_label.append(f"WSI {wsi:.2f}")

        detail = ", ".join(sources_label) + "."

        # Classification causale
        if canicule_severe and (secheresse or stress_hydrique_fort):
            cause = "stress-hydrique-canicule"
        elif pluie_normale and ecart_pct < -8.0:
            cause = "stress-non-hydrique"
        elif (secheresse or secheresse_persistante) and canicule:
            cause = "stress-hydrique-canicule"
        elif secheresse or secheresse_persistante or stress_hydrique_fort:
            cause = "stress-hydrique"
        elif canicule:
            cause = "stress-thermique"
        elif chaleur_moderee and tendance_chute:
            cause = "stress-thermique-progressif"
        else:
            cause = "anomalie-indeterminee"

        return cause, detail

    def generer_explication(
        self,
        statut: str,
        score: float,
        ecart_pct: float,
        meteo: Dict = None,
        systeme: str = "extensif",
        ndvi_observe: List[float] = None,
        ndvi_attendu: List[float] = None,
    ) -> Tuple[str, str]:
        """Générer explication causale et recommandation agronomique."""
        if ndvi_observe is None:
            ndvi_observe = []
        if ndvi_attendu is None:
            ndvi_attendu = []
        if meteo is None:
            meteo = {}

        abs_ecart = abs(ecart_pct)
        direction = "en dessous" if ecart_pct < 0 else "au dessus"

        if statut == "vert":
            expl = (
                f"Vegetation en bonne sante. NDVI dans les normes saisonnieres "
                f"(ecart de {abs_ecart:.1f}% par rapport au baseline). "
                f"Pluviometrie 21j: {float(meteo.get('precipitation_21d_mm', 0) or 0):.0f}mm, "
                f"temperature: {float(meteo.get('temperature_mean_c', 25) or 25):.1f}C."
            )
            reco = "Surveillance hebdomadaire routiniere. Prochain controle recommande dans 7 jours."
            return expl, reco

        cause, detail_meteo = self._analyser_causes(ecart_pct, meteo, systeme, ndvi_observe)

        ndvi_obs_str = (
            f"[{', '.join(f'{v:.2f}' for v in ndvi_observe[:5])}...]"
            if len(ndvi_observe) >= 5 else str(ndvi_observe)
        )
        ndvi_att_str = (
            f"[{', '.join(f'{v:.2f}' for v in ndvi_attendu[:5])}...]"
            if len(ndvi_attendu) >= 5 else str(ndvi_attendu)
        )

        # Construire l'explication selon la cause identifiée
        cause_text = {
            "stress-non-hydrique": (
                f"NDVI {abs_ecart:.0f}% {direction} attendu sur 3 semaines, malgre pluie normale. "
                "Stress probable non lie a la secheresse - verifier irrigation goutte-a-goutte, "
                "ravageurs (teigne, cochenille) ou maladie racinaire."
            ),
            "stress-hydrique-canicule": (
                f"NDVI {abs_ecart:.0f}% {direction} attendu. "
                "Deficit hydrique severe combine a des temperatures elevees detecte. "
                "Risque de deshydratation foliaire irreversible si non traite."
            ),
            "stress-hydrique": (
                f"NDVI {abs_ecart:.0f}% {direction} attendu sur 3 semaines. "
                "Deficit pluviometrique important - stress hydrique probable. "
                "L'olivier reduit sa surface foliaire active en reponse au manque d'eau."
            ),
            "stress-thermique": (
                f"NDVI {abs_ecart:.0f}% {direction} attendu. "
                "Temperatures elevees detectees - stress thermique probable. "
                "La photosynthese peut etre inhibee au-dela de 35C."
            ),
            "stress-thermique-progressif": (
                f"NDVI {abs_ecart:.0f}% {direction} attendu avec tendance a la baisse sur 7 jours. "
                "Chaleur moderee mais prolongee - stress cumulatif possible."
            ),
            "anomalie-indeterminee": (
                f"NDVI {abs_ecart:.0f}% {direction} attendu. "
                "Cause non determinee avec certitude - necessite inspection terrain."
            ),
        }.get(cause, f"NDVI {abs_ecart:.0f}% {direction} attendu.")

        if statut == "orange":
            expl = (
                f"Anomalie moderee detectee. {cause_text} "
                f"{detail_meteo} Score: {score:.1f}/100."
            )
            reco_map = {
                "stress-non-hydrique": "Inspection visuelle dans 3-5 jours. Verifier les feuilles (presence ravageurs, jaunissement). Controler le debit d'irrigation.",
                "stress-hydrique-canicule": "Irrigation d'appoint urgente. Reduire le stress thermique si possible (paillage). Inspection dans 48h.",
                "stress-hydrique": "Irrigation d'appoint recommandee dans les 3-5 jours. Verifier l'etat du sol en surface.",
                "stress-thermique": "Surveiller l'evolution. Si canicule > 3 jours consecutifs, prevoir irrigation complementaire.",
                "stress-thermique-progressif": "Inspection dans 5 jours. Envisager irrigation preventive si chaleur persiste.",
                "anomalie-indéterminée": "Inspection visuelle dans 3-5 jours pour identifier la cause.",
            }
            reco = reco_map.get(cause, "Inspection visuelle dans 3-5 jours.")

        else:  # rouge
            expl = (
                f"ANOMALIE CRITIQUE. {cause_text} "
                f"{detail_meteo} Score: {score:.1f}/100. "
                f"NDVI observe: {ndvi_obs_str} vs attendu: {ndvi_att_str}."
            )
            reco_map = {
                "stress-non-hydrique": "INSPECTION URGENTE 24-48h. Verifier ravageurs et systeme d'irrigation. Prelevements foliaires recommandes.",
                "stress-hydrique-canicule": "IRRIGATION IMMEDIATE. Intervention dans les 24h. Risque de pertes irreversibles.",
                "stress-hydrique": "IRRIGATION URGENTE dans les 24h. Verifier les conduites et robinets de secteur. Contactez un technicien.",
                "stress-thermique": "INSPECTION 24-48h. Irrigation de nuit pour reduire le stress. Eviter les traitements par forte chaleur.",
                "stress-thermique-progressif": "INSPECTION URGENTE. Irrigation immediate et suivi quotidien.",
                "anomalie-indéterminée": "INSPECTION URGENTE 24-48h. Photographier et documenter l'etat des arbres.",
            }
            reco = reco_map.get(cause, "Inspection URGENTE dans 24-48h.")

        return expl, reco
    
    def diagnostiquer(self,
                     ndvi_recent: List[float],
                     ndvi_historique: List[List[float]],
                     date_cible: datetime,
                     systeme: str = "extensif",
                     meteo: Dict = None,
                     oliveraie: Dict = None) -> Dict:
        """
        Diagnostic complet d'anomalie
        
        Args:
            ndvi_recent: NDVI des 21 derniers jours
            ndvi_historique: NDVI des années précédentes (par jour)
            date_cible: Date du diagnostic
            systeme: extensif/intensif/hyper-intensif
            meteo: Données météo (optionnel)
            oliveraie: Contexte parcellaire pour récupérer les coordonnées
        
        Returns:
            Dictionnaire avec résultats diagnostic
        """
        if meteo is None:
            meteo = fetch_weather_context(date_cible, oliveraie, window_days=21)

        # 1. NDVI attendu déterministe sur fenêtre glissante
        ndvi_attendu = self._serie_attendue_saisonniere(
            ndvi_historique=ndvi_historique,
            date_cible=date_cible,
            window_days=21,
            fenetre_saison=20,
        )
        ndvi_attendu = self._ajuster_ndvi_attendu(ndvi_attendu, systeme=systeme, meteo=meteo)
        ndvi_attendu_val = float(np.mean(ndvi_attendu)) if ndvi_attendu else 0.5
        
        # 2. Calculer score d'anomalie
        score = self.calculer_anomaly_score(ndvi_recent, ndvi_attendu)
        
        # 3. Déterminer statut
        scores_historiques = self._scores_historiques_parcelle(
            ndvi_historique=ndvi_historique,
            date_cible=date_cible,
            systeme=systeme,
            meteo=meteo,
            window_days=21,
        )
        seuils = self.calculer_seuils_dynamiques(scores_historiques)
        statut = self.determiner_statut(score, seuils)
        
        # 4. Écart %
        ndvi_recent_moyen = np.mean(ndvi_recent)
        ecart_pct = ((ndvi_recent_moyen - ndvi_attendu_val) / ndvi_attendu_val) * 100 \
            if ndvi_attendu_val > 0 else 0
        
        # 5. Générer explication
        expl, reco = self.generer_explication(
            statut=statut,
            score=score,
            ecart_pct=ecart_pct,
            meteo=meteo,
            systeme=systeme,
            ndvi_observe=list(ndvi_recent),
            ndvi_attendu=ndvi_attendu,
        )
        
        return {
            "statut": statut,
            "anomaly_score": score,
            "ndvi_observe": [float(v) for v in ndvi_recent],
            "ndvi_attendu": [float(v) for v in ndvi_attendu],
            "ecart_pct": ecart_pct,
            "explication": expl,
            "recommandation": reco,
            "baseline": ndvi_attendu_val,
            "weather_context": meteo,
            "seuils": seuils
        }


# Instance globale
detector = AnomalyDetector()
