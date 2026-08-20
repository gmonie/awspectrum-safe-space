/* ---------------------------------------------------------------------------
   Ejemplo de la configuración que necesita el frontend.

   NO edites este archivo ni lo copies a mano. El archivo real, config.js, lo
   genera scripts/publish-frontend.sh leyendo los Outputs de tu stack y el
   valor de la API key de Amazon Location.

       ./scripts/publish-frontend.sh

   config.js está en .gitignore porque contiene una credencial de tu cuenta.
   Este archivo existe solo para que sepas qué forma tiene.
--------------------------------------------------------------------------- */

window.SAFE_SPACE_CONFIG = {
  // Output ApiUrl de la stack.
  apiUrl: "https://abc123xyz.execute-api.us-east-1.amazonaws.com",

  // Región donde vive todo. Se usa para construir la URL del mapa.
  region: "us-east-1",

  // Valor de la API key de Amazon Location, obtenido con:
  //   aws location describe-key --key-name safe-space-maps-key --query Key
  mapsApiKey: "v1.public.EJEMPLO-NO-ES-UNA-KEY-REAL",

  // Taxonomía vigente, tal como la declara template.yaml. El frontend dibuja
  // los filtros y el formulario a partir de estas dos listas.
  allowedSignals: [
    "lgbtq_space",
    "neutral_bathroom",
    "accessible",
    "pronouns_respected",
    "couples_friendly",
    "quiet",
    "inclusive_healthcare",
  ],
  allowedCategories: [
    "cafe",
    "restaurant",
    "bar",
    "bookstore",
    "clinic",
    "community_center",
    "museum",
    "park",
    "coworking",
    "shop",
  ],
};
