#!/usr/bin/env python3
"""Carga data/seed.json en la tabla de DynamoDB de tu stack.

    python3 scripts/seed.py

Es idempotente: cada lugar del seed tiene un `id` fijo, así que volver a
ejecutarlo sobrescribe los mismos registros en vez de duplicarlos. Puedes
lanzarlo las veces que necesites.

Variables de entorno opcionales:
    STACK_NAME   nombre de la stack de CloudFormation (por defecto: safe-spot)
    AWS_REGION   región (por defecto: us-east-1)
"""

import json
import os
import sys
from decimal import Decimal
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

STACK_NAME = os.environ.get("STACK_NAME", "safe-spot")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_FILE = REPO_ROOT / "data" / "seed.json"

REQUIRED_FIELDS = ("id", "name", "category", "latitude", "longitude")


def main() -> int:
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
    allowed_signals = set(outputs["AllowedSignals"].split(","))
    allowed_categories = set(outputs["AllowedCategories"].split(","))

    # parse_float=Decimal porque DynamoDB no acepta el tipo float de Python.
    places = json.loads(SEED_FILE.read_text(encoding="utf-8"), parse_float=Decimal)

    problems = validate(places, allowed_signals, allowed_categories)
    if problems:
        print(f"✗ {SEED_FILE.name} tiene {len(problems)} problema(s):", file=sys.stderr)
        for problem in problems:
            print(f"  · {problem}", file=sys.stderr)
        return 1

    print(f"🌈 Cargando {len(places)} lugares en la tabla {table_name}…")

    table = boto3.resource("dynamodb", region_name=AWS_REGION).Table(table_name)
    # batch_writer agrupa las escrituras en lotes y reintenta las que DynamoDB
    # devuelve sin procesar. Es la forma recomendada de cargar varios items.
    with table.batch_writer() as batch:
        for place in places:
            batch.put_item(Item=place)
            print(f"  ✓ {place['name']}")

    print(f"\n✓ {len(places)} lugares cargados. Compruébalo con:")
    print(f"    curl {outputs['ApiUrl']}/places")
    return 0


def stack_outputs(cloudformation, stack_name: str) -> dict[str, str]:
    """Devuelve los Outputs de la stack como un diccionario."""
    described = cloudformation.describe_stacks(StackName=stack_name)
    return {
        output["OutputKey"]: output["OutputValue"]
        for output in described["Stacks"][0].get("Outputs", [])
    }


def validate(places, allowed_signals: set[str], allowed_categories: set[str]) -> list[str]:
    """Revisa el seed contra la taxonomía real de la stack.

    Atrapa erratas antes de escribir en DynamoDB: es mucho más fácil arreglar
    un typo aquí que descubrir por qué un pin no aparece en el mapa.
    """
    problems: list[str] = []

    if not isinstance(places, list):
        return ["El archivo debe contener una lista de lugares."]

    seen_ids: set[str] = set()
    for index, place in enumerate(places):
        label = place.get("name", f"#{index}") if isinstance(place, dict) else f"#{index}"

        if not isinstance(place, dict):
            problems.append(f"{label}: no es un objeto JSON.")
            continue

        for field in REQUIRED_FIELDS:
            if field not in place:
                problems.append(f"{label}: falta el campo '{field}'.")

        place_id = place.get("id")
        if place_id in seen_ids:
            problems.append(f"{label}: el id '{place_id}' está repetido.")
        seen_ids.add(place_id)

        category = place.get("category")
        if category not in allowed_categories:
            problems.append(f"{label}: categoría desconocida '{category}'.")

        unknown = set(place.get("signals", [])) - allowed_signals
        if unknown:
            problems.append(f"{label}: señales desconocidas {sorted(unknown)}.")

        if "provenance" not in place:
            problems.append(f"{label}: falta 'provenance'. Todo dato debe declarar su origen.")

    return problems


if __name__ == "__main__":
    sys.exit(main())
