"""Directorio de recursos: lectura pública y propuestas pendientes.

Esta función atiende dos rutas del HTTP API:

    GET  /resources -> devuelve los recursos aprobados
    POST /resources -> guarda una propuesta como pending

Es una sola Lambda a propósito. Separar un CRUD tan pequeño en dos funciones
añadiría infraestructura sin añadir aprendizaje.

Solo usa la librería estándar de Python y boto3, que el runtime gestionado de
Lambda ya incluye. Por eso no hay requirements.txt.

Dos decisiones del modelo de datos que conviene tener presentes al leer el
código, porque explican casi toda la validación de más abajo:

1. **La ubicación es opcional.** Un recurso puede ser una línea telefónica, una
   red de canalización o una derivación a un refugio. Exigir coordenadas
   obligaría a inventarlas.
2. **Publicar es una decisión humana.** Lo que llega por el formulario nace
   `pending` y nunca sale en el listado. Solo el seed, revisado a mano, entra
   como `approved`.
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
from urllib.parse import urlparse

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# El cliente se crea una sola vez, fuera del handler. Lambda reutiliza el
# entorno de ejecución entre invocaciones, así que las siguientes llamadas
# aprovechan la conexión ya establecida.
TABLE = boto3.resource("dynamodb").Table(os.environ["PLACES_TABLE"])

# La taxonomía llega desde template.yaml. Es la única fuente de verdad: el
# frontend y la función de búsqueda validan contra estas mismas listas.
ALLOWED_SIGNALS = frozenset(filter(None, os.environ["ALLOWED_SIGNALS"].split(",")))
ALLOWED_CATEGORIES = frozenset(filter(None, os.environ["ALLOWED_CATEGORIES"].split(",")))
ALLOWED_SERVICES = frozenset(filter(None, os.environ["ALLOWED_SERVICES"].split(",")))

MAX_NAME_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 500
MAX_ADDRESS_LENGTH = 200
MAX_SERVICE_AREA_LENGTH = 200
MAX_PHONE_LENGTH = 80
MAX_EMAIL_LENGTH = 160
MAX_URL_LENGTH = 500


class DecimalEncoder(json.JSONEncoder):
    """Convierte los Decimal de DynamoDB en números JSON.

    DynamoDB devuelve todos los números como Decimal para no perder precisión.
    json.dumps no sabe serializarlos, así que se lo enseñamos aquí.
    """

    def default(self, value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        return super().default(value)


def lambda_handler(event: dict, context: Any) -> dict:
    """Punto de entrada. Enruta según el método y la ruta que usó el navegador.

    El HTTP API entrega los eventos en formato payload 2.0, donde `routeKey`
    tiene la forma "GET /resources".
    """
    # API Gateway no entrega una petición HTTP cruda: la traduce a un
    # diccionario. De todo lo que trae, aquí solo interesa `routeKey`, que es el
    # par método + ruta ya resuelto ("GET /resources"). Comparar esa cadena es
    # lo que permite que una sola función atienda varias rutas.
    route_key = event.get("routeKey")
    logger.info("Petición recibida", extra={"routeKey": route_key})

    if route_key == "GET /resources":
        return list_resources()

    if route_key == "POST /resources":
        return create_resource(event.get("body"))

    return response(404, {"message": f"Ruta no encontrada: {route_key}"})


def list_resources() -> dict:
    """Devuelve únicamente los recursos aprobados.

    Usamos Scan porque el MVP maneja decenas de registros y queremos que el
    código sea legible. Scan lee la tabla entera: con millones de items habría
    que diseñar índices y consultas específicas. Ver la tabla
    "workshop vs. producción" del README.

    El filtro por `publicationStatus` se hace aquí, en el servidor. Si viviera
    en el navegador, las propuestas sin revisar viajarían igualmente por la red
    y bastaría abrir las herramientas de desarrollo para leerlas.
    """
    resources: list[dict] = []
    scan_kwargs: dict = {}

    # Un Scan devuelve como máximo 1 MB por página. Si hay más datos, DynamoDB
    # incluye LastEvaluatedKey y hay que volver a pedir desde ahí.
    while True:
        # La llamada a DynamoDB. Devuelve una página de la tabla en `Items`, ya
        # deserializada por boto3: cada item es un diccionario de Python. Los
        # números llegan como `Decimal`, y por eso existe el DecimalEncoder de
        # arriba —`json.dumps` no sabe serializarlos por su cuenta—.
        page = TABLE.scan(**scan_kwargs)

        # El filtro que decide qué puede salir de aquí. Recorre los items de la
        # página y conserva solo los aprobados, así que lo `pending` nunca llega
        # a la lista que se convertirá en la respuesta HTTP.
        resources.extend(
            item for item in page.get("Items", []) if item.get("publicationStatus") == "approved"
        )

        last_key = page.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    resources.sort(key=lambda resource: resource.get("name", ""))
    logger.info("Recursos aprobados devueltos", extra={"count": len(resources)})
    return response(200, {"resources": resources, "count": len(resources)})


def create_resource(raw_body: str | None) -> dict:
    """Guarda una propuesta y nunca la publica como recurso verificado.

    La API es pública, así que nunca confiamos en lo que llega: validamos cada
    campo y descartamos cualquier cosa que no reconozcamos. Devuelve 202
    —aceptado, todavía no procesado— porque eso es literalmente lo que ocurre:
    alguien tiene que revisar la fuente antes de que el recurso exista para el
    resto del mundo.
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

    resource, errors = validate_resource(payload)
    if errors:
        logger.warning("Propuesta rechazada", extra={"errors": errors})
        return response(400, {"message": "Datos inválidos.", "errors": errors})

    TABLE.put_item(Item=resource)
    logger.info("Propuesta guardada", extra={"resourceId": resource["id"]})
    return response(
        202,
        {
            "resource": resource,
            "message": "Gracias. El recurso quedó pendiente de revisión y todavía no aparece en el directorio.",
        },
    )


