/* ---------------------------------------------------------------------------
   Safe Space — lógica del directorio
   ---------------------------------------------------------------------------

   Aquí es donde el navegador toca AWS. Hay exactamente cuatro puntos de
   contacto y vale la pena tenerlos localizados:

     1. Amazon Location    ->  el estilo del mapa que carga MapLibre
     2. GET  /resources    ->  las fichas del directorio
     3. POST /search       ->  Amazon Bedrock interpreta lo que escribiste
     4. POST /resources    ->  proponer un recurso, que queda pendiente

   Dos ideas importantes:

   - El emparejamiento entre los criterios que devuelve Bedrock y los recursos
     NO ocurre en la nube. Ocurre en `applyFilters()`, unas líneas más abajo.
     El modelo interpreta; este código decide.
   - El mapa es una vista parcial del directorio, no el directorio. Solo se
     dibuja un pin cuando el recurso trae una ubicación pública; una línea
     telefónica o un refugio existen en la lista sin aparecer en el mapa.
--------------------------------------------------------------------------- */

"use strict";

// ---------------------------------------------------------------------------
// 1. Configuración
// ---------------------------------------------------------------------------

// config.js lo genera scripts/publish-frontend.sh con los Outputs de tu stack.
const CONFIG = window.SAFE_SPACE_CONFIG;

const MAP_CENTER = [-99.15, 19.42]; // Ciudad de México [longitud, latitud]
const MAP_ZOOM = 12.4;

// Etiquetas legibles. Si añades una señal en template.yaml y no la añades
// aquí, la interfaz mostrará su identificador tal cual en vez de romperse.
const SIGNAL_LABELS = {
  lgbtq_affirming: "🌈 Atención LGBTQ+",
  trans_inclusive: "🏳️‍⚧️ Inclusivo para personas trans",
  free: "🫶 Gratuito",
  open_24_7: "🕒 24/7",
  contact_only: "☎️ Contacto / derivación",
};

const CATEGORY_LABELS = {
  organization: "Organización",
  support_service: "Servicio de apoyo",
  community_center: "Centro comunitario",
  shelter_referral: "Derivación a refugio",
};

const SERVICE_LABELS = {
  psychological_support: "Apoyo psicológico",
  legal_support: "Apoyo legal",
  healthcare: "Salud",
  referral: "Canalización",
  community_network: "Red comunitaria",
  shelter_support: "Refugio",
};

const PROVENANCE_LABELS = {
  direct_source: "Fuente directa",
  community_submission: "Pendiente de revisión",
};

// ---------------------------------------------------------------------------
// 2. Estado
// ---------------------------------------------------------------------------

const state = {
  resources: [],
  markers: new Map(), // id del recurso -> maplibregl.Marker (solo los que tienen pin)
  activeSignals: new Set(),
  activeServices: new Set(),
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
  serviceFilters: document.getElementById("service-filters"),
  clearFilters: document.getElementById("clear-filters"),
  results: document.getElementById("results"),
  resultsCounter: document.getElementById("results-counter"),
  mapError: document.getElementById("map-error"),
  openForm: document.getElementById("open-form"),
  dialog: document.getElementById("resource-dialog"),
  resourceForm: document.getElementById("resource-form"),
  categorySelect: document.getElementById("category-select"),
  formServices: document.getElementById("form-services"),
  formSignals: document.getElementById("form-signals"),
  locationFields: document.getElementById("location-fields"),
  locationHint: document.getElementById("location-hint"),
  formError: document.getElementById("form-error"),
  formSuccess: document.getElementById("form-success"),
  submitForm: document.getElementById("submit-form"),
  cancelForm: document.getElementById("cancel-form"),
};

// ---------------------------------------------------------------------------
// 3. Arranque
// ---------------------------------------------------------------------------

function init() {
  if (!CONFIG) {
    showMapError(
      "Falta frontend/config.js. Ejecuta ./scripts/publish-frontend.sh para generarlo con los datos de tu stack.",
    );
    return;
  }

  buildSignalFilters();
  buildServiceFilters();
  buildFormControls();
  initMap();
  loadResources();

  dom.searchForm.addEventListener("submit", handleSearch);
  dom.clearFilters.addEventListener("click", clearAllFilters);
  dom.openForm.addEventListener("click", openResourceDialog);
  dom.cancelForm.addEventListener("click", () => dom.dialog.close());
  dom.categorySelect.addEventListener("change", updateLocationFields);
  dom.resourceForm.addEventListener("submit", handleCreateResource);
}

