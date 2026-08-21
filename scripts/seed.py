#!/usr/bin/env python3
"""Carga data/seed.json en la tabla de recursos de tu stack.

    python3 scripts/seed.py
    python3 scripts/seed.py --replace  # borra los items existentes primero

La ruta normal es idempotente: cada recurso del seed tiene un `id` fijo y
sobrescribe el mismo item. `--replace` es deliberadamente explícito porque
elimina los registros anteriores, incluidos los de una stack ya utilizada.
"""

import argparse
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError

STACK_NAME = os.environ.get("STACK_NAME", "safe-space")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_FILE = REPO_ROOT / "data" / "seed.json"

REQUIRED_FIELDS = (
    "id",
    "name",
    "category",
    "services",
    "signals",
    "publicationStatus",
    "provenance",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="borra todos los items existentes antes de cargar el seed",
    )
    args = parser.parse_args()

    if not SEED_FILE.exists():
        print(f"✗ No se encontró {SEED_FILE}", file=sys.stderr)
        return 1

    cloudformation = boto3.client("cloudformation", region_name=AWS_REGION)
    try:
        outputs = stack_outputs(cloudformation, STACK_NAME)
    except ClientError as error:
        print(f"✗ No se pudo leer la stack '{STACK_NAME}': {error}", file=sys.stderr)
        print("  ¿Ya ejecutaste 'sam deploy'?", file=sys.stderr)
        return 1

    table_name = outputs["PlacesTableName"]
    allowed_signals = set(filter(None, outputs["AllowedSignals"].split(",")))
    allowed_categories = set(filter(None, outputs["AllowedCategories"].split(",")))
    allowed_services = set(filter(None, outputs["AllowedServices"].split(",")))

    # parse_float=Decimal porque DynamoDB no acepta el tipo float de Python.
    places = json.loads(SEED_FILE.read_text(encoding="utf-8"), parse_float=Decimal)
    problems = validate(places, allowed_signals, allowed_categories, allowed_services)
    if problems:
        print(f"✗ {SEED_FILE.name} tiene {len(problems)} problema(s):", file=sys.stderr)
        for problem in problems:
            print(f"  · {problem}", file=sys.stderr)
        return 1

    table = boto3.resource("dynamodb", region_name=AWS_REGION).Table(table_name)
    if args.replace:
        deleted = replace_items(table)
        print(f"🧹 Se eliminaron {deleted} recursos anteriores.")

    print(f"🌈 Cargando {len(places)} recursos aprobados en la tabla {table_name}…")
    # batch_writer agrupa las escrituras en lotes y reintenta las que DynamoDB
    # devuelve sin procesar. Es la forma recomendada de cargar varios items.
    with table.batch_writer() as batch:
        for place in places:
            batch.put_item(Item=place)
            print(f"  ✓ {place['name']}")

    print(f"\n✓ {len(places)} recursos cargados. Compruébalo con:")
    print(f"    curl {outputs['ApiUrl']}/resources")
    return 0


def stack_outputs(cloudformation, stack_name: str) -> dict[str, str]:
    """Devuelve los Outputs de la stack como un diccionario."""
    described = cloudformation.describe_stacks(StackName=stack_name)
    return {
        output["OutputKey"]: output["OutputValue"]
        for output in described["Stacks"][0].get("Outputs", [])
    }


def validate(
    resources,
    allowed_signals: set[str],
    allowed_categories: set[str],
    allowed_services: set[str],
) -> list[str]:
    """Revisa el seed contra la taxonomía real de la stack.

    Atrapa erratas antes de escribir en DynamoDB: es mucho más fácil arreglar
    un typo aquí que descubrir por qué una ficha no aparece en el directorio.

    Comprueba además las dos reglas que definen el producto: un recurso del
    seed nace `approved`, y para estarlo necesita una fuente directa con fecha.
    """
    problems: list[str] = []

    if not isinstance(resources, list):
        return ["El archivo debe contener una lista de recursos."]

    seen_ids: set[str] = set()
    for index, resource in enumerate(resources):
        label = resource.get("name", f"#{index}") if isinstance(resource, dict) else f"#{index}"

        if not isinstance(resource, dict):
            problems.append(f"{label}: no es un objeto JSON.")
            continue

        for field in REQUIRED_FIELDS:
            if field not in resource:
                problems.append(f"{label}: falta el campo '{field}'.")

        resource_id = resource.get("id")
        if resource_id in seen_ids:
            problems.append(f"{label}: el id '{resource_id}' está repetido.")
        seen_ids.add(resource_id)

        if resource.get("publicationStatus") != "approved":
            problems.append(f"{label}: el seed solo puede contener recursos approved.")

        category = resource.get("category")
        if category not in allowed_categories:
            problems.append(f"{label}: categoría desconocida '{category}'.")

        problems.extend(check_allowed_list(label, resource.get("services"), allowed_services, "services"))
        problems.extend(check_allowed_list(label, resource.get("signals"), allowed_signals, "signals"))
        problems.extend(validate_provenance(label, resource.get("provenance")))
        problems.extend(validate_contact(label, resource.get("contact")))
        problems.extend(validate_location(label, resource))

    return problems


