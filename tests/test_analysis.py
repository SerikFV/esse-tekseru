"""Offline contract/error tests. These do NOT establish Gemini's grading quality."""
import asyncio
import copy
import json

import httpx
import pytest
from pydantic import ValidationError

from backend.ai_analyzer import AIError, GeminiAnalyzer
from backend.config import Settings
from backend.marker_detector import detect_markers
from backend.schemas import AIAnalysis, EssayInput, MAX_SCORES, validate_quotes, word_count

ESSAY = "Менің ойымша, білім маңызды. Себебі білім жаңа мүмкіндік береді. Мысалы, оқушы кітаптан үйренеді. Демек, оқу қажет."


@pytest.fixture
def analysis_json():
    criterion = {"score": 5, "explanation": "Бұл — тек тест жауабы.", "quotes": ["білім маңызды"],
                 "weaknesses": [], "recommendations": ["Ойды нақты мысалмен толықтырыңыз."]}
    return {"summary": "Бұл — тек тест нәтижесі, нақты Gemini талдауы емес.", "strengths": ["Тезис бар."],
            "weaknesses": [], "recommendations": [],
            "classification": {"style": "Публицистикалық", "essay_type": "Аргументативті", "explanation": "Автор пікірі бар.", "quotes": ["Менің ойымша"]},
            "criteria": {name: copy.deepcopy(criterion) for name in MAX_SCORES},
            "structure_map": {"introduction": [], "thesis": ["білім маңызды"], "arguments": [], "evidence": [], "counterargument": [], "conclusion": ["Демек, оқу қажет."]},
            "language_corrections": []}


@pytest.mark.parametrize("name,limit", MAX_SCORES.items())
def test_score_bounds(analysis_json, name, limit):
    for invalid in (-1, limit + 1, 2.5, "5", True):
        analysis_json["criteria"][name]["score"] = invalid
        with pytest.raises(ValidationError):
            AIAnalysis.model_validate(analysis_json)


def test_quotes_must_come_from_essay(analysis_json):
    result = AIAnalysis.model_validate(analysis_json)
    validate_quotes(result, ESSAY)
    result.structure_map.thesis.append("Бұл сөйлем түпнұсқада жоқ.")
    with pytest.raises(ValueError):
        validate_quotes(result, ESSAY)


@pytest.mark.parametrize("text", ["", "  \n ", "!!!", "_", "а" * 30001])
def test_invalid_input(text):
    with pytest.raises(ValidationError):
        EssayInput(title="Эссе", essay_text=text)


def test_short_text_is_accepted():
    assert EssayInput(title="Оқу", essay_text="Оқу пайдалы.").essay_text == "Оқу пайдалы."


def test_markers_have_real_offsets_and_no_substring_matches():
    markers = detect_markers(ESSAY)
    assert len(markers) == 4
    for marker in markers:
        assert ESSAY[marker["start"]:marker["end"]] == marker["text"]
        assert marker["text"] in marker["sentence"]
    assert detect_markers("Мысалындағы сөз.") == []
    assert word_count("Қазақ тілі — білім-білік негізі.") == 4


def analyzer(handler):
    return GeminiAnalyzer(Settings(_env_file=None, gemini_api_key="test-key-not-a-credential"), httpx.MockTransport(handler))


def test_real_http_contract(analysis_json):
    def handler(request):
        assert request.url.host == "generativelanguage.googleapis.com"
        assert "key=" not in str(request.url)
        assert request.headers["x-goog-api-key"] == "test-key-not-a-credential"
        payload = json.loads(request.content)
        assert json.loads(payload["contents"][0]["parts"][0]["text"])["essay_text"] == ESSAY
        assert payload["generationConfig"]["responseJsonSchema"]["$defs"]
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(analysis_json)}]}}]})
    result = asyncio.run(analyzer(handler).analyze("Білім", ESSAY, detect_markers(ESSAY)))
    assert result.criteria.structure.score == 5


@pytest.mark.parametrize("status,expected", [(401,503),(403,503),(429,429),(500,502),(404,502)])
def test_provider_errors(status, expected):
    with pytest.raises(AIError) as raised:
        asyncio.run(analyzer(lambda request: httpx.Response(status, text="private provider error")).analyze("Тақырып", ESSAY, []))
    assert raised.value.status == expected
    assert "private" not in raised.value.message


@pytest.mark.parametrize("response", [
    {}, {"candidates": []}, {"candidates": [{"finishReason": "MAX_TOKENS"}]},
    {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "not json"}]}}]},
    {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "{}"}]}}]},
])
def test_bad_provider_responses(response):
    with pytest.raises(AIError):
        asyncio.run(analyzer(lambda request: httpx.Response(200, json=response)).analyze("Тақырып", ESSAY, []))


def test_timeout():
    def handler(request):
        raise httpx.ReadTimeout("sensitive detail", request=request)
    with pytest.raises(AIError) as raised:
        asyncio.run(analyzer(handler).analyze("Тақырып", ESSAY, []))
    assert raised.value.status == 504


def test_missing_key_never_calls_provider():
    service = GeminiAnalyzer(Settings(_env_file=None, gemini_api_key=""))
    with pytest.raises(AIError) as raised:
        asyncio.run(service.analyze("Тақырып", ESSAY, []))
    assert raised.value.status == 503


def test_total_timeout_includes_waiting_for_available_slot():
    async def run():
        service = GeminiAnalyzer(Settings(_env_file=None, gemini_api_key="test-key-not-a-credential", gemini_timeout_seconds=1))
        service.slots = asyncio.Semaphore(0)
        with pytest.raises(AIError) as raised:
            await service.analyze("Тақырып", ESSAY, [])
        assert raised.value.status == 504
    asyncio.run(run())


def test_invalid_quote_is_repaired_once_and_still_validated(analysis_json):
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        result = copy.deepcopy(analysis_json)
        if len(calls) == 1:
            result["structure_map"]["thesis"] = ["Түпнұсқада жоқ сөйлем."]
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(result)}]}}]})
    result = asyncio.run(analyzer(handler).analyze("Тақырып", ESSAY, []))
    validate_quotes(result, ESSAY)
    assert len(calls) == 2
    assert len(calls[1]["contents"][0]["parts"]) == 2


def test_unrepairable_quotes_fail_without_returning_fake_result(analysis_json):
    calls = []
    analysis_json["classification"]["quotes"] = ["Ойдан шығарылған дәйексөз."]
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(analysis_json)}]}}]})
    with pytest.raises(AIError):
        asyncio.run(analyzer(handler).analyze("Тақырып", ESSAY, []))
    assert len(calls) == 2


def test_temporary_provider_outage_is_retried_once(analysis_json):
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        assert len(json.loads(request.content)["contents"][0]["parts"]) == 1
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(analysis_json)}]}}]})
    result = asyncio.run(analyzer(handler).analyze("Тақырып", ESSAY, []))
    validate_quotes(result, ESSAY)
    assert len(calls) == 2
