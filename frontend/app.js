/* ---------------------------------------------------------------------------
   Safe Spot — lógica del frontend
   ---------------------------------------------------------------------------

   Aquí es donde el navegador toca AWS. Hay exactamente cuatro puntos de
   contacto y vale la pena tenerlos localizados:

     1. Amazon Location  ->  el estilo del mapa que carga MapLibre
     2. GET  /places     ->  los pines
     3. POST /search     ->  Amazon Bedrock interpreta lo que escribiste
     4. POST /places     ->  registrar un espacio nuevo

   Una idea importante: el emparejamiento entre los criterios que devuelve
   Bedrock y los lugares NO ocurre en la nube. Ocurre en `applyFilters()`, unas
   líneas más abajo. El modelo interpreta; este código decide.
--------------------------------------------------------------------------- */

"use strict";

// ---------------------------------------------------------------------------
// 1. Configuración
// ---------------------------------------------------------------------------

// config.js lo genera scripts/publish-frontend.sh con los Outputs de tu stack.
const CONFIG = window.SAFE_SPOT_CONFIG;

const MAP_CENTER = [-99.15, 19.42]; // Ciudad de México [longitud, latitud]
const MAP_ZOOM = 12.4;

// Etiquetas legibles. Si añades una señal en template.yaml y no la añades
// aquí, la interfaz mostrará su identificador tal cual en vez de romperse.
const SIGNAL_LABELS = {
  lgbtq_space: "🌈 Espacio LGBTQ+",
  neutral_bathroom: "🚻 Baño neutral",
  accessible: "♿ Accesible",
  pronouns_respected: "🏷️ Respetan pronombres",
  couples_friendly: "💞 Cómodo en pareja",
  quiet: "🤫 Tranquilo",
  inclusive_healthcare: "🩺 Salud inclusiva",
};

const CATEGORY_LABELS = {
  cafe: "Café",
  restaurant: "Restaurante",
  bar: "Bar",
  bookstore: "Librería",
  clinic: "Clínica",
  community_center: "Centro comunitario",
  museum: "Museo",
  park: "Parque",
  coworking: "Coworking",
  shop: "Tienda",
};

const PROVENANCE_LABELS = {
  official_source: "Fuente oficial",
  first_party: "Informado por el lugar",
  community_report: "Reporte de la comunidad",
  community_draft: "Borrador del equipo · sin verificar",
};

// ---------------------------------------------------------------------------
// 2. Estado
// ---------------------------------------------------------------------------

const state = {
  places: [],
  markers: new Map(), // id del lugar -> maplibregl.Marker
  activeSignals: new Set(),
  activeCategory: null,
  selectedId: null,
};

let map = null;

const dom = {
  searchForm: document.getElementById("search-form"),
  searchInput: document.getElementById("search-input"),
  searchButton: document.getElementById("search-button"),
  searchResult: document.getElementById("search-result"),
  signalFilters: document.getElementById("signal-filters"),
  clearFilters: document.getElementById("clear-filters"),
  results: document.getElementById("results"),
  resultsCounter: document.getElementById("results-counter"),
  mapError: document.getElementById("map-error"),
  openForm: document.getElementById("open-form"),
  dialog: document.getElementById("place-dialog"),
  placeForm: document.getElementById("place-form"),
  categorySelect: document.getElementById("category-select"),
  formSignals: document.getElementById("form-signals"),
  formError: document.getElementById("form-error"),
  submitForm: document.getElementById("submit-form"),
  cancelForm: document.getElementById("cancel-form"),
};

// ---------------------------------------------------------------------------
// 3. Arranque
// ---------------------------------------------------------------------------

function init() {
  if (!CONFIG) {
    showMapError(
      "Falta frontend/config.js. Ejecuta ./scripts/publish-frontend.sh para generarlo " +
        "con los datos de tu stack.",
    );
    return;
  }

  buildSignalFilters();
  buildFormControls();
  initMap();
  loadPlaces();

  dom.searchForm.addEventListener("submit", handleSearch);
  dom.clearFilters.addEventListener("click", clearAllFilters);
  dom.openForm.addEventListener("click", () => dom.dialog.showModal());
  dom.cancelForm.addEventListener("click", () => dom.dialog.close());
  dom.placeForm.addEventListener("submit", handleCreatePlace);
}

