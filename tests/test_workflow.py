import asyncio
import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.ai_analyzer import AIError
from backend.config import Settings
from backend.feedback import annotations_for, version_comparison, topic_segments
from backend.main import create_app
from backend.models import Essay, EssayAnalysis, EssayWork
from backend.schemas import AIAnalysis, TopicAlignment, validate_quotes
from backend.workflow import finish_section
from test_analysis import ESSAY, analysis_json  # noqa: F401
from test_api import database  # noqa: F401


class Service:
    def __init__(self, data):
        self.data = data
        self.grade_calls = self.topic_calls = 0
        self.fail_grade = self.fail_topic = False
        self.release_topic = None

    async def analyze(self, title, essay, markers):
        self.grade_calls += 1
        if self.fail_grade:
            raise AIError("Бағалау уақытша орындалмады.", 503)
        return AIAnalysis.model_validate(self.data)

    async def compare_topic(self, title, essay):
        self.topic_calls += 1
        if self.release_topic:
            while not self.release_topic.is_set():
                await asyncio.sleep(0.01)
        if self.fail_topic:
            raise AIError("Тақырып тексеруі уақытша орындалмады.")
        return TopicAlignment(status="matched", explanation="Мәтін тақырыпқа сай.", quotes=["білім маңызды"], suggested_title=None)


def wait_for(client, essay_id, predicate=lambda data: data["status"] != "pending"):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        data = client.get(f"/api/essays/{essay_id}").json()
        if predicate(data):
            return data
        time.sleep(0.025)
    pytest.fail("The workflow did not reach the expected state")


@pytest.mark.parametrize("failed", ["grade", "topic"])
def test_partial_results_and_retry_preserve_ready_section(database, analysis_json, failed):
    service = Service(analysis_json)
    setattr(service, f"fail_{failed}", True)
    with TestClient(create_app(Settings(_env_file=None), service, database)) as client:
        first = client.post("/api/essays/analyze", json={"title": "Білім", "essay_text": ESSAY}).json()
        assert first["status"] == "partial"
        assert first["sections"][failed]["status"] == "failed"
        ready_key = "analysis" if failed == "topic" else "topic_alignment"
        ready_result = copy.deepcopy(first[ready_key])
        setattr(service, f"fail_{failed}", False)
        second = client.post(f"/api/essays/{first['id']}/retry/{failed}").json()
        assert second["status"] == "ready"
        assert second[ready_key] == ready_result
        assert second["total_score"] == 25
        assert (service.grade_calls, service.topic_calls) == ((1, 2) if failed == "topic" else (2, 1))
        client.post(f"/api/essays/{first['id']}/retry/{failed}")
        assert (service.grade_calls, service.topic_calls) == ((1, 2) if failed == "topic" else (2, 1))


def test_progress_is_saved_before_other_section_completes(database, analysis_json):
    service = Service(analysis_json)
    service.release_topic = threading.Event()
    with TestClient(create_app(Settings(_env_file=None), service, database)) as client:
        response = client.post("/api/essays/analyze?background=true", json={"title": "Білім", "essay_text": ESSAY})
        assert response.status_code == 202
        essay_id = response.json()["id"]
        partial = wait_for(client, essay_id, lambda data: data["sections"]["grade"]["status"] == "ready")
        assert partial["total_score"] == 25
        assert partial["sections"]["topic"]["status"] == "pending"
        # Reloading the endpoint reads the committed grade from PostgreSQL.
        assert client.get(f"/api/essays/{essay_id}").json()["analysis"] == partial["analysis"]
        service.release_topic.set()
        assert wait_for(client, essay_id)["status"] == "ready"


def test_provider_error_metadata_is_saved_without_provider_secrets(database, analysis_json):
    class RejectedService(Service):
        async def analyze(self, *args):
            raise AIError("Сұрау пішімі қабылданбады.", provider_status=400, error_code="invalid_request")
    service = RejectedService(analysis_json)
    with TestClient(create_app(Settings(_env_file=None), service, database)) as client:
        result = client.post("/api/essays/analyze", json={"title": "Білім", "essay_text": ESSAY}).json()
        assert result["status"] == "partial" and result["topic_alignment"]
        grade = client.get(f"/api/essays/{result['id']}").json()["sections"]["grade"]
        assert grade["provider_status"] == 400 and grade["error_code"] == "invalid_request"


def test_retry_records_actual_new_model_without_relabeling_cached_topic(database, analysis_json):
    service = Service(analysis_json); service.fail_grade = True
    with TestClient(create_app(Settings(_env_file=None, gemini_model="gemini-3.1-flash-lite"), service, database)) as client:
        first = client.post("/api/essays/analyze", json={"title": "Білім", "essay_text": ESSAY}).json()
    service.fail_grade = False
    with TestClient(create_app(Settings(_env_file=None, gemini_model="gemini-3.5-flash-lite"), service, database)) as client:
        result = client.post(f"/api/essays/{first['id']}/retry/grade").json()
        assert result["model"] == result["sections"]["grade"]["model"] == "gemini-3.5-flash-lite"
        assert result["sections"]["topic"]["model"] == "gemini-3.1-flash-lite"
        assert result["topic_alignment"] == first["topic_alignment"]
        assert service.grade_calls == 2 and service.topic_calls == 1


