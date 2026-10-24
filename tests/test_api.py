import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from backend.ai_analyzer import AIError
from backend.config import Settings
from backend.database import Database
from backend.main import create_app
from backend.schemas import AIAnalysis
from test_analysis import ESSAY, analysis_json  # noqa: F401 — shared fixture


@pytest.fixture
def database():
    if os.getenv("RUN_DATABASE_TESTS") != "1":
        pytest.skip("Set RUN_DATABASE_TESTS=1 to exercise real PostgreSQL in an isolated schema")
    url = Settings().database_url.get_secret_value()
    schema = "test_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    db = Database(url)
    db.engine.dispose()
    db.engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    db.session = sessionmaker(db.engine, expire_on_commit=False)
    yield db
    db.engine.dispose()
    with admin.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


def test_ui_and_missing_database():
    with TestClient(create_app(Settings(_env_file=None, database_url="", gemini_api_key=""))) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/script.js").status_code == 200
        assert client.get("/.env").status_code == 404
        assert client.get("/api/health").status_code == 503
        assert client.post("/api/essays/analyze", json={"title": "Сынақ", "essay_text": " "}).status_code == 422
        assert client.post("/api/essays/analyze", json={"title": "Сынақ", "essay_text": ESSAY}).status_code == 503
        assert client.get("/api/essays").status_code == 503
        assert client.post("/api/ocr", files={"image": ("bad.png", b"not an image", "image/png")}).status_code == 422


def test_full_postgres_roundtrip(database, analysis_json):
    class FixtureAnalyzer:
        async def analyze(self, title, essay, markers):
            assert essay == ESSAY
            return AIAnalysis.model_validate(analysis_json)

    with TestClient(create_app(Settings(_env_file=None), analyzer=FixtureAnalyzer(), database=database)) as client:
        response = client.post("/api/essays/analyze", json={"title": "Тест эссесі", "essay_text": ESSAY})
        assert response.status_code == 201, response.text
        result = response.json()
        assert result["total_score"] == 25
        assert result["essay_text"] == ESSAY
        assert result["markers"]
        essay_id = result["id"]
        assert client.get(f"/api/essays/{essay_id}").json() == result
        assert client.get("/api/essays").json()[0]["id"] == essay_id
        assert client.get("/api/essays?limit=0").status_code == 422
        assert client.delete(f"/api/essays/{essay_id}").status_code == 204
        assert client.get(f"/api/essays/{essay_id}").status_code == 404
        assert client.get("/api/essays").json() == []
        with database.session() as session:
            assert session.execute(text("SELECT count(*) FROM essay_analyses")).scalar() == 0


def test_provider_failure_saves_nothing(database):
    class FailingAnalyzer:
        async def analyze(self, *args):
            raise AIError("Gemini сұрау лимиті аяқталды.", 429)
    with TestClient(create_app(Settings(_env_file=None), analyzer=FailingAnalyzer(), database=database)) as client:
        assert client.post("/api/essays/analyze", json={"title": "Сынақ", "essay_text": ESSAY}).status_code == 429
        assert client.get("/api/essays").json() == []


def test_database_connection_failure_does_not_call_ai():
    class BrokenDatabase:
        def initialize(self):
            raise OperationalError("test", None, Exception("private database password"))
    class ForbiddenAnalyzer:
        async def analyze(self, *args):
            pytest.fail("AI must not be charged when database is unavailable")
    db = BrokenDatabase()
    db.engine = type("Engine", (), {"dispose": lambda self: None})()
    with TestClient(create_app(Settings(_env_file=None), analyzer=ForbiddenAnalyzer(), database=db)) as client:
        response = client.post("/api/essays/analyze", json={"title": "Сынақ", "essay_text": ESSAY})
        assert response.status_code == 503
        assert "private" not in response.text