function openResourceDialog() {
  dom.formError.textContent = "";
  dom.formSuccess.textContent = "";
  dom.dialog.showModal();
  updateLocationFields();
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
      "El mapa no cargó. Revisa que la API key de Amazon Location sea válida y que config.js apunte a tu región.",
    );
  });

  // Comodidad para el formulario: un clic en el mapa rellena las coordenadas.
  // Salvo cuando están desactivadas, que es lo que ocurre al elegir una
  // derivación a refugio: ahí el clic no debe poder escribir una ubicación.
  map.on("click", (event) => {
    const latitude = dom.resourceForm.elements.latitude;
    const longitude = dom.resourceForm.elements.longitude;
    if (latitude.disabled || longitude.disabled) return;
    latitude.value = event.lngLat.lat.toFixed(5);
    longitude.value = event.lngLat.lng.toFixed(5);
  });
}

function showMapError(message) {
  dom.mapError.textContent = message;
  dom.mapError.hidden = false;
}

// ---------------------------------------------------------------------------
// 5. Datos — GET /resources
// ---------------------------------------------------------------------------

async function loadResources() {
  try {
    const data = await callApi("/resources");
    state.resources = data.resources;
    renderMarkers();
    applyFilters();
  } catch (error) {
    console.error(error);
    dom.results.innerHTML =
      '<li class="empty">No se pudieron cargar los recursos. ¿Ejecutaste scripts/seed.py?</li>';
  }
}

/**
 * ¿Este recurso se puede dibujar en el mapa?
 *
 * Que la respuesta sea "no" es normal, no un error: significa que el recurso
 * funciona por teléfono o que su dirección está protegida a propósito.
 *
 * Se descarta `null` explícitamente porque `Number(null)` es 0, y un 0 sí es
 * finito: sin esa comprobación, una ficha con la latitud a null acabaría con
 * un pin en mitad del Golfo de Guinea.
 */
function hasPublicLocation(resource) {
  return [resource.latitude, resource.longitude].every(
    (value) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)),
  );
}

function renderMarkers() {
  state.markers.forEach((marker) => marker.remove());
  state.markers.clear();

  for (const resource of state.resources) {
    if (!hasPublicLocation(resource)) continue;

    const element = document.createElement("div");
    element.className = "marker";
    element.title = resource.name;

    const marker = new maplibregl.Marker({ element })
      .setLngLat([Number(resource.longitude), Number(resource.latitude)])
      .setPopup(new maplibregl.Popup({ offset: 16 }).setHTML(popupHtml(resource)))
      .addTo(map);

    element.addEventListener("click", () => selectResource(resource.id));
    state.markers.set(resource.id, marker);
  }
}

function popupHtml(resource) {
  const serviceTags = (resource.services ?? [])
    .map((service) => `<span class="tag">${escapeHtml(serviceLabel(service))}</span>`)
    .join("");
  const signalTags = (resource.signals ?? [])
    .map((signal) => `<span class="tag">${escapeHtml(signalLabel(signal))}</span>`)
    .join("");
  const provenance = resource.provenance ?? {};
  const sourceLink = safeHref(provenance.sourceUrl)
    ? `<a class="popup__source" href="${escapeHtml(provenance.sourceUrl)}" target="_blank" rel="noreferrer">Ver fuente directa</a>`
    : "";

  return `
    <div class="popup__name">${escapeHtml(resource.name)}</div>
    <div class="result__meta">${escapeHtml(categoryLabel(resource.category))} · ${escapeHtml(resource.address ?? resource.serviceArea ?? "Contacto")}</div>
    ${resource.description ? `<p class="popup__note">${escapeHtml(resource.description)}</p>` : ""}
    ${resource.contact ? `<p class="popup__contact">${escapeHtml(contactText(resource.contact))}</p>` : ""}
    <div class="result__signals">${serviceTags}${signalTags}</div>
    <p class="popup__provenance">${escapeHtml(provenanceLabel(provenance))}${provenance.checkedAt ? ` · revisado ${escapeHtml(provenance.checkedAt)}` : ""} ${sourceLink}</p>
  `;
}

// ---------------------------------------------------------------------------
// 6. Filtros y emparejamiento
//
// Este es el punto que conviene entender bien: el resultado de la búsqueda
// inteligente entra por aquí exactamente igual que un clic en un filtro. La IA
// no tiene una vía privilegiada.
//
// Los filtros se generan desde CONFIG, que sale de los Outputs del stack, que
// salen de template.yaml. Añadir un servicio nuevo es editar una línea de la
// plantilla y volver a desplegar: la interfaz aparece sola.
// ---------------------------------------------------------------------------

