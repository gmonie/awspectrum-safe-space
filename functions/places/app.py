"""Espacios de la comunidad: lectura y alta.

Esta función atiende dos rutas del HTTP API:

    GET  /places  -> devuelve todos los espacios registrados
    POST /places  -> registra un espacio nuevo

Es una sola Lambda a propósito. Separar un CRUD tan pequeño en dos funciones
añadiría infraestructura sin añadir aprendizaje.

Solo usa la librería estándar de Python y boto3, que el runtime gestionado de
Lambda ya incluye. Por eso no hay requirements.txt.
"""

import json
import logging
import os
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# El cliente se crea una sola vez, fuera del handler. Lambda reutiliza el
# entorno de ejecución entre invocaciones, así que las siguientes llamadas
# aprovechan la conexión ya establecida.
TABLE = boto3.resource("dynamodb").Table(os.environ["PLACES_TABLE"])

# La taxonomía llega desde template.yaml. Es la única fuente de verdad: el
# frontend y la función de búsqueda validan contra la misma lista.
ALLOWED_SIGNALS = frozenset(os.environ["ALLOWED_SIGNALS"].split(","))
ALLOWED_CATEGORIES = frozenset(os.environ["ALLOWED_CATEGORIES"].split(","))

MAX_NAME_LENGTH = 120
MAX_ADDRESS_LENGTH = 200
MAX_NOTE_LENGTH = 280


class DecimalEncoder(json.JSONEncoder):
    """Convierte los Decimal de DynamoDB en números JSON.

    DynamoDB devuelve todos los números como Decimal para no perder precisión.
    json.dumps no sabe serializarlos, así que se lo enseñamos aquí.
    """

    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


def lambda_handler(event: dict, context: Any) -> dict:
    """Punto de entrada. Enruta según el método y la ruta que usó el navegador.

    El HTTP API entrega los eventos en formato payload 2.0, donde `routeKey`
    tiene la forma "GET /places".
    """
    route_key = event.get("routeKey")
    logger.info("Petición recibida", extra={"routeKey": route_key})

    if route_key == "GET /places":
        return list_places()

    if route_key == "POST /places":
        return create_place(event.get("body"))

    return response(404, {"message": f"Ruta no encontrada: {route_key}"})


def list_places() -> dict:
    """Devuelve todos los espacios de la tabla.

    Usamos Scan porque el MVP maneja decenas de registros y queremos que el
    código sea legible. Scan lee la tabla entera: con millones de items habría
    que diseñar índices y consultas específicas. Ver la tabla
    "workshop vs. producción" del README.
    """
    places: list[dict] = []
    scan_kwargs: dict = {}

    # Un Scan devuelve como máximo 1 MB por página. Si hay más datos, DynamoDB
    # incluye LastEvaluatedKey y hay que volver a pedir desde ahí.
    while True:
        page = TABLE.scan(**scan_kwargs)
        places.extend(page.get("Items", []))

        last_key = page.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    places.sort(key=lambda place: place.get("name", ""))
    logger.info("Espacios devueltos", extra={"count": len(places)})
    return response(200, {"places": places, "count": len(places)})


def create_place(raw_body: str | None) -> dict:
    """Registra un espacio nuevo enviado desde el formulario del frontend.

    La API es pública, así que nunca confiamos en lo que llega: validamos cada
    campo y descartamos cualquier cosa que no reconozcamos.
    """
    if not raw_body:
        return response(400, {"message": "El cuerpo de la petición está vacío."})

    try:
        # parse_float=Decimal porque DynamoDB no acepta float de Python.
        payload = json.loads(raw_body, parse_float=Decimal)
    except json.JSONDecodeError:
        return response(400, {"message": "El cuerpo de la petición no es JSON válido."})

    if not isinstance(payload, dict):
        return response(400, {"message": "Se esperaba un objeto JSON."})

    place, errors = validate_place(payload)
    if errors:
        logger.warning("Alta rechazada", extra={"errors": errors})
        return response(400, {"message": "Datos inválidos.", "errors": errors})

    TABLE.put_item(Item=place)
    logger.info("Espacio registrado", extra={"placeId": place["id"]})
    return response(201, {"place": place})