def validate_resource(payload: dict) -> tuple[dict, list[str]]:
    """Valida un payload de propuesta y devuelve (recurso, errores)."""
    errors: list[str] = []

    name = text_value(payload.get("name"))
    if not 1 <= len(name) <= MAX_NAME_LENGTH:
        errors.append(f"'name' es obligatorio y admite hasta {MAX_NAME_LENGTH} caracteres.")

    category = text_value(payload.get("category"))
    if category not in ALLOWED_CATEGORIES:
        errors.append(f"'category' debe ser una de: {', '.join(sorted(ALLOWED_CATEGORIES))}.")

    services, service_errors = allowed_values(payload.get("services", []), ALLOWED_SERVICES, "services")
    errors.extend(service_errors)
    signals, signal_errors = allowed_values(payload.get("signals", []), ALLOWED_SIGNALS, "signals")
    errors.extend(signal_errors)

    description = text_value(payload.get("description"))[:MAX_DESCRIPTION_LENGTH]
    address = text_value(payload.get("address"))[:MAX_ADDRESS_LENGTH]
    service_area = text_value(payload.get("serviceArea"))[:MAX_SERVICE_AREA_LENGTH]

    # Distinguimos "no mandó coordenadas" de "mandó coordenadas mal": lo
    # primero es válido —un recurso puede no tener sitio físico— y lo segundo
    # es un error que hay que contar. Sin esa distinción, un typo en la latitud
    # se descartaría en silencio y la ficha se publicaría sin ubicación.
    raw_latitude = payload.get("latitude")
    raw_longitude = payload.get("longitude")
    latitude = to_coordinate(raw_latitude, minimum=-90, maximum=90)
    longitude = to_coordinate(raw_longitude, minimum=-180, maximum=180)
    if coordinate_supplied(raw_latitude) and latitude is None:
        errors.append("'latitude' debe ser un número finito entre -90 y 90.")
    if coordinate_supplied(raw_longitude) and longitude is None:
        errors.append("'longitude' debe ser un número finito entre -180 y 180.")
    if coordinate_supplied(raw_latitude) != coordinate_supplied(raw_longitude):
        errors.append("'latitude' y 'longitude' deben enviarse juntas.")

    # La regla de privacidad del proyecto, y vive aquí a propósito. El
    # formulario también desactiva estos campos, pero un formulario se puede
    # saltar con un `curl`. La dirección de un refugio no se guarda ni por
    # error: a veces la decisión correcta es *no* tener un campo.
    if category == "shelter_referral" and (latitude is not None or longitude is not None or address):
        errors.append("Una derivación a refugio no puede guardar dirección ni coordenadas.")

    contact, contact_errors = validate_contact(payload.get("contact"))
    errors.extend(contact_errors)

    submitted_source_url = text_value(payload.get("sourceUrl"))[:MAX_URL_LENGTH]
    if submitted_source_url and not is_http_url(submitted_source_url):
        errors.append("'sourceUrl' debe ser una URL http(s) válida.")

    if not contact and not submitted_source_url:
        errors.append("Incluye al menos un canal de contacto o una fuente para revisar.")

    if errors:
        return {}, errors

    now = datetime.now(timezone.utc)
    resource: dict[str, Any] = {
        "id": build_resource_id(name),
        "name": name,
        "category": category,
        "services": services,
        "signals": signals,
        # El estado y la procedencia los escribe el servidor, nunca el cliente:
        # así nadie puede mandar `publicationStatus: "approved"` en el JSON y
        # colarse en el directorio sin que un humano revise la fuente.
        "publicationStatus": "pending",
        "provenance": {
            "type": "community_submission",
            "source": "safe-space-form",
        },
        "createdAt": now.isoformat(timespec="seconds"),
    }

    for key, value in (
        ("description", description),
        ("address", address),
        ("serviceArea", service_area),
    ):
        if value:
            resource[key] = value

    if latitude is not None and longitude is not None:
        resource["latitude"] = latitude
        resource["longitude"] = longitude

    if contact:
        resource["contact"] = contact

    if submitted_source_url:
        resource["provenance"]["submittedSourceUrl"] = submitted_source_url

    return resource, []