function buildSignalFilters() {
  dom.signalFilters.innerHTML = "";
  for (const signal of CONFIG.allowedSignals ?? []) {
    const chip = createFilterChip(signalLabel(signal), () => toggleSignal(signal, chip));
    chip.dataset.signal = signal;
    dom.signalFilters.append(chip);
  }
}

function buildServiceFilters() {
  dom.serviceFilters.innerHTML = "";
  for (const service of CONFIG.allowedServices ?? []) {
    const chip = createFilterChip(serviceLabel(service), () => toggleService(service, chip));
    chip.dataset.service = service;
    dom.serviceFilters.append(chip);
  }
}

function createFilterChip(label, onClick) {
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "chip";
  chip.textContent = label;
  chip.setAttribute("aria-pressed", "false");
  chip.addEventListener("click", onClick);
  return chip;
}

function toggleSignal(signal, chip) {
  toggleSet(state.activeSignals, signal, chip);
  applyFilters();
}

function toggleService(service, chip) {
  toggleSet(state.activeServices, service, chip);
  applyFilters();
}

function toggleSet(set, value, chip) {
  const active = set.has(value);
  if (active) set.delete(value);
  else set.add(value);
  chip.setAttribute("aria-pressed", String(!active));
}

function clearAllFilters() {
  state.activeSignals.clear();
  state.activeServices.clear();
  state.activeCategory = null;
  dom.searchResult.textContent = "";
  for (const chip of [...dom.signalFilters.children, ...dom.serviceFilters.children]) {
    chip.setAttribute("aria-pressed", "false");
  }
  applyFilters();
}

/**
 * Decide qué recursos se muestran y en qué orden.
 *
 * Un recurso entra si coincide con la categoría pedida (cuando hay una) y con
 * al menos uno de los servicios o señales activos. Se ordena por cuántos
 * cumple, así que los que cumplen todo quedan arriba y nunca acabas con una
 * lista vacía por pedir un filtro de más.
 *
 * En un directorio de apoyo esa diferencia importa: exigir la coincidencia
 * completa devolvería cero resultados a quien pide "psicólogo trans gratuito",
 * en vez de enseñarle los que cumplen dos de las tres cosas.
 */
function applyFilters() {
  const wantedSignals = [...state.activeSignals];
  const wantedServices = [...state.activeServices];
  const wanted = [...wantedSignals, ...wantedServices];

  const matches = state.resources
    .filter((resource) => !state.activeCategory || resource.category === state.activeCategory)
    .map((resource) => ({
      resource,
      score:
        wantedSignals.filter((signal) => (resource.signals ?? []).includes(signal)).length +
        wantedServices.filter((service) => (resource.services ?? []).includes(service)).length,
    }))
    .filter(({ score }) => wanted.length === 0 || score > 0)
    .sort((a, b) => b.score - a.score || a.resource.name.localeCompare(b.resource.name, "es"));

  renderResults(matches, wanted);
  updateMarkerVisibility(new Set(matches.map(({ resource }) => resource.id)));
}

function renderResults(matches, wanted) {
  dom.resultsCounter.textContent =
    matches.length === state.resources.length ? `${matches.length}` : `${matches.length} de ${state.resources.length}`;

  if (matches.length === 0) {
    dom.results.innerHTML =
      '<li class="empty">Ningún recurso coincide todavía. Prueba con menos filtros.</li>';
    return;
  }

  dom.results.innerHTML = "";
  for (const { resource, score } of matches) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "result";
    button.setAttribute("aria-current", String(resource.id === state.selectedId));

    const tags = [...(resource.services ?? []), ...(resource.signals ?? [])]
      .map((tag) => {
        const matched = wanted.includes(tag) ? " tag--match" : "";
        return `<span class="tag${matched}">${escapeHtml(tagLabel(tag))}</span>`;
      })
      .join("");
    const scoreNote = wanted.length > 1 ? ` · coincide en ${score} de ${wanted.length}` : "";
    const locationNote = hasPublicLocation(resource) ? "📍 ubicación pública" : "☎️ contacto / derivación";

    button.innerHTML = `
      <div class="result__name">${escapeHtml(resource.name)}</div>
      <div class="result__meta">${escapeHtml(categoryLabel(resource.category))} · ${escapeHtml(locationNote)}${escapeHtml(scoreNote)}</div>
      ${resource.serviceArea ? `<div class="result__meta">${escapeHtml(resource.serviceArea)}</div>` : ""}
      <div class="result__signals">${tags}</div>
      ${resource.contact ? `<div class="result__contact">${escapeHtml(contactText(resource.contact))}</div>` : ""}
      <div class="result__provenance">${escapeHtml(provenanceLabel(resource.provenance ?? {}))}${resource.provenance?.checkedAt ? ` · ${escapeHtml(resource.provenance.checkedAt)}` : ""}</div>
    `;
    button.addEventListener("click", () => selectResource(resource.id));
    item.append(button);
    dom.results.append(item);
  }
}