def test_comparison_exposes_different_grading_models(analysis_json):
    base = {"title": "Білім", "essay_text": ESSAY, "analysis": analysis_json, "total_score": 25, "annotations": []}
    result = version_comparison({**base, "id": 1, "model": "old-model"}, {**base, "id": 2, "model": "new-model"})
    assert result["model_changed"] and result["before_model"] == "old-model" and result["after_model"] == "new-model"


def test_idempotent_submit_and_conflicting_payload(database, analysis_json):
    service = Service(analysis_json)
    request_id = str(uuid4())
    body = {"title": "Білім", "essay_text": ESSAY, "request_id": request_id}
    with TestClient(create_app(Settings(_env_file=None), service, database)) as client:
        first = client.post("/api/essays/analyze", json=body).json()
        repeated = client.post("/api/essays/analyze", json=body).json()
        assert first["id"] == repeated["id"]
        assert service.grade_calls == service.topic_calls == 1
        assert client.post("/api/essays/analyze", json={**body, "title": "Басқа тақырып"}).status_code == 409
        assert len(client.get("/api/essays").json()) == 1


def test_concurrent_duplicate_submissions_create_one_record(database, analysis_json):
    service = Service(analysis_json)
    service.release_topic = threading.Event()
    body = {"title": "Білім", "essay_text": ESSAY, "request_id": str(uuid4())}
    with TestClient(create_app(Settings(_env_file=None), service, database)) as client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: client.post("/api/essays/analyze?background=true", json=body), range(2)))
        assert all(result.status_code in (201, 202) for result in results)
        assert results[0].json()["id"] == results[1].json()["id"]
        service.release_topic.set()
        wait_for(client, results[0].json()["id"])
        assert service.grade_calls == service.topic_calls == 1


def test_concurrent_retries_claim_only_one_ai_call(database, analysis_json):
    service = Service(analysis_json); service.fail_topic = True
    with TestClient(create_app(Settings(_env_file=None), service, database)) as client:
        first = client.post("/api/essays/analyze", json={"title": "Білім", "essay_text": ESSAY}).json()
        service.fail_topic = False; service.release_topic = threading.Event()
        url = f"/api/essays/{first['id']}/retry/topic?background=true"
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: client.post(url), range(2)))
        assert all(response.status_code == 202 for response in responses)
        wait_for(client, first["id"], lambda data: data["sections"]["topic"]["status"] == "pending")
        service.release_topic.set()
        wait_for(client, first["id"])
        assert service.topic_calls == 2 and service.grade_calls == 1


def test_restart_preserves_grade_and_allows_retry(database, analysis_json):
    service = Service(analysis_json); service.release_topic = threading.Event()
    with TestClient(create_app(Settings(_env_file=None), service, database)) as client:
        first = client.post("/api/essays/analyze?background=true", json={"title": "Білім", "essay_text": ESSAY}).json()
        saved = wait_for(client, first["id"], lambda data: data["analysis"] is not None)
    service.release_topic.set()
    with TestClient(create_app(Settings(_env_file=None), service, database)) as client:
        interrupted = client.get(f"/api/essays/{first['id']}").json()
        assert interrupted["analysis"] == saved["analysis"]
        assert interrupted["sections"]["topic"]["status"] == "failed"
        assert client.post(f"/api/essays/{first['id']}/retry/topic").json()["status"] == "ready"


def test_deleting_pending_essay_does_not_recreate_it(database, analysis_json):
    service = Service(analysis_json); service.release_topic = threading.Event()
    with TestClient(create_app(Settings(_env_file=None), service, database)) as client:
        first = client.post("/api/essays/analyze?background=true", json={"title": "Білім", "essay_text": ESSAY}).json()
        wait_for(client, first["id"], lambda data: data["analysis"] is not None)
        assert client.delete(f"/api/essays/{first['id']}").status_code == 204
        service.release_topic.set()
        assert client.get(f"/api/essays/{first['id']}").status_code == 404
        assert client.get("/api/essays").json() == []


def test_expired_attempt_cannot_overwrite_retry(database, analysis_json):
    service = Service(analysis_json)
    with TestClient(create_app(Settings(_env_file=None), service, database)) as client:
        first = client.post("/api/essays/analyze", json={"title": "Білім", "essay_text": ESSAY}).json()
        with database.session.begin() as session:
            work = session.get(EssayWork, first["id"])
            state = copy.deepcopy(work.state)
            state["sections"]["topic"] = {"status": "pending", "attempt": "old", "deadline": 0}
            state["topic_alignment"] = None
            work.state = state
        expired = client.get(f"/api/essays/{first['id']}").json()
        assert expired["sections"]["topic"]["status"] == "failed"
        current = client.post(f"/api/essays/{first['id']}/retry/topic").json()
        finish_section(database, first["id"], "topic", "old", error=AIError("Late old error"))
        assert client.get(f"/api/essays/{first['id']}").json() == current


