"""Búsqueda en lenguaje natural.

Atiende una sola ruta del HTTP API:

    POST /search  -> convierte una frase en criterios de búsqueda

Qué hace exactamente la IA
--------------------------
Amazon Bedrock traduce "busco un café tranquilo con baño neutral" a:

    {"category": "cafe", "signals": ["quiet", "neutral_bathroom"]}

Y ahí termina su trabajo. **El modelo no elige lugares y no consulta la base de
datos.** Esta función valida su respuesta contra la taxonomía que declara
template.yaml, y el navegador hace el emparejamiento sobre los espacios que ya
descargó con `GET /places`. Así garantizamos que Safe Space solo puede devolver
lugares que existen de verdad en la tabla.

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

# La taxonomía llega desde template.yaml, igual que en la función de lugares.
ALLOWED_SIGNALS = tuple(os.environ["ALLOWED_SIGNALS"].split(","))
ALLOWED_CATEGORIES = tuple(os.environ["ALLOWED_CATEGORIES"].split(","))

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

SYSTEM_PROMPT = f"""Eres un extractor de criterios para Safe Space, un mapa de espacios inclusivos.

Recibes una frase de una persona que busca un lugar y devuelves ÚNICAMENTE un
objeto JSON con esta forma exacta:

{{"category": <una de {list(ALLOWED_CATEGORIES)} o null>, "signals": [<subconjunto de {list(ALLOWED_SIGNALS)}>]}}

Reglas:
- No escribas nada fuera del JSON: ni explicaciones, ni bloques de código.
- No inventes categorías ni señales que no estén en las listas.
- No inventes lugares, nombres ni direcciones. No es tu tarea.
- Si no puedes deducir la categoría con confianza, usa null.
- Si no detectas ninguna señal, usa una lista vacía."""

# Plan B determinista. Se usa cuando Bedrock falla y también sirve para explicar
# en el workshop qué parte del sistema es IA y qué parte no lo es.
SIGNAL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "neutral_bathroom": ("bano neutral", "bano neutro", "bano inclusivo", "bano sin genero", "bano mixto"),
    "accessible": ("accesible", "accesibilidad", "silla de ruedas", "rampa", "movilidad"),
    "pronouns_respected": ("pronombre", "nombre elegido", "nombre social", "trans", "no binari"),
    "couples_friendly": ("pareja", "novia", "novio", "cita", "romantic", "besar"),
    "quiet": ("tranquil", "silencios", "calmad", "relajad", "sin ruido"),
    "lgbtq_space": ("lgbt", "queer", "gay", "lesbi", "arcoiris", "pride", "diversidad"),
    "inclusive_healthcare": ("salud", "clinica", "medic", "hormonal", "atencion trans"),
}

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "cafe": ("cafe", "cafeteria", "capuchino"),
    "restaurant": ("restaurante", "comer", "comida", "cenar", "brunch"),
    "bar": ("bar", "copas", "antro", "cantina", "mezcal", "cerveza"),
    "bookstore": ("libreria", "libros", "leer"),
    "clinic": ("clinica", "consultorio", "medic"),
    "community_center": ("centro comunitario", "colectivo", "asociacion"),
    "museum": ("museo", "galeria", "exposicion"),
    "park": ("parque", "jardin", "aire libre"),
    "coworking": ("coworking", "trabajar", "oficina"),
    "shop": ("tienda", "comprar", "boutique"),
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
    if not query:
        return None
    return query[:MAX_QUERY_LENGTH]


def extract_criteria(query: str) -> tuple[dict, str]:
    """Convierte la frase en criterios validados.

    Devuelve los criterios y de dónde salieron: "bedrock" o "fallback".
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
        inferenceConfig={"temperature": 0, "maxTokens": 200},
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
    """Parsea y valida la respuesta del modelo.

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

    raw_signals = parsed.get("signals")
    if not isinstance(raw_signals, list):
        raw_signals = []

    # Allowlist: lo que no reconocemos se descarta en silencio. Preservamos el
    # orden de la taxonomía para que la respuesta sea estable.
    signals = [signal for signal in ALLOWED_SIGNALS if signal in raw_signals]

    return {"category": category, "signals": signals}


def keyword_criteria(query: str) -> dict:
    """Extrae criterios buscando palabras clave. Sin IA y sin red.

    Es el plan B cuando Bedrock no responde, y de paso deja claro qué aporta
    realmente el modelo: entender frases que esta tabla no cubre.
    """
    normalized = strip_accents(query.lower())

    signals = [
        signal
        for signal in ALLOWED_SIGNALS
        if any(keyword in normalized for keyword in SIGNAL_KEYWORDS.get(signal, ()))
    ]

    category = next(
        (
            candidate
            for candidate in ALLOWED_CATEGORIES
            if any(keyword in normalized for keyword in CATEGORY_KEYWORDS.get(candidate, ()))
        ),
        None,
    )

    return {"category": category, "signals": signals}


def strip_accents(text: str) -> str:
    """Quita los acentos para poder comparar sin sorpresas.

    "café tranquilo" -> "cafe tranquilo"
    """
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


def response(status_code: int, body: dict) -> dict:
    """Da forma a la respuesta que espera el HTTP API.

    Las cabeceras de CORS las añade API Gateway a partir de la
    CorsConfiguration declarada en template.yaml.
    """
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json; charset=utf-8"},
        "body": json.dumps(body, ensure_ascii=False),
    }
