"""Búsqueda de recursos en lenguaje natural.

Atiende una sola ruta del HTTP API:

    POST /search  -> convierte una frase en criterios de búsqueda

Qué hace exactamente la IA
--------------------------
Amazon Bedrock traduce "necesito apoyo psicológico y no puedo pagarlo" a:

    {"category": null, "services": ["psychological_support"], "signals": ["free"]}

Y ahí termina su trabajo. **El modelo no elige recursos y no consulta la base de
datos.** Esta función valida su respuesta contra la taxonomía que declara
template.yaml, y el navegador hace el emparejamiento sobre los recursos que ya
descargó con `GET /resources`. Así garantizamos que Safe Space solo puede
devolver recursos que existen de verdad en la tabla, con su fuente y su fecha.

En un directorio de apoyo esto no es una sutileza técnica. Un modelo que
inventara un teléfono de refugio con la misma seguridad con la que escribe
cualquier otra frase mandaría a alguien a un número que no existe.

Si Bedrock no está disponible, la función degrada a una extracción por palabras
clave y lo indica en el campo `source`. El workshop nunca se queda bloqueado.
"""

import json
import logging
import os
import re
import unicodedata
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MODEL_ID = os.environ["BEDROCK_MODEL_ID"]

# La taxonomía llega desde template.yaml, igual que en la función de recursos.
ALLOWED_SIGNALS = tuple(filter(None, os.environ["ALLOWED_SIGNALS"].split(",")))
ALLOWED_CATEGORIES = tuple(filter(None, os.environ["ALLOWED_CATEGORIES"].split(",")))
ALLOWED_SERVICES = tuple(filter(None, os.environ["ALLOWED_SERVICES"].split(",")))

MAX_QUERY_LENGTH = 300

# Tiempos cortos y un solo reintento: preferimos caer al plan B rápido antes
# que agotar los 10 s de la Lambda esperando a Bedrock.
BEDROCK = boto3.client(
    "bedrock-runtime",
    config=Config(
        connect_timeout=3,
        read_timeout=6,
        retries={"max_attempts": 2, "mode": "standard"},
    ),
)

SYSTEM_PROMPT = f"""Eres un extractor de criterios para Safe Space, un directorio de recursos inclusivos en Ciudad de México.

Recibes una frase de una persona y devuelves ÚNICAMENTE un objeto JSON con esta forma exacta:

{{"category": <una de {list(ALLOWED_CATEGORIES)} o null, "services": [<subconjunto de {list(ALLOWED_SERVICES)}>], "signals": [<subconjunto de {list(ALLOWED_SIGNALS)}>]}}

Reglas:
- No escribas nada fuera del JSON: ni explicaciones, ni bloques de código.
- No inventes categorías, servicios ni señales que no estén en las listas.
- No inventes nombres, direcciones, teléfonos ni organizaciones. No es tu tarea.
- Si no puedes deducir la categoría con confianza, usa null.
- Si no detectas servicios o señales, usa listas vacías.
- “Refugio” debe orientar a shelter_referral; nunca implica que debas revelar una ubicación.
"""

# Plan B determinista. Se usa cuando Bedrock falla y también sirve para explicar
# en el workshop qué parte del sistema es IA y qué parte no lo es: esta tabla
# entiende "psicólogo", pero no entiende "llevo semanas sin poder dormir".
SERVICE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "psychological_support": ("psicolog", "psicoemocional", "salud mental", "terapia", "psiquiatr"),
    "legal_support": ("legal", "jurid", "abogada", "abogado", "discriminacion", "derechos"),
    "healthcare": ("salud", "clinica", "medic", "vih", "its", "hormonal"),
    "referral": ("canaliza", "orienta", "linea", "línea", "referencia", "derivacion"),
    "community_network": ("red de apoyo", "comunidad", "colectivo", "acompanamiento", "acompañamiento"),
    "shelter_support": ("refugio", "albergue", "casa segura", "resguardo", "alojamiento"),
}

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "shelter_referral": ("refugio", "albergue", "casa segura", "resguardo", "alojamiento"),
    "support_service": ("apoyo", "asistencia", "linea", "línea", "clinica", "clínica", "servicio"),
    "organization": ("organizacion", "organización", "asociacion", "asociación", "fundacion", "fundación", "copred", "unadis"),
    "community_center": ("centro comunitario", "centro cultural", "comunidad", "taller", "colectivo"),
}

SIGNAL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "lgbtq_affirming": ("lgbt", "lgbtq", "queer", "gay", "lesbi", "diversidad", "disidencia"),
    "free": ("gratis", "gratuito", "gratuita", "sin costo", "público", "publico"),
    "open_24_7": ("24/7", "24 horas", "todo el día", "todo el dia"),
    "contact_only": ("por telefono", "por teléfono", "linea", "línea", "sin direccion", "sin dirección"),
}


