import asyncio
import copy
import json

import httpx
import pytest

from backend.ai_analyzer import AIError, GeminiAnalyzer
from backend.config import Settings
from backend.gemini_schema import grading_schema
from backend.schemas import AIAnalysis
from test_analysis import ESSAY, analysis_json  # noqa: F401


def response(value):
    return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(value)}]}}]})


def test_compatible_schema_preserves_fields_refs_and_numeric_bounds():
    original = AIAnalysis.model_json_schema()
    before = copy.deepcopy(original)
    compact = grading_schema(original)
    assert original == before
    assert compact["required"] == original["required"]
    assert compact["properties"].keys() == original["properties"].keys()
    assert compact["$defs"]["StructureCriterion"]["properties"]["score"]["maximum"] == 25
    assert compact["$defs"]["Scores"]["properties"]["structure"]["$ref"] == "#/$defs/StructureCriterion"
    assert compact["$defs"]["TextFeedback"]["properties"]["category"]["enum"]
    assert len(json.dumps(compact)) < len(json.dumps(original))


def test_schema_metadata_names_are_not_confused_with_field_names():
    full = {"type": "object", "title": "Example", "properties": {
        "title": {"type": "string", "title": "Title", "maxLength": 250},
        "default": {"type": "string", "default": "value"}}, "required": ["title", "default"]}
    compact = grading_schema(full)
    assert compact["properties"] == {"title": {"type": "string"}, "default": {"type": "string"}}
    assert compact["required"] == ["title", "default"]


def test_grading_uses_compatible_transport_schema(analysis_json):
    calls = []
    def handler(request):
        schema = json.loads(request.content)["generationConfig"]["responseJsonSchema"]
        calls.append(schema)
        # Emulate a provider that rejects the large nested string/array limits.
        if '"maxLength"' in json.dumps(schema) or '"maxItems"' in json.dumps(schema):
            return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}})
        return response(analysis_json)
    service = GeminiAnalyzer(Settings(_env_file=None, gemini_api_key="test-key-not-a-credential"), httpx.MockTransport(handler))
    result = asyncio.run(service.analyze("Білім", ESSAY, []))
    assert result.criteria.structure.score == 5
    assert len(calls) == 1


@pytest.mark.parametrize("invalid", ["score", "feedback_count", "feedback_quote"])
def test_backend_validation_is_not_relaxed_by_transport_schema(analysis_json, invalid):
    if invalid == "score":
        analysis_json["criteria"]["structure"]["score"] = 26
    else:
        item = {"category": "argumentation", "quote": "білім маңызды" if invalid == "feedback_count" else "Ойдан шығарылған үзінді.",
                "explanation": "Тест түсіндірмесі.", "suggestion": "Негіздеңіз."}
        analysis_json["text_feedback"] = [item] * (21 if invalid == "feedback_count" else 1)
    service = GeminiAnalyzer(Settings(_env_file=None, gemini_api_key="test-key-not-a-credential"),
                             httpx.MockTransport(lambda request: response(analysis_json)))
    with pytest.raises(AIError):
        asyncio.run(service.analyze("Білім", ESSAY, []))


@pytest.mark.parametrize("reason,expected", [(None, "invalid_request"), ("API_KEY_INVALID", "invalid_api_key"), ("API_KEY_EXPIRED", "invalid_api_key")])
def test_permanent_400_is_diagnosed_without_retries_or_leaking_provider_body(reason, expected):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"error": {"message": "private provider detail", "details": [{"reason": reason}]}})
    service = GeminiAnalyzer(Settings(_env_file=None, gemini_api_key="test-key-not-a-credential"), httpx.MockTransport(handler))
    with pytest.raises(AIError) as error:
        asyncio.run(service.analyze("Білім", ESSAY, []))
    assert error.value.provider_status == 400 and error.value.error_code == expected
    assert "private" not in str(error.value) and len(calls) == 1


def test_quote_repair_identifies_the_actual_invalid_quote(analysis_json):
    calls = []
    bad = copy.deepcopy(analysis_json)
    bad["text_feedback"] = [{"category": "language", "quote": "Дұрысталған, бірақ түпнұсқада жоқ сөйлем.",
                             "explanation": "Емле қатесі.", "suggestion": "Түзетіңіз."}]
    def handler(request):
        parts = json.loads(request.content)["contents"][0]["parts"]
        calls.append(parts)
        if len(calls) == 1:
            return response(bad)
        repair = json.loads(parts[1]["text"])
        assert repair["validation_errors"]["invalid_quotes"] == [bad["text_feedback"][0]["quote"]]
        assert repair["previous_response"] == bad
        return response(analysis_json)
    service = GeminiAnalyzer(Settings(_env_file=None, gemini_api_key="test-key-not-a-credential"), httpx.MockTransport(handler))
    assert asyncio.run(service.analyze("Білім", ESSAY, [])).summary
    assert len(calls) == 2