// ---------------------------------------------------------------------------
// 4. El mapa — Amazon Location Maps V2 renderizado por MapLibre
// ---------------------------------------------------------------------------

function initMap() {
  // Amazon Location expone el estilo del mapa como una URL firmada con la API
  // key. MapLibre solo necesita esa URL: no sabe que detrás hay AWS.
  const styleUrl =
    `https://maps.geo.${CONFIG.region}.amazonaws.com/v2/styles/Standard/descriptor` +
    `?key=${encodeURIComponent(CONFIG.mapsApiKey)}&color-scheme=Dark`;

  map = new maplibregl.Map({
    container: "map",
    style: styleUrl,
    center: MAP_CENTER,
    zoom: MAP_ZOOM,
  });

  map.addControl(new maplibregl.NavigationControl(), "top-right");

  map.on("error", (event) => {
    console.error("MapLibre:", event.error);
    showMapError(
      "El mapa no cargó. Revisa que la API key de Amazon Location sea válida y que " +
        "config.js apunte a tu región.",
    );
  });

  // Comodidad para el formulario: un clic en el mapa rellena las coordenadas.
  map.on("click", (event) => {
    dom.placeForm.latitude.value = event.lngLat.lat.toFixed(5);
    dom.placeForm.longitude.value = event.lngLat.lng.toFixed(5);
  });
}

function showMapError(message) {
  dom.mapError.textContent = message;
  dom.mapError.hidden = false;
}

// ---------------------------------------------------------------------------
// 5. Datos — GET /places
// ---------------------------------------------------------------------------

async function loadPlaces() {
  try {
    const data = await callApi("/places");
    state.places = data.places;
    renderMarkers();
    applyFilters();
  } catch (error) {
    console.error(error);
    dom.results.innerHTML =
      '<li class="empty">No se pudieron cargar los espacios. ¿Ejecutaste scripts/seed.py?</li>';
  }
}

function renderMarkers() {
  state.markers.forEach((marker) => marker.remove());
  state.markers.clear();

  for (const place of state.places) {
    const element = document.createElement("div");
    element.className = "marker";
    element.title = place.name;

    const marker = new maplibregl.Marker({ element })
      .setLngLat([place.longitude, place.latitude])
      .setPopup(new maplibregl.Popup({ offset: 16 }).setHTML(popupHtml(place)))
      .addTo(map);

    element.addEventListener("click", () => selectPlace(place.id));
    state.markers.set(place.id, marker);
  }
}

function popupHtml(place) {
  const signals = place.signals
    .map((signal) => `<span class="tag">${escapeHtml(signalLabel(signal))}</span>`)
    .join("");

  const provenance = place.provenance ?? {};
  const provenanceLabel = PROVENANCE_LABELS[provenance.type] ?? provenance.type ?? "Sin procedencia";

  return `
    <div class="popup__name">${escapeHtml(place.name)}</div>
    <div class="result__meta">${escapeHtml(categoryLabel(place.category))} · ${escapeHtml(place.address ?? "")}</div>
    ${place.communityNote ? `<p class="popup__note">${escapeHtml(place.communityNote)}</p>` : ""}
    <div class="result__signals">${signals}</div>
    <p class="popup__provenance">
      ${escapeHtml(provenanceLabel)} · verificado ${escapeHtml(provenance.verifiedAt ?? "—")}
    </p>
  `;
}

// ---------------------------------------------------------------------------
// 6. Filtros y emparejamiento
//
// Este es el punto que conviene entender bien: el resultado de la búsqueda
// inteligente entra por aquí exactamente igual que un clic en un filtro. La IA
// no tiene una vía privilegiada.
// ---------------------------------------------------------------------------

function buildSignalFilters() {
  dom.signalFilters.innerHTML = "";

  for (const signal of CONFIG.allowedSignals) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.dataset.signal = signal;
    chip.textContent = signalLabel(signal);
    chip.setAttribute("aria-pressed", "false");
    chip.addEventListener("click", () => toggleSignal(signal, chip));
    dom.signalFilters.append(chip);
  }
}

