"""Isolated smoke-test server with explicit AI fixtures; never used by backend.main."""
import asyncio
import copy
import os
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.ai_analyzer import AIError
from backend.config import Settings
from backend.database import Database
from backend.main import create_app
from backend.schemas import AIAnalysis, TopicAlignment
from test_analysis import analysis_json
from test_accounts import Mailbox
from backend.auth_models import AuthLimit, now
from sqlalchemy import update


class FixtureService:
    def __init__(self):
        self.grades = self.topics = 0

    async def analyze(self, title, essay, markers):
        self.grades += 1
        await asyncio.sleep(0.15)
        data = copy.deepcopy(analysis_json.__wrapped__())
        quote = "Мен кітап оқиды." if "Мен кітап оқиды." in essay else "Мен кітап оқимын."
        data["classification"]["quotes"] = [quote]
        for criterion in data["criteria"].values():
            criterion["score"] = 5 if "Мен кітап оқиды." in essay else 7
            criterion["quotes"] = [quote]
        for key in data["structure_map"]:
            data["structure_map"][key] = []
        if "Мен кітап оқиды." in essay:
            data["language_corrections"] = [{"original": quote, "corrected": "Мен кітап оқимын.", "explanation": "Бастауыш пен баяндауыштың жағы сәйкес емес."}]
            data["text_feedback"] = [{"category": "argumentation", "quote": "Кітап пайдалы.", "explanation": "Пікірдің себебі түсіндірілмеген.", "suggestion": "Неліктен пайдалы екенін түсіндіріңіз."}]
        return AIAnalysis.model_validate(data)

    async def compare_topic(self, title, essay):
        self.topics += 1
        await asyncio.sleep(0.3)
        if self.topics == 1:
            raise AIError("Тест: тақырып тексеруі уақытша орындалмады.")
        return TopicAlignment(status="mismatched", explanation="Бұл — тек браузер тестінің жауабы.",
                              quotes=["Мен кітап оқимын." if "Мен кітап оқимын." in essay else "Мен кітап оқиды."],
                              suggested_title="Кітап оқудың пайдасы")


if __name__ == "__main__":
    if os.getenv("RUN_BROWSER_TESTS") != "1":
        raise SystemExit("Set RUN_BROWSER_TESTS=1; this server is for tests only.")
    url = Settings().database_url.get_secret_value()
    schema = "browser_test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    db = Database(url)
    db.engine.dispose()
    db.engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    db.session = sessionmaker(db.engine, expire_on_commit=False)
    service = FixtureService()
    mailbox = Mailbox()
    app = create_app(Settings(_env_file=None, gemini_api_key="", bootstrap_admin_email="owner@example.com"), service, db, mailbox)

    @app.get("/__test__/mail")
    def last_mail():
        return mailbox.sent[-1] if mailbox.sent else {}

    @app.post("/__test__/allow-mail")
    def allow_mail():
        from datetime import timedelta
        with db.session.begin() as session:
            session.execute(update(AuthLimit).values(reset_at=now() - timedelta(seconds=1)))
        return {"ok": True}

    @app.get("/__test__/counts")
    def counts():
        return {"grades": service.grades, "topics": service.topics}

    try:
        uvicorn.run(app, host="127.0.0.1", port=8002, log_level="warning")
    finally:
        db.engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
