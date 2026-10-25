"""Persist sections independently and safely claim retries across requests/workers."""
import copy
import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from .feedback import annotations_for
from .models import Essay, EssayAnalysis, EssayWork
from .schemas import MAX_SCORES

SECTIONS = ("grade", "topic")


def pending_section(timeout: float, attempts: int = 1) -> dict:
    return {"status": "pending", "attempt": str(uuid4()), "attempts": attempts,
            "deadline": time.time() + timeout + 30}


def expire_pending(work: EssayWork):
    state = copy.deepcopy(work.state)
    changed = False
    for name in SECTIONS:
        section = state["sections"][name]
        if section["status"] == "pending" and section["deadline"] < time.time():
            state["sections"][name] = {**section, "status": "failed",
                "error": "Тексеру үзіліп қалды. Тек осы бөлімді қайта тексеріңіз.", "retry_at": 0}
            changed = True
    if changed:
        work.state = state
        work.revision += 1


def snapshot(essay: Essay) -> dict:
    base = {"id": essay.id, "title": essay.title, "essay_text": essay.essay_text,
            "word_count": essay.word_count, "created_at": essay.created_at.isoformat()}
    if essay.work:
        state = copy.deepcopy(essay.work.state)
        sections = state["sections"]
        statuses = [sections[name]["status"] for name in SECTIONS]
        status = "pending" if "pending" in statuses else "ready" if all(s == "ready" for s in statuses) else "partial" if "ready" in statuses else "failed"
        return {**base, **state, "status": status, "parent_id": essay.work.parent_id, "revision": essay.work.revision}
    saved = copy.deepcopy(essay.analysis.analysis_result)
    return {**base, **saved, "status": "ready", "parent_id": None, "revision": 0,
            "annotations": annotations_for(essay.essay_text, saved.get("analysis")),
            "sections": {"grade": {"status": "ready"},
                         "topic": {"status": "ready" if saved.get("topic_alignment") else "not_run"}}}


def read_snapshot(db, essay_id: int) -> dict:
    with db.session.begin() as session:
        work = session.scalar(select(EssayWork).where(EssayWork.essay_id == essay_id).with_for_update())
        if work:
            expire_pending(work)
        essay = session.get(Essay, essay_id)
        if not essay:
            raise HTTPException(404, "Эссе табылмады.")
        return snapshot(essay)


def finish_section(db, essay_id: int, name: str, attempt: str, *, result=None, error=None, model=None):
    with db.session.begin() as session:
        work = session.scalar(select(EssayWork).where(EssayWork.essay_id == essay_id).with_for_update())
        if not work:
            return  # The user deleted the essay while AI was running.
        state = copy.deepcopy(work.state)
        section = state["sections"][name]
        if section.get("attempt") != attempt or section["status"] != "pending":
            return  # An expired/older worker cannot overwrite a newer retry.
        if error:
            state["sections"][name] = {**section, "status": "failed", "error": error.message,
                "model": model,
                "provider_status": error.provider_status, "error_code": error.error_code,
                "retry_at": time.time() + (60 if error.status == 429 else 0)}
        else:
            state["sections"][name] = {**section, "status": "ready", "model": model}
            if name == "topic":
                state["topic_alignment"] = result.model_dump()
            else:
                analysis = result.model_dump()
                if model:
                    state["model"] = model
                scores = {key: value["score"] for key, value in analysis["criteria"].items()}
                state.update(analysis=analysis, total_score=sum(scores.values()),
                             annotations=annotations_for(session.get(Essay, essay_id).essay_text, analysis))
                stored = session.scalar(select(EssayAnalysis).where(EssayAnalysis.essay_id == essay_id))
                if stored is None:
                    stored = EssayAnalysis(essay_id=essay_id)
                    session.add(stored)
                stored.essay_style = analysis["classification"]["style"]
                stored.essay_type = analysis["classification"]["essay_type"]
                stored.total_score = state["total_score"]
                for key, score in scores.items():
                    setattr(stored, f"{key}_score", score)
                stored.analysis_result = {"analysis": analysis, "total_score": state["total_score"],
                                          "max_scores": MAX_SCORES, "markers": state["markers"], "model": state["model"]}
        work.state = state
        work.revision += 1


def claim_retry(db, essay_id: int, name: str, timeout: float) -> dict:
    with db.session.begin() as session:
        essay = session.get(Essay, essay_id)
        if not essay:
            raise HTTPException(404, "Эссе табылмады.")
        work = session.scalar(select(EssayWork).where(EssayWork.essay_id == essay_id).with_for_update())
        if work is None:
            # Legacy grades are retained; permit filling only the missing topic section.
            old = snapshot(essay)
            work = EssayWork(essay_id=essay_id, revision=1, state={key: old.get(key) for key in
                ("analysis", "total_score", "max_scores", "markers", "model", "annotations", "topic_alignment", "sections")})
            session.add(work)
            session.flush()
        expire_pending(work)
        state = copy.deepcopy(work.state)
        section = state["sections"][name]
        if section["status"] in ("ready", "pending"):
            return {"claimed": False}
        if section.get("retry_at", 0) > time.time():
            raise HTTPException(429, "Сұрау лимитінен кейін кемінде бір минут күтіңіз.")
        state["sections"][name] = pending_section(timeout, section.get("attempts", 0) + 1)
        work.state = state
        work.revision += 1
        return {"claimed": True, "section": state["sections"][name], "title": essay.title,
                "essay_text": essay.essay_text, "markers": state["markers"]}