def lambda_handler(event: dict, context: Any) -> dict:
    """Punto de entrada del HTTP API."""
    query = read_query(event.get("body"))
    if query is None:
        return response(400, {"message": "Envía un objeto JSON con el campo 'query'."})

    criteria, source = extract_criteria(query)
    logger.info("Búsqueda resuelta", extra={"source": source, "criteria": criteria})
    return response(200, {"query": query, "criteria": criteria, "source": source})


def read_query(raw_body: str | None) -> str | None:
    """Devuelve la frase de búsqueda, o None si la petición no es utilizable."""
    if not raw_body:
        return None
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    query = str(payload.get("query", "")).strip()
    return query[:MAX_QUERY_LENGTH] if query else None


def extract_criteria(query: str) -> tuple[dict, str]:
    """Convierte la frase en criterios validados.

    Devuelve los criterios y de dónde salieron: "bedrock" o "fallback". Ese
    segundo valor llega hasta la interfaz y se pinta como una etiqueta, para
    que se vea cuándo está respondiendo el modelo y cuándo la tabla.
    """
    try:
        raw_criteria = ask_bedrock(query)
    except (ClientError, BotoCoreError) as error:
        # Puede pasar por falta de permisos, cuota o un problema transitorio.
        # No es motivo para devolver un error a la persona usuaria.
        logger.warning("Bedrock no disponible, se usa el plan B", extra={"error": str(error)})
        return keyword_criteria(query), "fallback"

    criteria = validate_criteria(raw_criteria)
    if criteria is None:
        logger.warning("Respuesta del modelo no utilizable, se usa el plan B")
        return keyword_criteria(query), "fallback"

    return criteria, "bedrock"


def ask_bedrock(query: str) -> str:
    """Llama al modelo con la Converse API y devuelve su texto tal cual.

    Converse es la interfaz unificada de Bedrock: el mismo código sirve para
    cualquier modelo compatible, solo cambia el identificador.
    """
    result = BEDROCK.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": query}]}],
        # temperature=0 hace la salida lo más determinista posible, que es lo
        # que queremos en una tarea de clasificación.
        inferenceConfig={"temperature": 0, "maxTokens": 240},
    )

    usage = result.get("usage", {})
    logger.info(
        "Bedrock respondió",
        extra={
            "modelId": MODEL_ID,
            "inputTokens": usage.get("inputTokens"),
            "outputTokens": usage.get("outputTokens"),
        },
    )
    return result["output"]["message"]["content"][0]["text"]


def validate_criteria(model_text: str) -> dict | None:
    """Parsea la respuesta del modelo y la reduce a las tres allowlists.

    Aunque el prompt pida JSON, un modelo puede devolver otra cosa. Esta es la
    frontera de confianza: nada llega al frontend sin pasar por aquí.
    Devuelve None si la respuesta no es aprovechable.
    """
    # A veces el modelo envuelve el JSON en un bloque de código o añade una
    # frase antes. Nos quedamos con el primer objeto que encontremos.
    match = re.search(r"\{.*\}", model_text, re.DOTALL)
    if not match:
        return None

    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None

    category = parsed.get("category")
    if category not in ALLOWED_CATEGORIES:
        category = None

    services = stable_allowed_list(parsed.get("services"), ALLOWED_SERVICES)
    signals = stable_allowed_list(parsed.get("signals"), ALLOWED_SIGNALS)
    return {"category": category, "services": services, "signals": signals}


def stable_allowed_list(value: Any, allowed: tuple[str, ...]) -> list[str]:
    """Allowlist: lo que no reconocemos se descarta en silencio.

    Se recorre `allowed`, no lo que devolvió el modelo, por dos motivos: filtra
    y además fija el orden, así que la misma frase da siempre la misma
    respuesta aunque el modelo cambie el orden de la lista.
    """
    if not isinstance(value, list):
        return []
    return [candidate for candidate in allowed if candidate in value]


def keyword_criteria(query: str) -> dict:
    """Plan B determinista, sin IA ni red."""
    normalized = strip_accents(query.lower())

    services = [
        service
        for service in ALLOWED_SERVICES
        if any(keyword in normalized for keyword in SERVICE_KEYWORDS.get(service, ()))
    ]
    signals = [
        signal
        for signal in ALLOWED_SIGNALS
        if any(strip_accents(keyword) in normalized for keyword in SIGNAL_KEYWORDS.get(signal, ()))
    ]
    category = next(
        (
            candidate
            for candidate in ALLOWED_CATEGORIES
            if any(strip_accents(keyword) in normalized for keyword in CATEGORY_KEYWORDS.get(candidate, ()))
        ),
        None,
    )

    return {"category": category, "services": services, "signals": signals}


def strip_accents(text: str) -> str:
    """Quita los acentos para poder comparar sin sorpresas.

    "asesoría jurídica" -> "asesoria juridica"
    """
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


def response(status_code: int, body: dict) -> dict:
    """Da forma a la respuesta que espera el HTTP API.

    Las cabeceras de CORS las añade API Gateway a partir de la
    CorsConfiguration declarada en template.yaml. `no-store` va aquí por la
    misma razón que en la otra función: sin cabecera de frescura el navegador
    puede reutilizar la interpretación de una búsqueda anterior.
    """
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }
