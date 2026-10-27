"""Account and admin endpoints. Public responses never serialize ORM credentials."""
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from .accounts import (COOKIE, AccountService, RegisterInput, LoginInput, IdentifierInput, ChallengeInput, VerifyInput,
                       AvailabilityInput, ProfileInput, EmailInput, PasswordInput, DeleteInput,
                       service, current_user, admin_user, user_data, username_value, email_address,
                       hash_secret, check_secret, token_hash, problem)
from .auth_models import User, LoginSession, Challenge, EssayOwner, MailDelivery, Audit, now
from .models import Essay, EssayWork


def signed_in(svc, user):
    response = JSONResponse({"user": user_data(user)})
    response.set_cookie(COOKIE, svc.new_session(user.id), max_age=svc.settings.session_days * 86400,
                        httponly=True, secure=svc.settings.cookie_secure, samesite="lax", path="/")
    return response


def clear_cookie(response):
    response.delete_cookie(COOKIE, path="/")
    return response


def ip_limit(request, label, maximum=30, seconds=3600):
    service(request).limit(label + ":" + (request.client.host if request.client else "unknown"), maximum, seconds)


def install_accounts(app):
    @app.get("/api/auth/me")
    def me(request: Request):
        svc = service(request)
        user = svc.session_user(request.cookies.get(COOKIE))
        return {"user": user_data(user) if user else None}

    @app.post("/api/auth/register")
    def register(body: RegisterInput, request: Request):
        ip_limit(request, "register", 20)
        return service(request).register(body)

    @app.post("/api/auth/validate")
    def validate(body: AvailabilityInput, request: Request):
        ip_limit(request, "validate", 180, 60)
        result = {}
        with service(request).db.session() as session:
            for field, raw, normalizer in (("username", body.username, username_value), ("email", body.email, email_address)):
                if raw is None:
                    continue
                try:
                    value = normalizer(raw)
                except ValueError as exc:
                    result[field] = {"valid": False, "message": str(exc)}
                    continue
                exists = session.scalar(select(User.id).where(getattr(User, field) == value))
                result[field] = {"valid": not bool(exists), "message": "Қолжетімді." if not exists else "Бұл логин/email қолданылған."}
        return result

    @app.post("/api/auth/verify")
    def verify(body: VerifyInput, request: Request):
        ip_limit(request, "verify", 80)
        svc = service(request)
        user, purpose = svc.verify(body, svc.session_user(request.cookies.get(COOKIE)))
        if purpose == "register":
            return signed_in(svc, user)
        if purpose == "reset":
            return clear_cookie(JSONResponse({"user": None, "message": "Құпиясөз өзгертілді. Қайта кіріңіз."}))
        return {"user": user_data(user), "message": "Жаңа пошта расталды."}

    @app.post("/api/auth/resend")
    def resend(body: ChallengeInput, request: Request):
        ip_limit(request, "resend", 20)
        svc = service(request)
        with svc.db.session() as session:
            row = session.get(Challenge, body.challenge_id)
            if row and row.purpose == "email":
                actor = current_user(request)
                if row.user_id != actor.id:
                    raise HTTPException(403, "Рұқсат жоқ.")
        return svc.resend(body.challenge_id)

    @app.post("/api/auth/login")
    def login(body: LoginInput, request: Request):
        ip_limit(request, "login", 80)
        svc = service(request)
        return signed_in(svc, svc.login(body))

    @app.post("/api/auth/forgot")
    def forgot(body: IdentifierInput, request: Request):
        ip_limit(request, "forgot", 20)
        return service(request).forgot(body.identifier)

    @app.post("/api/auth/logout")
    def logout(request: Request):
        svc = service(request)
        token = request.cookies.get(COOKIE)
        if token:
            with svc.db.session.begin() as session:
                session.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(token)))
        return clear_cookie(JSONResponse({"message": "Аккаунттан шықтыңыз."}))

    @app.post("/api/auth/logout-all")
    def logout_all(request: Request, user=Depends(current_user)):
        with service(request).db.session.begin() as session:
            session.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
        return clear_cookie(JSONResponse({"message": "Барлық құрылғыдан шықтыңыз."}))

    @app.get("/api/account")
    def profile(request: Request, user=Depends(current_user)):
        with service(request).db.session() as session:
            count = session.scalar(select(func.count()).select_from(EssayOwner).where(EssayOwner.user_id == user.id))
            latest = session.scalar(select(Essay).join(EssayOwner).where(EssayOwner.user_id == user.id).order_by(Essay.id.desc()))
        return {"user": user_data(user), "essay_count": count,
                "last_essay_at": latest.created_at.isoformat() if latest else None}

    @app.patch("/api/account")
    def update_profile(body: ProfileInput, request: Request, user=Depends(current_user)):
        svc = service(request)
        try:
            with svc.db.session.begin() as session:
                svc.unique(session, username=body.username, except_id=user.id)
                saved = session.get(User, user.id)
                saved.username = body.username
                session.add(Audit(actor_id=user.id, target_id=user.id, action="username_changed"))
            return {"user": user_data(saved)}
        except IntegrityError:
            problem("Бұл логин қолданылған.", 409, "username")

    @app.post("/api/account/email")
    def change_email(body: EmailInput, request: Request, user=Depends(current_user)):
        svc = service(request)
        ip_limit(request, "change-email", 20)
        if not check_secret(body.password, user.password_hash):
            problem("Қазіргі құпиясөз дұрыс емес.", 401, "password")
        with svc.db.session() as session:
            svc.unique(session, email=body.email)
        return svc.issue("email", body.email, user_id=user.id)

    @app.post("/api/account/password")
    def change_password(body: PasswordInput, request: Request, user=Depends(current_user)):
        svc = service(request)
        ip_limit(request, "change-password", 20)
        if not check_secret(body.current_password, user.password_hash):
            problem("Қазіргі құпиясөз дұрыс емес.", 401, "current_password")
        with svc.db.session.begin() as session:
            session.get(User, user.id).password_hash = hash_secret(body.new_password)
            session.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
            session.execute(delete(Challenge).where(Challenge.user_id == user.id))
            session.add(Audit(actor_id=user.id, target_id=user.id, action="password_changed"))
        return signed_in(svc, user)

    @app.delete("/api/account")
    def delete_account(body: DeleteInput, request: Request, user=Depends(current_user)):
        svc = service(request)
        ip_limit(request, "delete-account", 10)
        if body.username != user.username or not check_secret(body.password, user.password_hash):
            problem("Логин немесе құпиясөз дұрыс емес.", 401)
        if user.role == "admin":
            problem("Әкімші аккаунтын жою алдында әкімші құқығын басқа жауапты адамға беру қажет.", 409)
        with svc.db.session.begin() as session:
            ids = select(EssayOwner.essay_id).where(EssayOwner.user_id == user.id)
            for essay in session.scalars(select(Essay).where(Essay.id.in_(ids))):
                session.delete(essay)
            session.add(Audit(actor_id=user.id, target_id=user.id, action="account_deleted"))
            session.flush()
            session.execute(delete(User).where(User.id == user.id))
        return clear_cookie(JSONResponse({"message": "Аккаунт және оның эсселері жойылды."}))

    @app.get("/api/admin/overview")
    def overview(request: Request, user=Depends(admin_user)):
        with service(request).db.session() as session:
            users = session.scalar(select(func.count()).select_from(User))
            blocked = session.scalar(select(func.count()).select_from(User).where(User.blocked.is_(True)))
            essays = session.scalar(select(func.count()).select_from(Essay))
            unowned = session.scalar(select(func.count()).select_from(Essay).outerjoin(EssayOwner).where(EssayOwner.essay_id.is_(None)))
            states = session.scalars(select(EssayWork.state)).all()
            failed = sum(any(s.get("status") == "failed" for s in state.get("sections", {}).values()) for state in states)
            pending = session.scalar(select(func.count()).select_from(Challenge).where(Challenge.purpose == "register", Challenge.consumed_at.is_(None), Challenge.expires_at > now()))
        return {"users": users, "blocked": blocked, "essays": essays, "unowned": unowned,
                "failed_analyses": failed, "pending_registrations": pending}

    @app.get("/api/admin/users")
    def users(request: Request, q: str = "", offset: int = 0, user=Depends(admin_user)):
        with service(request).db.session() as session:
            query = select(User).order_by(User.id.desc()).offset(max(offset, 0)).limit(30)
            if q:
                # Escape wildcard characters; a search string is never SQL syntax.
                query = query.where(User.username.contains(q.casefold(), autoescape=True) | User.email.contains(q.casefold(), autoescape=True))
            return [user_data(row) for row in session.scalars(query)]

    class BlockInput(BaseModel):
        blocked: bool

    @app.patch("/api/admin/users/{user_id}")
    def block(user_id: int, body: BlockInput, request: Request, admin=Depends(admin_user)):
        with service(request).db.session.begin() as session:
            user = session.get(User, user_id)
            if not user:
                raise HTTPException(404, "Пайдаланушы табылмады.")
            if user.role == "admin":
                problem("Әкімші аккаунтын бұл әрекетпен бұғаттау мүмкін емес.", 409)
            user.blocked = body.blocked
            if user.blocked:
                session.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
                session.execute(delete(Challenge).where(Challenge.user_id == user.id))
            session.add(Audit(actor_id=admin.id, target_id=user.id, action="user_blocked" if body.blocked else "user_unblocked"))
        return {"user": user_data(user)}

    @app.get("/api/admin/pending")
    def pending(request: Request, user=Depends(admin_user)):
        with service(request).db.session() as session:
            return [{"id": row.id, "purpose": row.purpose, "email": row.email, "username": row.username,
                     "attempts": row.attempts, "expired": row.expires_at <= now(), "expires_at": row.expires_at.isoformat()}
                    for row in session.scalars(select(Challenge).where(Challenge.consumed_at.is_(None)).order_by(Challenge.sent_at.desc()).limit(100))]

    @app.get("/api/admin/services")
    def services(request: Request, user=Depends(admin_user)):
        svc = service(request)
        svc.db.ping()
        with svc.db.session() as session:
            deliveries = [{"status": row.status, "purpose": row.purpose, "created_at": row.created_at.isoformat()}
                          for row in session.scalars(select(MailDelivery).order_by(MailDelivery.id.desc()).limit(20))]
            errors = []
            for work in session.scalars(select(EssayWork).order_by(EssayWork.essay_id.desc()).limit(100)):
                for section, state in work.state.get("sections", {}).items():
                    if state.get("status") == "failed":
                        errors.append({"essay_id": work.essay_id, "section": section,
                                       "error": state.get("error"), "provider_status": state.get("provider_status")})
        return {"database": True, "smtp_configured": svc.mailer.configured,
                "gemini_configured": bool(svc.settings.gemini_api_key.get_secret_value()),
                "model": svc.settings.gemini_model, "deliveries": deliveries, "errors": errors}

    @app.get("/api/admin/audit")
    def audit(request: Request, user=Depends(admin_user)):
        with service(request).db.session() as session:
            return [{"id": row.id, "actor_id": row.actor_id, "target_id": row.target_id,
                     "action": row.action, "created_at": row.created_at.isoformat()}
                    for row in session.scalars(select(Audit).order_by(Audit.id.desc()).limit(100))]