function toggleSignal(signal, chip) {
  if (state.activeSignals.has(signal)) {
    state.activeSignals.delete(signal);
    chip.setAttribute("aria-pressed", "false");
  } else {
    state.activeSignals.add(signal);
    chip.setAttribute("aria-pressed", "true");
  }
  applyFilters();
}

function clearAllFilters() {
  state.activeSignals.clear();
  state.activeCategory = null;
  dom.searchResult.textContent = "";
  for (const chip of dom.signalFilters.children) {
    chip.setAttribute("aria-pressed", "false");
  }
  applyFilters();
}

/**
 * Decide qué lugares se muestran y en qué orden.
 *
 * Un lugar entra si coincide con la categoría pedida (cuando hay una) y con al
 * menos una de las señales activas. Se ordena por cuántas señales cumple, así
 * que los que cumplen todas quedan arriba y nunca acabas con una lista vacía
 * por pedir una señal de más.
 */
function applyFilters() {
  const wanted = [...state.activeSignals];

  const matches = state.places
    .filter((place) => !state.activeCategory || place.category === state.activeCategory)
    .map((place) => ({
      place,
      score: wanted.filter((signal) => place.signals.includes(signal)).length,
    }))
    .filter(({ score }) => wanted.length === 0 || score > 0)
    .sort((a, b) => b.score - a.score || a.place.name.localeCompare(b.place.name, "es"));

  renderResults(matches, wanted);
  updateMarkerVisibility(new Set(matches.map(({ place }) => place.id)));
}

function renderResults(matches, wanted) {
  dom.resultsCounter.textContent =
    matches.length === state.places.length
      ? `${matches.length}`
      : `${matches.length} de ${state.places.length}`;

  if (matches.length === 0) {
    dom.results.innerHTML =
      '<li class="empty">Ningún espacio coincide todavía. Prueba con menos señales.</li>';
    return;
  }

  dom.results.innerHTML = "";

  for (const { place, score } of matches) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "result";
    button.setAttribute("aria-current", String(place.id === state.selectedId));

    const signals = place.signals
      .map((signal) => {
        const matched = wanted.includes(signal) ? " tag--match" : "";
        return `<span class="tag${matched}">${escapeHtml(signalLabel(signal))}</span>`;
      })
      .join("");

    const scoreNote =
      wanted.length > 1 ? ` · coincide en ${score} de ${wanted.length}` : "";

    button.innerHTML = `
      <div class="result__name">${escapeHtml(place.name)}</div>
      <div class="result__meta">${escapeHtml(categoryLabel(place.category))}${escapeHtml(scoreNote)}</div>
      <div class="result__signals">${signals}</div>
    `;
    button.addEventListener("click", () => selectPlace(place.id));

    item.append(button);
    dom.results.append(item);
  }
}

function updateMarkerVisibility(visibleIds) {
  for (const [id, marker] of state.markers) {
    marker.getElement().classList.toggle("marker--dim", !visibleIds.has(id));
  }
}

function selectPlace(placeId) {
  state.selectedId = placeId;
  const place = state.places.find((candidate) => candidate.id === placeId);
  if (!place) return;

  map.flyTo({ center: [place.longitude, place.latitude], zoom: 15.5 });
  state.markers.get(placeId)?.togglePopup();
  applyFilters();
}

// ---------------------------------------------------------------------------
// 7. Búsqueda inteligente — POST /search
// ---------------------------------------------------------------------------

async function handleSearch(event) {
  event.preventDefault();

  const query = dom.searchInput.value.trim();
  if (!query) return;

  dom.searchButton.disabled = true;
  dom.searchResult.textContent = "Interpretando…";

  try {
    const data = await callApi("/search", { method: "POST", body: { query } });
    applyCriteria(data.criteria);
    dom.searchResult.innerHTML = describeCriteria(data.criteria, data.source);
  } catch (error) {
    console.error(error);
    dom.searchResult.textContent = "No se pudo interpretar la búsqueda. Usa los filtros de abajo.";
  } finally {
    dom.searchButton.disabled = false;
  }
}

