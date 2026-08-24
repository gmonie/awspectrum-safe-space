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
  // Output ApiUrl del stack.
  apiUrl: "https://abc123xyz.execute-api.us-east-1.amazonaws.com",

  // Región donde vive todo. Se usa para construir la URL del mapa.
  region: "us-east-1",

  // Valor de la API key de Amazon Location, obtenido con:
  //   aws location describe-key --key-name safe-space-maps-key --query Key
  mapsApiKey: "v1.public.EJEMPLO-NO-ES-UNA-KEY-REAL",

  // Taxonomía vigente, tal como la declara template.yaml. El frontend dibuja
  // los filtros y el formulario a partir de estas tres listas.
  allowedSignals: [
    "lgbtq_affirming",
    "free",
    "open_24_7",
    "contact_only",
  ],
  allowedCategories: [
    "organization",
    "support_service",
    "community_center",
    "shelter_referral",
  ],
  allowedServices: [
    "psychological_support",
    "legal_support",
    "healthcare",
    "referral",
    "community_network",
    "shelter_support",
  ],
};
