import unittest

from render_master_bot.models import RenderSpec
from render_master_bot.schemas import (
    contract_schema,
    ollama_generation_schema,
    ollama_model_schema,
)


def schema_contains(value, key):
    if isinstance(value, dict):
        return key in value or any(schema_contains(item, key) for item in value.values())
    if isinstance(value, list):
        return any(schema_contains(item, key) for item in value)
    return False


class GenerationSchemaTests(unittest.TestCase):
    def test_all_public_contract_schemas_can_be_exported(self):
        for name in (
            "render-spec",
            "technique-card",
            "asset-card",
            "render-spec-patch",
            "correction-decision",
            "evaluation-report",
            "capability-manifest",
            "run-manifest",
        ):
            with self.subTest(contract=name):
                schema = contract_schema(name)
                self.assertEqual(schema["type"], "object")
                self.assertIn("schema_version", schema["properties"])

    def test_strict_schema_keeps_validation_constraints(self):
        schema = RenderSpec.model_json_schema()
        self.assertTrue(schema_contains(schema, "pattern"))
        self.assertTrue(schema_contains(schema, "prefixItems"))

    def test_ollama_projection_removes_incompatible_keywords(self):
        schema = ollama_generation_schema()
        self.assertFalse(schema_contains(schema, "pattern"))
        self.assertFalse(schema_contains(schema, "prefixItems"))
        self.assertTrue(schema_contains(schema, "items"))

    def test_ollama_projection_accepts_any_pydantic_model(self):
        schema = ollama_model_schema(RenderSpec)
        self.assertEqual(schema, ollama_generation_schema())


if __name__ == "__main__":
    unittest.main()
