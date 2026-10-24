import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from backend.ai_analyzer import AIError, GeminiAnalyzer
from backend.config import Settings
from backend.schemas import TopicAlignment
from test_analysis import ESSAY, analysis_json  # noqa: F401


def reply(value):
    return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(value)}]}}]})


def test_scoring_payload_is_identical_for_different_titles(analysis_json):
    scoring_payloads = []
    topic_payloads = []
    def handler(request):
        payload = json.loads(request.content)
        data = json.loads(payload["contents"][0]["parts"][0]["text"])
        if "status" in payload["generationConfig"]["responseJsonSchema"]["properties"]:
            topic_payloads.append(data)
            return reply({"status": "mismatched", "explanation": "Мәтін білім туралы.",
                          "quotes": ["білім маңызды"], "suggested_title": "Білімнің пайдасы"})
        assert "title" not in data
        scoring_payloads.append(payload)
        return reply(analysis_json)

    async def run():
        service = GeminiAnalyzer(Settings(_env_file=None, gemini_api_key="test-key-not-a-credential"), httpx.MockTransport(handler))
        first, topic1 = await service.assess("Спорт", ESSAY, [])
        second, topic2 = await service.assess("Экология", ESSAY, [])
        assert first == second
        assert sum(c["score"] for c in second.criteria.model_dump().values()) == 25
        assert topic1.status == topic2.status == "mismatched"
    asyncio.run(run())
    assert scoring_payloads[0] == scoring_payloads[1]
    assert [data["title"] for data in topic_payloads] == ["Спорт", "Экология"]


@pytest.mark.parametrize("status,suggestion,quotes", [
    ("mismatched", None, ["білім маңызды"]),
    ("partial", "", ["білім маңызды"]),
    ("matched", "Артық ұсыныс", ["білім маңызды"]),
    ("undetermined", "Ойдан шығарылған тақырып", []),
    ("mismatched", "Білім", []),
    ("matched", None, []),
    ("unknown", None, []),
])
def test_inconsistent_topic_results_are_rejected(status, suggestion, quotes):
    with pytest.raises(ValidationError):
        TopicAlignment(status=status, explanation="Түсіндірме", quotes=quotes, suggested_title=suggestion)


def test_undetermined_does_not_require_invented_evidence():
    assert TopicAlignment(status="undetermined", explanation="Мазмұн жеткіліксіз.", quotes=[], suggested_title=None).status == "undetermined"


@pytest.mark.parametrize("invalid", [
    {"quotes": ["Түпнұсқада жоқ сөйлем."]},
    {"suggested_title": "Спорт"},
    {"suggested_title": None},
])
def test_topic_validation_repairs_once_then_rejects(invalid):
    calls = []
    def handler(request):
        calls.append(request)
        return reply({"status": "mismatched", "explanation": "Мәтін білім туралы.",
                      "quotes": ["білім маңызды"], "suggested_title": "Білімнің пайдасы", **invalid})
    service = GeminiAnalyzer(Settings(_env_file=None, gemini_api_key="test-key-not-a-credential"), httpx.MockTransport(handler))
    with pytest.raises(AIError):
        asyncio.run(service.compare_topic("Спорт", ESSAY))
    assert len(calls) == 2


def test_failure_cancels_other_ai_request():
    cancelled = asyncio.Event()
    class Service(GeminiAnalyzer):
        async def analyze(self, *args):
            await asyncio.sleep(0)
            raise AIError("Тест қатесі")
        async def compare_topic(self, *args):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()
    async def run():
        service = Service(Settings(_env_file=None))
        with pytest.raises(AIError):
            await service.assess("Спорт", ESSAY, [])
        assert cancelled.is_set()
    asyncio.run(run())