def check_allowed_list(label: str, value, allowed: set[str], field: str) -> list[str]:
    if not isinstance(value, list):
        return [f"{label}: '{field}' debe ser una lista."]
    unknown = set(value) - allowed
    return [f"{label}: {field} desconocidos {sorted(unknown)}."] if unknown else []


def validate_provenance(label: str, provenance) -> list[str]:
    if not isinstance(provenance, dict):
        return [f"{label}: 'provenance' debe ser un objeto."]
    problems: list[str] = []
    if provenance.get("type") != "direct_source":
        problems.append(f"{label}: un recurso aprobado necesita provenance.type='direct_source'.")
    if not is_http_url(str(provenance.get("sourceUrl", ""))):
        problems.append(f"{label}: falta una provenance.sourceUrl http(s) directa.")
    if not provenance.get("checkedAt"):
        problems.append(f"{label}: falta provenance.checkedAt.")
    return problems


def validate_contact(label: str, contact) -> list[str]:
    if not isinstance(contact, dict):
        return [f"{label}: falta el objeto 'contact'."]
    if not any(str(contact.get(key, "")).strip() for key in ("phone", "email", "website")):
        return [f"{label}: debe tener al menos un canal de contacto."]
    if contact.get("website") and not is_http_url(str(contact["website"])):
        return [f"{label}: contact.website debe ser una URL http(s)."]
    return []


def validate_location(label: str, resource: dict) -> list[str]:
    latitude = resource.get("latitude")
    longitude = resource.get("longitude")
    has_latitude = latitude is not None
    has_longitude = longitude is not None
    problems: list[str] = []

    if has_latitude != has_longitude:
        problems.append(f"{label}: latitude y longitude deben aparecer juntas.")
    if has_latitude:
        try:
            parsed_latitude = Decimal(str(latitude))
            if not parsed_latitude.is_finite() or not -90 <= parsed_latitude <= 90:
                problems.append(f"{label}: latitude fuera de rango.")
        except (ArithmeticError, ValueError):
            problems.append(f"{label}: latitude no es un número válido.")
    if has_longitude:
        try:
            parsed_longitude = Decimal(str(longitude))
            if not parsed_longitude.is_finite() or not -180 <= parsed_longitude <= 180:
                problems.append(f"{label}: longitude fuera de rango.")
        except (ArithmeticError, ValueError):
            problems.append(f"{label}: longitude no es un número válido.")

    # Misma regla que en la Lambda, comprobada también aquí: el seed se edita a
    # mano y es justo donde alguien añadiría "solo la colonia, para ubicarlo".
    if resource.get("category") == "shelter_referral":
        if has_latitude or has_longitude or resource.get("address"):
            problems.append(f"{label}: shelter_referral no puede tener dirección ni coordenadas.")
    return problems


def is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def replace_items(table) -> int:
    """Elimina todo el contenido actual; solo se invoca con --replace."""
    keys: list[dict] = []
    scan_kwargs: dict = {"ProjectionExpression": "#id", "ExpressionAttributeNames": {"#id": "id"}}
    while True:
        page = table.scan(**scan_kwargs)
        keys.extend({"id": item["id"]} for item in page.get("Items", []))
        last_key = page.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    with table.batch_writer() as batch:
        for key in keys:
            batch.delete_item(Key=key)
    return len(keys)


if __name__ == "__main__":
    sys.exit(main())
