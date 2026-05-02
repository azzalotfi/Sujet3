const API_BASE = "";

const STATUS_COLORS = {
  vert: "#2f9e44",
  orange: "#f08c00",
  rouge: "#d63324",
};

const map = L.map("map", { zoomControl: true }).setView([35.0, 10.0], 7);

const streetLayer = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 18,
  attribution: "&copy; OpenStreetMap contributors",
});

const satelliteLayer = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  {
    maxZoom: 19,
    attribution: "Tiles &copy; Esri &mdash; Source: Esri, Maxar, GeoEye, Earthstar Geographics",
  }
);

// Fond satellite par défaut
satelliteLayer.addTo(map);

// Contrôle basculer carte/satellite
const baseLayers = { "Satellite": satelliteLayer, "Carte": streetLayer };
L.control.layers(baseLayers, null, { position: "topright" }).addTo(map);

const parcelLayer = L.layerGroup().addTo(map);
const statsList = document.getElementById("statsList");
const details = document.getElementById("details");
const systemeFilter = document.getElementById("systemeFilter");
const refreshBtn = document.getElementById("refreshBtn");

let cachedParcelles = [];

function getPolygon(parcelle) {
  return parcelle.polygone || parcelle.coordinates || [];
}

function getParcelName(parcelle) {
  return parcelle.nom || parcelle.name || parcelle.id || "Parcelle";
}

function getStatusColor(statut) {
  return STATUS_COLORS[statut] || "#5f6d5d";
}

function drawSparkline(containerId, observed, expected) {
  const w = 260;
  const h = 120;
  const pad = 12;
  const values = [...observed, ...expected];
  const min = Math.min(...values, 0.1);
  const max = Math.max(...values, 0.9);
  const scaleX = (i, n) => pad + (i * (w - pad * 2)) / Math.max(1, n - 1);
  const scaleY = (v) => h - pad - ((v - min) * (h - pad * 2)) / Math.max(0.001, max - min);

  const path = (arr) =>
    arr
      .map((v, i) => `${i === 0 ? "M" : "L"}${scaleX(i, arr.length)} ${scaleY(v)}`)
      .join(" ");

  const svg = `
    <svg viewBox="0 0 ${w} ${h}" class="spark" role="img" aria-label="Courbe NDVI">
      <rect x="0" y="0" width="${w}" height="${h}" fill="#f8fbf5"></rect>
      <path d="${path(expected)}" fill="none" stroke="#2f6b3f" stroke-width="2" stroke-dasharray="4 3"></path>
      <path d="${path(observed)}" fill="none" stroke="#d63324" stroke-width="2.4"></path>
    </svg>`;

  document.getElementById(containerId).innerHTML = svg;
}

function toDiagnosticPayload(rawParcelle) {
  const poly = getPolygon(rawParcelle);
  const inferredSysteme = rawParcelle.systeme || (rawParcelle.name ? "intensif" : "extensif");
  return {
    oliveraie: {
      id: rawParcelle.id,
      nom: getParcelName(rawParcelle),
      polygone: poly,
      systeme: inferredSysteme,
      area_ha: rawParcelle.area_ha || 0,
    },
    date: new Date().toISOString().slice(0, 10),
  };
}

async function fetchParcelles() {
  const res = await fetch(`${API_BASE}/api/parcelles`);
  if (!res.ok) {
    throw new Error(`Erreur /api/parcelles: ${res.status}`);
  }
  const data = await res.json();
  return data.parcelles || [];
}

async function diagnoseParcelle(parcelle) {
  const payload = toDiagnosticPayload(parcelle);
  const res = await fetch(`${API_BASE}/api/diagnostic-anomalie`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    throw new Error(`Diagnostic ${parcelle.id} echoue: ${res.status}`);
  }
  return res.json();
}

function updateStats(diagnostics) {
  const counts = { vert: 0, orange: 0, rouge: 0 };
  diagnostics.forEach((d) => {
    counts[d.statut] = (counts[d.statut] || 0) + 1;
  });

  statsList.innerHTML = "";
  ["vert", "orange", "rouge"].forEach((key) => {
    const li = document.createElement("li");
    li.textContent = `${key.toUpperCase()}: ${counts[key]}`;
    li.style.borderLeft = `6px solid ${getStatusColor(key)}`;
    statsList.appendChild(li);
  });
}