def allowed_values(value: Any, allowed: frozenset[str], field: str) -> tuple[list[str], list[str]]:
    """Comprueba una lista de etiquetas contra la allowlist del stack."""
    if not isinstance(value, list):
        return [], [f"'{field}' debe ser una lista."]

    values = [str(item) for item in value]
    unknown = sorted(set(values) - allowed)
    if unknown:
        return [], [f"Valores no reconocidos en '{field}': {', '.join(unknown)}."]

    return list(dict.fromkeys(values)), []


def validate_contact(value: Any) -> tuple[dict[str, str], list[str]]:
    """Acepta solo canales públicos y acotados."""
    if value is None or value == {}:
        return {}, []
    if not isinstance(value, dict):
        return {}, ["'contact' debe ser un objeto."]

    contact: dict[str, str] = {}
    limits = {
        "phone": MAX_PHONE_LENGTH,
        "email": MAX_EMAIL_LENGTH,
        "website": MAX_URL_LENGTH,
    }
    errors: list[str] = []
    for key, limit in limits.items():
        item = text_value(value.get(key))
        if not item:
            continue
        if len(item) > limit:
            errors.append(f"'contact.{key}' admite hasta {limit} caracteres.")
            continue
        if key == "email" and ("@" not in item or item.startswith("@") or item.endswith("@")):
            errors.append("'contact.email' debe parecer un correo válido.")
            continue
        if key == "website" and not is_http_url(item):
            errors.append("'contact.website' debe ser una URL http(s) válida.")
            continue
        contact[key] = item

    return contact, errors


def is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def text_value(value: Any) -> str:
    return str(value or "").strip()


def to_coordinate(value: Any, minimum: int, maximum: int) -> Decimal | None:
    """Devuelve la coordenada como Decimal, o None si no es utilizable."""
    # `isinstance(value, bool)` no sobra: en Python `True` es un entero, y sin
    # esta línea un `latitude: true` se guardaría como la latitud 1.
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        coordinate = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    if not coordinate.is_finite():
        return None
    if not minimum <= coordinate <= maximum:
        return None
    return coordinate


def coordinate_supplied(value: Any) -> bool:
    """Indica si el cliente intentó enviar una coordenada."""
    return value is not None and value != ""


def build_resource_id(name: str) -> str:
    """Construye un identificador legible y único a partir del nombre.

    "Casa Frida Refugio LGBT+" -> "casa-frida-refugio-lgbt-9f3c1a"

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
    a servir una respuesta antigua. En la práctica: quien abre el directorio
    antes de cargar los datos ve la lista vacía, y al recargar la sigue viendo
    hasta que fuerza el refresco. Pasó de verdad durante el ensayo.
    """
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
        },
        "body": json.dumps(body, cls=DecimalEncoder, ensure_ascii=False),
    }
