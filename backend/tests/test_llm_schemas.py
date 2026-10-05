"""Every task response model must convert to the schemas of OpenAI (strict mode) and Gemini."""

from typing import Any

import pytest
from google import genai
from google.genai import _transformers, types
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel

from app.llm.types import Explanation, GeneratedExercise, Gloss, GradeResult, SimplifiedText

RESPONSE_MODELS = [GeneratedExercise, GradeResult, Explanation, SimplifiedText, Gloss]


def objects(schema: Any) -> list[dict[str, Any]]:
    """Every object schema inside a JSON schema."""
    found: list[dict[str, Any]] = []
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            found.append(schema)
        for value in schema.values():
            found.extend(objects(value))
    elif isinstance(schema, list):
        for value in schema:
            found.extend(objects(value))
    return found


@pytest.mark.parametrize("model", RESPONSE_MODELS, ids=lambda m: m.__name__)
def test_openai_strict_schema(model: type[BaseModel]) -> None:
    schema = to_strict_json_schema(model)
    for obj in objects(schema):
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])


@pytest.mark.parametrize("model", RESPONSE_MODELS, ids=lambda m: m.__name__)
def test_gemini_schema(model: type[BaseModel]) -> None:
    # The conversion the SDK applies to `response_schema=<model>`; no network is involved.
    client = genai.Client(api_key="dummy")
    schema = _transformers.t_schema(client._api_client, model)
    assert isinstance(schema, types.Schema) and schema.properties
    # Strict variant: fails on any JSON-schema keyword Gemini does not support.
    types.Schema.from_json_schema(
        json_schema=types.JSONSchema.model_validate(model.model_json_schema()),
        raise_error_on_unsupported_field=True,
    )
