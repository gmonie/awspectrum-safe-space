"""Pruebas pequeñas del contrato que el workshop enseña.

Se usa unittest de la librería estándar para que las pruebas funcionen en
CloudShell sin instalar un runner adicional ni hacer llamadas a AWS.
"""

import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parents[1]


def load_places_module():
    table = MagicMock()
    dynamodb = MagicMock()
    dynamodb.Table.return_value = table
    return load_module(
        "places_test_module",
        ROOT / "functions" / "places" / "app.py",
        {"PLACES_TABLE": "test-table"},
        "resource",
        dynamodb,
        table,
    )


def load_search_module():
    bedrock = MagicMock()
    return load_module(
        "search_test_module",
        ROOT / "functions" / "search" / "app.py",
        {"BEDROCK_MODEL_ID": "test-model"},
        "client",
        bedrock,
        bedrock,
    )


def load_module(name, path, extra_env, boto3_method, boto3_value, return_value):
    environment = {
        "ALLOWED_SIGNALS": "lgbtq_affirming,free,open_24_7,contact_only",
        "ALLOWED_CATEGORIES": "organization,support_service,community_center,shelter_referral",
        "ALLOWED_SERVICES": "psychological_support,legal_support,healthcare,referral,community_network,shelter_support",
        **extra_env,
    }
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    boto3_stub = types.ModuleType("boto3")
    setattr(boto3_stub, boto3_method, MagicMock(return_value=boto3_value))
    with patch.dict(os.environ, environment, clear=False), patch.dict(
        sys.modules, {"boto3": boto3_stub}
    ):
        spec.loader.exec_module(module)
    return module, return_value


class PlacesContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module, cls.table = load_places_module()

    def test_list_exposes_only_approved_resources(self):
        self.table.scan.return_value = {
            "Items": [
                {"id": "approved", "name": "Visible", "publicationStatus": "approved"},
                {"id": "pending", "name": "Review", "publicationStatus": "pending"},
                {"id": "legacy", "name": "Legacy"},
            ]
        }

        result = self.module.lambda_handler({"routeKey": "GET /resources"}, None)
        body = json.loads(result["body"])

        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["resources"][0]["id"], "approved")
        # El item "legacy" no tiene publicationStatus: una tabla ya usada puede
        # arrastrar registros del modelo anterior y no deben publicarse por
        # omisión. Solo entra lo que dice "approved" de forma explícita.
        self.assertNotIn("places", body)

    def test_valid_submission_is_pending_and_can_be_contact_only(self):
        payload = {
            "name": "Línea de prueba",
            "category": "support_service",
            "services": ["legal_support"],
            "signals": ["lgbtq_affirming", "contact_only"],
            "serviceArea": "Ciudad de México",
            "contact": {"phone": "55 0000 0000"},
            "sourceUrl": "https://example.org/resource",
        }

        resource, errors = self.module.validate_resource(payload)

        self.assertEqual(errors, [])
        self.assertEqual(resource["publicationStatus"], "pending")
        self.assertEqual(resource["provenance"]["type"], "community_submission")
        self.assertNotIn("latitude", resource)
        self.assertNotIn("longitude", resource)

    def test_shelter_submission_rejects_location(self):
        payload = {
            "name": "Refugio de prueba",
            "category": "shelter_referral",
            "services": ["shelter_support"],
            "signals": ["contact_only"],
            "latitude": 19.42,
            "longitude": -99.15,
            "contact": {"phone": "55 0000 0000"},
        }

        resource, errors = self.module.validate_resource(payload)

        self.assertEqual(resource, {})
        self.assertTrue(any("no puede guardar dirección ni coordenadas" in error for error in errors))

    def test_invalid_coordinates_are_not_silently_dropped(self):
        payload = {
            "name": "Recurso de prueba",
            "category": "organization",
            "services": [],
            "signals": [],
            "latitude": "NaN",
            "longitude": "-99.15",
            "contact": {"website": "https://example.org"},
        }

        _, errors = self.module.validate_resource(payload)

        self.assertTrue(any("latitude" in error and "finito" in error for error in errors))

    def test_coordinate_pair_is_required(self):
        payload = {
            "name": "Recurso de prueba",
            "category": "organization",
            "services": [],
            "signals": [],
            "latitude": 19.42,
            "contact": {"website": "https://example.org"},
        }

        _, errors = self.module.validate_resource(payload)

        self.assertTrue(any("deben enviarse juntas" in error for error in errors))


class SearchContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module, cls.bedrock = load_search_module()

    def test_model_output_is_reduced_to_allowlists(self):
        raw = json.dumps(
            {
                "category": "not-a-category",
                "services": ["legal_support", "invented_service"],
                "signals": ["lgbtq_affirming", "invented_signal"],
            }
        )

        criteria = self.module.validate_criteria(raw)

        self.assertEqual(
            criteria,
            {
                "category": None,
                "services": ["legal_support"],
                "signals": ["lgbtq_affirming"],
            },
        )

    def test_keyword_fallback_extracts_resource_need(self):
        criteria = self.module.keyword_criteria("apoyo psicológico gratuito para personas lgbt")

        self.assertEqual(criteria["category"], "support_service")
        self.assertIn("psychological_support", criteria["services"])
        self.assertIn("lgbtq_affirming", criteria["signals"])

    def test_bedrock_error_returns_fallback_contract(self):
        error = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "test"}},
            "Converse",
        )
        with patch.object(self.module, "ask_bedrock", side_effect=error):
            criteria, source = self.module.extract_criteria("apoyo legal")

        self.assertEqual(source, "fallback")
        self.assertIn("legal_support", criteria["services"])


if __name__ == "__main__":
    unittest.main()