def validate_place(payload: dict) -> tuple[dict, list[str]]:
    """Comprueba el payload y devuelve (espacio_listo_para_guardar, errores).

    Si la lista de errores no está vacía, el primer valor no debe usarse.
    """
    errors: list[str] = []

    name = str(payload.get("name", "")).strip()
    if not 1 <= len(name) <= MAX_NAME_LENGTH:
        errors.append(f"'name' es obligatorio y admite hasta {MAX_NAME_LENGTH} caracteres.")

    category = str(payload.get("category", "")).strip()
    if category not in ALLOWED_CATEGORIES:
        errors.append(f"'category' debe ser una de: {', '.join(sorted(ALLOWED_CATEGORIES))}.")

    latitude = to_coordinate(payload.get("latitude"), minimum=-90, maximum=90)
    if latitude is None:
        errors.append("'latitude' debe ser un número entre -90 y 90.")

    longitude = to_coordinate(payload.get("longitude"), minimum=-180, maximum=180)
    if longitude is None:
        errors.append("'longitude' debe ser un número entre -180 y 180.")

    raw_signals = payload.get("signals", [])
    if not isinstance(raw_signals, list):
        errors.append("'signals' debe ser una lista.")
        signals: list[str] = []
    else:
        signals = [signal for signal in raw_signals if signal in ALLOWED_SIGNALS]
        unknown = sorted({str(signal) for signal in raw_signals} - ALLOWED_SIGNALS)
        if unknown:
            errors.append(f"Señales no reconocidas: {', '.join(unknown)}.")

    address = str(payload.get("address", "")).strip()[:MAX_ADDRESS_LENGTH]
    community_note = str(payload.get("communityNote", "")).strip()[:MAX_NOTE_LENGTH]

    if errors:
        return {}, errors

    now = datetime.now(timezone.utc)
    place = {
        "id": build_place_id(name),
        "name": name,
        "category": category,
        "address": address,
        "latitude": latitude,
        "longitude": longitude,
        "signals": signals,
        "communityNote": community_note,
        # La procedencia la escribe el servidor, nunca el cliente: así el
        # origen del dato no se puede falsear desde el formulario.
        "provenance": {
            "type": "community_report",
            "source": "safe-space-form",
            "verifiedAt": now.strftime("%Y-%m"),
        },
        "createdAt": now.isoformat(timespec="seconds"),
    }
    return place, []


def to_coordinate(value: Any, minimum: int, maximum: int) -> Decimal | None:
    """Devuelve la coordenada como Decimal, o None si no es utilizable."""
    if value is None or isinstance(value, bool):
        return None
    try:
        coordinate = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    if not minimum <= coordinate <= maximum:
        return None
    return coordinate


def build_place_id(name: str) -> str:
    """Construye un identificador legible y único a partir del nombre.

    "Café Ana Bonita" -> "cafe-ana-bonita-9f3c1a"

    La clave de partición de DynamoDB solo necesita ser única; que además sea
    legible ayuda a inspeccionar la tabla en la consola durante el workshop.
    """
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:48]
    suffix = uuid.uuid4().hex[:6]
    return f"{slug}-{suffix}" if slug else suffix


def response(status_code: int, body: dict) -> dict:
    """Da forma a la respuesta que espera el HTTP API.

    Las cabeceras de CORS no se ponen aquí: las añade el propio API Gateway a
    partir de la CorsConfiguration declarada en template.yaml.

    'no-store' es necesario, no decorativo. Sin ninguna cabecera de frescura el
    navegador puede aplicar cacheo heurístico (RFC 9111, sección 4.2.2) y volver
    a servir una respuesta antigua. En la práctica: quien abre el mapa antes de
    cargar los datos ve la lista vacía, y al recargar la sigue viendo hasta que
    fuerza el refresco. La lista de lugares cambia cada vez que alguien
    recomienda un sitio, así que nunca debe cachearse.
    """
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
        },
        "body": json.dumps(body, cls=DecimalEncoder, ensure_ascii=False),
    }