/** Traduce los criterios que devuelve la API al estado de los filtros. */
function applyCriteria(criteria) {
  state.activeCategory = criteria.category;
  state.activeSignals = new Set(criteria.signals);

  for (const chip of dom.signalFilters.children) {
    chip.setAttribute("aria-pressed", String(state.activeSignals.has(chip.dataset.signal)));
  }

  applyFilters();
}

function describeCriteria(criteria, source) {
  const parts = [];
  if (criteria.category) parts.push(`categoría <strong>${escapeHtml(categoryLabel(criteria.category))}</strong>`);
  if (criteria.signals.length > 0) {
    parts.push(`señales <strong>${escapeHtml(criteria.signals.map(signalLabel).join(", "))}</strong>`);
  }

  const tag =
    source === "bedrock"
      ? '<span class="source-tag source-tag--bedrock">Bedrock</span>'
      : '<span class="source-tag source-tag--fallback">Plan B</span>';

  const summary =
    parts.length > 0
      ? `Entendí: ${parts.join(" y ")}.`
      : "No detecté criterios claros. Prueba a mencionar el tipo de lugar o una señal.";

  return summary + tag;
}

// ---------------------------------------------------------------------------
// 8. Alta de espacios — POST /places
// ---------------------------------------------------------------------------

function buildFormControls() {
  dom.categorySelect.innerHTML = "";
  for (const category of CONFIG.allowedCategories) {
    const option = document.createElement("option");
    option.value = category;
    option.textContent = categoryLabel(category);
    dom.categorySelect.append(option);
  }

  dom.formSignals.innerHTML = "";
  for (const signal of CONFIG.allowedSignals) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.dataset.signal = signal;
    chip.textContent = signalLabel(signal);
    chip.setAttribute("aria-pressed", "false");
    chip.addEventListener("click", () => {
      const pressed = chip.getAttribute("aria-pressed") === "true";
      chip.setAttribute("aria-pressed", String(!pressed));
    });
    dom.formSignals.append(chip);
  }
}

async function handleCreatePlace(event) {
  event.preventDefault();
  dom.formError.textContent = "";
  dom.submitForm.disabled = true;

  const form = dom.placeForm;
  const payload = {
    name: form.name.value.trim(),
    category: form.category.value,
    address: form.address.value.trim(),
    latitude: Number(form.latitude.value),
    longitude: Number(form.longitude.value),
    communityNote: form.communityNote.value.trim(),
    signals: [...dom.formSignals.children]
      .filter((chip) => chip.getAttribute("aria-pressed") === "true")
      .map((chip) => chip.dataset.signal),
  };

  try {
    const data = await callApi("/places", { method: "POST", body: payload });

    // El servidor devuelve el lugar ya normalizado, con su id y su procedencia.
    state.places.push(data.place);
    renderMarkers();
    applyFilters();

    form.reset();
    for (const chip of dom.formSignals.children) chip.setAttribute("aria-pressed", "false");
    dom.dialog.close();
    selectPlace(data.place.id);
  } catch (error) {
    dom.formError.textContent = error.message;
  } finally {
    dom.submitForm.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// 9. Utilidades
// ---------------------------------------------------------------------------

/** Envoltura única sobre fetch para las tres rutas del HTTP API. */
async function callApi(path, { method = "GET", body } = {}) {
  const response = await fetch(CONFIG.apiUrl + path, {
    method,
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });

  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    // Las Lambdas devuelven {"message": ..., "errors": [...]} cuando rechazan.
    const detail = data.errors?.join(" ") ?? data.message ?? `Error ${response.status}`;
    throw new Error(detail);
  }

  return data;
}

function signalLabel(signal) {
  return SIGNAL_LABELS[signal] ?? signal;
}

function categoryLabel(category) {
  return CATEGORY_LABELS[category] ?? category;
}

/** Nunca insertamos texto de la API en el DOM sin escaparlo. */
function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = String(value ?? "");
  return element.innerHTML;
}

init();