def test_revision_comparison_and_parent_deletion(database, analysis_json):
    service = Service(analysis_json)
    with TestClient(create_app(Settings(_env_file=None), service, database)) as client:
        first = client.post("/api/essays/analyze", json={"title": "Білім", "essay_text": ESSAY}).json()
        improved = ESSAY + " Білімді тәжірибеде қолдану да маңызды."
        second = client.post("/api/essays/analyze", json={"title": "Білім", "essay_text": improved, "parent_id": first["id"]}).json()
        assert client.get(f"/api/essays/{first['id']}").json()["essay_text"] == ESSAY
        comparison = client.get(f"/api/essays/{second['id']}/comparison").json()
        assert comparison["before_id"] == first["id"] and comparison["after_id"] == second["id"]
        assert comparison["text_changed"] and len(comparison["score_changes"]) == 5
        assert ''.join(p["text"] for p in comparison["after_parts"]) == improved
        assert [v["id"] for v in client.get(f"/api/essays/{second['id']}/versions").json()] == [second["id"], first["id"]]
        assert client.get(f"/api/essays/{first['id']}/comparison?before_id={second['id']}").status_code == 422
        assert client.post("/api/essays/analyze", json={"title": "Білім", "essay_text": ESSAY, "parent_id": 999999}).status_code == 404
        client.delete(f"/api/essays/{first['id']}")
        assert client.get(f"/api/essays/{second['id']}").json()["parent_id"] is None


def test_legacy_analysis_can_fill_missing_topic_without_regrading(database, analysis_json):
    database.initialize()
    with database.session.begin() as session:
        old = Essay(title="Ескі эссе", essay_text=ESSAY, word_count=20)
        session.add(old); session.flush()
        old.analysis = EssayAnalysis(essay_style="Ғылыми", essay_type="Аналитикалық", total_score=25,
            structure_score=5, argumentation_score=5, logic_score=5, evidence_score=5, language_score=5,
            analysis_result={"analysis": analysis_json, "total_score": 25, "max_scores": {"structure":25,"argumentation":30,"logic":20,"evidence":15,"language":10}, "markers": [], "model": "legacy-model"})
        old_id = old.id
    service = Service(analysis_json)
    with TestClient(create_app(Settings(_env_file=None), service, database)) as client:
        legacy = client.get(f"/api/essays/{old_id}").json()
        assert legacy["sections"]["topic"]["status"] == "not_run"
        assert client.post(f"/api/essays/{old_id}/retry/topic").json()["topic_alignment"]
        assert service.grade_calls == 0 and service.topic_calls == 1


def test_annotations_have_exact_unicode_offsets_and_do_not_invent_errors(analysis_json):
    essay = "😀 Мен кітап оқиды. Мен кітап оқиды."
    analysis_json["language_corrections"] = [{"original": "Мен кітап оқиды.", "corrected": "Мен кітап оқимын.", "explanation": "Жақ сәйкестігі."}]
    annotations = annotations_for(essay, analysis_json)
    assert len(annotations) == 1
    item = annotations[0]
    assert essay[item["start"]:item["end"]] == item["quote"] == "Мен кітап оқиды."
    assert item["occurrences"] == 2
    assert annotations_for(essay, None) == []
    analysis_json["language_corrections"][0]["original"] = "Ойдан шығарылған сөйлем."
    assert annotations_for(essay, analysis_json) == []


def test_feedback_quotes_are_validated(analysis_json):
    analysis_json["text_feedback"] = [{"category": "argumentation", "quote": "Ойдан шығарылған сөйлем.", "explanation": "Әлсіз ой.", "suggestion": "Негіздеңіз."}]
    with pytest.raises(ValueError):
        validate_quotes(AIAnalysis.model_validate(analysis_json), ESSAY)


def test_comparison_does_not_claim_a_rewritten_error_is_fixed(analysis_json):
    before = {"id":1,"title":"Білім","essay_text":"Мен кітап оқиды.","analysis":None,"total_score":None,
              "annotations":[{"quote":"Мен кітап оқиды.","explanation":"Жақ қатесі."}]}
    after = {"id":2,"title":"Білім","essay_text":"Мен кітап оқисың.","analysis":analysis_json,"total_score":25,"annotations":[]}
    comparison = version_comparison(before, after)
    assert comparison["observations"][0]["status"] == "revised_quote"
    assert comparison["total_delta"] is None
    assert ''.join(p["text"] for p in comparison["before_parts"]) == before["essay_text"]
    assert ''.join(p["text"] for p in comparison["after_parts"]) == after["essay_text"]


def test_topic_segments_preserve_long_source_and_line_breaks():
    essay = "Қате сөйлем осында\n" + "а" * 2100 + ". Соңғы сөйлем."
    parts = topic_segments(essay)
    assert parts and all(part in essay and len(part) <= 1800 for part in parts)