function buildSourceBadges(meta) {
  if (!meta || !meta.weather_context) return "";
  const wx = meta.weather_context;
  const badges = [];

  // CHIRPS
  if (wx.chirps) {
    const mm = wx.chirps.total_mm != null ? wx.chirps.total_mm.toFixed(1) + "mm" : "";
    badges.push(`<span class="badge badge-chirps">CHIRPS ${mm}</span>`);
  }
  // MODIS LST
  if (wx.modis_lst) {
    const t = wx.modis_lst.lst_mean_c != null ? wx.modis_lst.lst_mean_c.toFixed(1) + "°C" : "";
    badges.push(`<span class="badge badge-modis">MODIS LST ${t}</span>`);
  }
  // GEE
  if (wx.gee) {
    const ndvi = wx.gee.ndvi_mean != null ? " NDVI=" + wx.gee.ndvi_mean.toFixed(2) : "";
    badges.push(`<span class="badge badge-gee">GEE Sentinel-2${ndvi}</span>`);
  }

  if (badges.length === 0) return "";
  return `<div class="sources-section"><strong>Sources actives:</strong><br>${badges.join(" ")}</div>`;
}

function showDetails(parcelle, diagnostic) {
  const meta = diagnostic.metadata || {};
  const sourceBadges = buildSourceBadges(meta);

  details.innerHTML = `
    <h3>${getParcelName(parcelle)}</h3>
    <p><strong>Statut:</strong> <span style="color:${getStatusColor(diagnostic.statut)}">${diagnostic.statut.toUpperCase()}</span></p>
    <p><strong>Score:</strong> ${diagnostic.anomaly_score.toFixed(1)}</p>
    <p>${diagnostic.explication}</p>
    <p><strong>Recommandation:</strong> ${diagnostic.recommandation}</p>
    ${sourceBadges}
    <div id="sparkContainer"></div>
  `;
  drawSparkline("sparkContainer", diagnostic.ndvi_observe || [], diagnostic.ndvi_attendu || []);
}

function makePopup(parcelle, diagnostic) {
  return `
    <div>
      <strong>${getParcelName(parcelle)}</strong><br/>
      Statut: <span style="color:${getStatusColor(diagnostic.statut)}">${diagnostic.statut}</span><br/>
      Score: ${diagnostic.anomaly_score.toFixed(1)}
    </div>
  `;
}

async function renderMap() {
  parcelLayer.clearLayers();
  details.innerHTML = "<h3>Details parcelle</h3><p>Chargement en cours...</p>";

  const selectedSysteme = systemeFilter.value;
  const parcelles = selectedSysteme === "all"
    ? cachedParcelles
    : cachedParcelles.filter((p) => (p.systeme || "extensif") === selectedSysteme);

  const validParcelles = parcelles.filter((p) => getPolygon(p).length > 0);

  // Appels parallèles (Promise.allSettled ne bloque pas si une parcelle échoue)
  const results = await Promise.allSettled(
    validParcelles.map((p) => diagnoseParcelle(p))
  );

  const diagnostics = [];
  const bounds = [];

  results.forEach((res, i) => {
    const parcelle = validParcelles[i];
    if (res.status !== "fulfilled") {
      console.error(`Diagnostic ${parcelle.id} echoue:`, res.reason);
      return;
    }
    const diagnostic = res.value;
    diagnostics.push(diagnostic);

    const latlngs = getPolygon(parcelle).map((pt) => [pt.lat, pt.lng]);
    const shape = L.polygon(latlngs, {
      color: getStatusColor(diagnostic.statut),
      weight: 2,
      fillOpacity: 0.45,
    }).addTo(parcelLayer);

    shape.bindPopup(makePopup(parcelle, diagnostic));
    shape.on("click", () => showDetails(parcelle, diagnostic));
    bounds.push(...latlngs);
  });

  if (bounds.length) {
    map.fitBounds(bounds, { padding: [18, 18] });
  }

  updateStats(diagnostics);
  details.innerHTML = "<h3>Details parcelle</h3><p>Clique une parcelle sur la carte pour voir le diagnostic.</p>";
}

async function bootstrap() {
  try {
    cachedParcelles = await fetchParcelles();
    await renderMap();
  } catch (err) {
    console.error(err);
    details.innerHTML = `<h3>Erreur</h3><p>${err.message}</p>`;
  }
}

refreshBtn.addEventListener("click", renderMap);
systemeFilter.addEventListener("change", renderMap);

bootstrap();