function updateMarkerVisibility(visibleIds) {
  for (const [id, marker] of state.markers) {
    marker.getElement().classList.toggle("marker--dim", !visibleIds.has(id));
  }
}

function selectResource(resourceId) {
  state.selectedId = resourceId;
  const resource = state.resources.find((candidate) => candidate.id === resourceId);
  if (!resource) return;

  // Un recurso sin ubicación pública también se puede seleccionar: se marca en
  // la lista y el mapa simplemente no se mueve. Volar a una coordenada
  // inventada sería peor que no volar a ninguna.
  if (hasPublicLocation(resource)) {
    map.flyTo({ center: [Number(resource.longitude), Number(resource.latitude)], zoom: 15.5 });
    state.markers.get(resourceId)?.togglePopup();
  }
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
  state.activeServices = new Set(criteria.services ?? []);
  state.activeSignals = new Set(criteria.signals ?? []);

  for (const chip of dom.signalFilters.children) {
    chip.setAttribute("aria-pressed", String(state.activeSignals.has(chip.dataset.signal)));
  }
  for (const chip of dom.serviceFilters.children) {
    chip.setAttribute("aria-pressed", String(state.activeServices.has(chip.dataset.service)));
  }
  applyFilters();
}

function describeCriteria(criteria, source) {
  const parts = [];
  if (criteria.category) parts.push(`tipo <strong>${escapeHtml(categoryLabel(criteria.category))}</strong>`);
  if ((criteria.services ?? []).length > 0) {
    parts.push(`servicios <strong>${escapeHtml(criteria.services.map(serviceLabel).join(", "))}</strong>`);
  }
  if ((criteria.signals ?? []).length > 0) {
    parts.push(`señales <strong>${escapeHtml(criteria.signals.map(signalLabel).join(", "))}</strong>`);
  }

  const tag =
    source === "bedrock"
      ? '<span class="source-tag source-tag--bedrock">Bedrock</span>'
      : '<span class="source-tag source-tag--fallback">Plan B</span>';
  const summary = parts.length > 0 ? `Entendí: ${parts.join(" y ")}.` : "No detecté criterios claros. Prueba con un servicio o necesidad.";
  return summary + tag;
}

// ---------------------------------------------------------------------------
// 8. Propuestas — POST /resources
// ---------------------------------------------------------------------------

function buildFormControls() {
  dom.categorySelect.innerHTML = "";
  for (const category of CONFIG.allowedCategories ?? []) {
    const option = document.createElement("option");
    option.value = category;
    option.textContent = categoryLabel(category);
    dom.categorySelect.append(option);
  }

  buildFormChips(dom.formServices, CONFIG.allowedServices ?? [], serviceLabel);
  buildFormChips(dom.formSignals, CONFIG.allowedSignals ?? [], signalLabel);
}

function buildFormChips(container, values, labeler) {
  container.innerHTML = "";
  for (const value of values) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.dataset.value = value;
    chip.textContent = labeler(value);
    chip.setAttribute("aria-pressed", "false");
    chip.addEventListener("click", () => {
      const pressed = chip.getAttribute("aria-pressed") === "true";
      chip.setAttribute("aria-pressed", String(!pressed));
    });
    container.append(chip);
  }
}

/**
 * Desactiva dirección y coordenadas cuando la categoría es un refugio.
 *
 * Esto es comodidad y pedagogía, no seguridad: la regla de verdad vive en la
 * Lambda, que rechaza el POST con un 400 aunque alguien lo mande con `curl`
 * saltándose esta pantalla.
 */
function updateLocationFields() {
  const shelter = dom.categorySelect.value === "shelter_referral";
  const latitude = dom.resourceForm.elements.latitude;
  const longitude = dom.resourceForm.elements.longitude;
  const address = dom.resourceForm.elements.address;
  latitude.disabled = shelter;
  longitude.disabled = shelter;
  address.disabled = shelter;
  if (shelter) {
    latitude.value = "";
    longitude.value = "";
    address.value = "";
    dom.locationHint.textContent = "Las derivaciones a refugios no guardan dirección ni coordenadas. Comparte solo un canal de contacto.";
  } else {
    dom.locationHint.textContent = "Si existe una ubicación pública, haz clic en el mapa para rellenar las coordenadas. Nunca publiques la dirección de un refugio.";
  }
}

async function handleCreateResource(event) {
  event.preventDefault();
  dom.formError.textContent = "";
  dom.formSuccess.textContent = "";
  dom.submitForm.disabled = true;

  // Leemos con FormData en vez de form.<campo>: un input llamado "name"
  // chocaría con la propiedad `name` del propio elemento <form>.
  const fields = new FormData(dom.resourceForm);
  const payload = {
    name: String(fields.get("name") ?? "").trim(),
    category: fields.get("category"),
    description: String(fields.get("description") ?? "").trim(),
    address: String(fields.get("address") ?? "").trim(),
    serviceArea: String(fields.get("serviceArea") ?? "").trim(),
    services: selectedValues(dom.formServices),
    signals: selectedValues(dom.formSignals),
    contact: compactObject({
      phone: String(fields.get("phone") ?? "").trim(),
      email: String(fields.get("email") ?? "").trim(),
      website: String(fields.get("website") ?? "").trim(),
    }),
    sourceUrl: String(fields.get("sourceUrl") ?? "").trim(),
  };

  // Las coordenadas solo viajan si se escribieron. Mandar `latitude: 0` porque
  // el campo estaba vacío es exactamente el error que la Lambda no puede
  // distinguir de una coordenada legítima.
  const latitude = String(fields.get("latitude") ?? "").trim();
  const longitude = String(fields.get("longitude") ?? "").trim();
  if (latitude) payload.latitude = Number(latitude);
  if (longitude) payload.longitude = Number(longitude);

  try {
    // La respuesta es 202, no 201: el recurso existe en la tabla pero no en el
    // directorio, así que no se añade a `state.resources` ni se pinta un pin.
    const data = await callApi("/resources", { method: "POST", body: payload });
    dom.formSuccess.textContent = data.message ?? "La propuesta quedó pendiente de revisión.";
    dom.resourceForm.reset();
    resetFormChips();
    updateLocationFields();
    window.setTimeout(() => dom.dialog.close(), 1800);
  } catch (error) {
    dom.formError.textContent = error.message;
  } finally {
    dom.submitForm.disabled = false;
  }
}

function selectedValues(container) {
  return [...container.children]
    .filter((chip) => chip.getAttribute("aria-pressed") === "true")
    .map((chip) => chip.dataset.value);
}

function resetFormChips() {
  for (const chip of [...dom.formServices.children, ...dom.formSignals.children]) {
    chip.setAttribute("aria-pressed", "false");
  }
}

function compactObject(value) {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item));
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

  // El `.catch()` no sobra: nuestras Lambdas siempre responden JSON, pero un
  // error que nunca llega a la Lambda —throttling de API Gateway, un fallo de
  // integración— puede traer un cuerpo vacío o que no sea JSON. Sin esta red,
  // la persona vería un SyntaxError del parser en vez del error de verdad.
  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    // Las Lambdas devuelven {"message": ..., "errors": [...]} cuando rechazan.
    const detail = data.errors?.join(" ") ?? data.message ?? `Error ${response.status}`;
    throw new Error(detail);
  }
  return data;
}

function categoryLabel(category) {
  return CATEGORY_LABELS[category] ?? category;
}

function serviceLabel(service) {
  return SERVICE_LABELS[service] ?? service;
}

function signalLabel(signal) {
  return SIGNAL_LABELS[signal] ?? signal;
}

function tagLabel(tag) {
  return SERVICE_LABELS[tag] ?? SIGNAL_LABELS[tag] ?? tag;
}

function provenanceLabel(provenance) {
  return PROVENANCE_LABELS[provenance.type] ?? provenance.type ?? "Sin procedencia";
}

function contactText(contact) {
  return [contact.phone, contact.email, contact.website].filter(Boolean).join(" · ");
}

/**
 * Solo deja pasar http(s) al `href` de la fuente.
 *
 * Un `javascript:` en ese atributo se ejecutaría al pulsar el enlace, y el
 * texto de la ficha viene de la base de datos. Escapar el valor no basta: hay
 * que comprobar también el esquema.
 */
function safeHref(value) {
  return typeof value === "string" && /^https?:\/\//i.test(value) ? value : "";
}

/**
 * Nunca insertamos texto de la API en el DOM sin escaparlo.
 *
 * Se escapan también las comillas porque parte de este texto acaba dentro de
 * un atributo HTML, no solo entre etiquetas.
 */
function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

init();
