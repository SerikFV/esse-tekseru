"""Verified registration, opaque database sessions and account ownership."""
import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from uuid import uuid4

from email_validator import EmailNotValidError, validate_email
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, model_validator, field_validator
from sqlalchemy import delete, select, text, func
from sqlalchemy.exc import IntegrityError

from .auth_models import User, LoginSession, Challenge, EssayOwner, AuthLimit, MailDelivery, Audit, now
from .models import Essay

COOKIE = "tekser_session"
USERNAME = re.compile(r"^[a-zA-Z0-9_]{3,30}$")


def problem(message, status=422, field=None):
    raise HTTPException(status, {"message": message, "fields": {field: message} if field else {}})


def email_address(value):
    try:
        result = validate_email(value.strip(), check_deliverability=False)
        return result.normalized.casefold()
    except EmailNotValidError:
        raise ValueError("Email мекенжайының пішімі дұрыс емес.") from None


def username_value(value):
    if not USERNAME.fullmatch(value):
        raise ValueError("Логин 3–30 латын әрпінен, саннан немесе _ белгісінен тұруы керек.")
    return value.lower()


def hash_secret(value):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(value.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return "scrypt$" + salt.hex() + "$" + digest.hex()


def check_secret(value, stored):
    try:
        _, salt, expected = stored.split("$")
        actual = hashlib.scrypt(value.encode("utf-8"), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


DUMMY_HASH = hash_secret("not-an-account-password")


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def lock(session, key):
    number = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": number})


def user_data(user):
    return {"id": user.id, "username": user.username, "email": user.email, "role": user.role,
            "blocked": user.blocked, "email_verified": True, "created_at": user.created_at.isoformat()}


class RegisterInput(BaseModel):
    username: str
    email: str = Field(max_length=254)
    password: str = Field(min_length=4)
    password_confirmation: str
    _username = field_validator("username")(username_value)
    _email = field_validator("email")(email_address)

    @model_validator(mode="after")
    def same_password(self):
        if self.password != self.password_confirmation:
            raise ValueError("Құпиясөздер сәйкес емес.")
        return self


class LoginInput(BaseModel):
    identifier: str = Field(min_length=1, max_length=254)
    password: str


class IdentifierInput(BaseModel):
    identifier: str = Field(min_length=1, max_length=254)


class ChallengeInput(BaseModel):
    challenge_id: str = Field(pattern=r"^[0-9a-f-]{36}$")


class VerifyInput(ChallengeInput):
    code: str = Field(pattern=r"^[0-9]{6}$")
    new_password: str | None = None
    password_confirmation: str | None = None


class AvailabilityInput(BaseModel):
    username: str | None = None
    email: str | None = Field(default=None, max_length=254)


class ProfileInput(BaseModel):
    username: str
    _username = field_validator("username")(username_value)


class EmailInput(BaseModel):
    email: str = Field(max_length=254)
    password: str
    _email = field_validator("email")(email_address)


class PasswordInput(BaseModel):
    current_password: str
    new_password: str = Field(min_length=4)
    password_confirmation: str

    @model_validator(mode="after")
    def same_password(self):
        if self.new_password != self.password_confirmation:
            raise ValueError("Құпиясөздер сәйкес емес.")
        return self


class DeleteInput(BaseModel):
    password: str
    username: str


class AccountService:
    def __init__(self, db, settings, mailer):
        self.db, self.settings, self.mailer = db, settings, mailer

    def limit(self, key, maximum, seconds):
        key = token_hash(key)
        exceeded = False
        with self.db.session.begin() as session:
            lock(session, key)
            row = session.get(AuthLimit, key)
            if row is None:
                row = AuthLimit(key=key, count=0, reset_at=now() + timedelta(seconds=seconds))
                session.add(row)
            elif row.reset_at <= now():
                row.count, row.reset_at = 0, now() + timedelta(seconds=seconds)
            row.count += 1
            exceeded = row.count > maximum
            session.execute(delete(AuthLimit).where(AuthLimit.reset_at < now() - timedelta(days=1)))
        if exceeded:
            problem("Әрекеттер тым жиі орындалды. Кейінірек қайталаңыз.", 429)

    def unique(self, session, username=None, email=None, except_id=None):
        for field, value in (("username", username), ("email", email)):
            if value is None:
                continue
            query = select(User).where(getattr(User, field) == value)
            if except_id:
                query = query.where(User.id != except_id)
            if session.scalar(query):
                problem("Бұл логин қолданылған." if field == "username" else "Бұл email тіркелген.", 409, field)

    def send(self, email, code, purpose):
        try:
            self.mailer.send_code(email, code, purpose)
        except Exception:
            with self.db.session.begin() as session:
                session.add(MailDelivery(purpose=purpose, status="failed"))
            raise HTTPException(503, "Растау хатын жіберу мүмкін болмады. SMTP баптауын тексеріп, кейінірек қайталаңыз.") from None

    def issue(self, purpose, email, *, username=None, password_hash=None, user_id=None):
        # Durable mailbox cooldown shared by resend/register/reset and all workers.
        self.limit("mail:" + email, 1, 60)
        code = f"{secrets.randbelow(1000000):06d}"
        challenge_id = str(uuid4())
        # Deliver first; no account or usable challenge exists if delivery fails.
        self.send(email, code, purpose)
        with self.db.session.begin() as session:
            lock(session, "challenge:" + email)
            session.execute(delete(Challenge).where(Challenge.expires_at < now() - timedelta(days=1)))
            for old in session.scalars(select(Challenge).where(Challenge.email == email, Challenge.purpose == purpose,
                                                             Challenge.consumed_at.is_(None))):
                old.consumed_at = now()
            session.add(Challenge(id=challenge_id, purpose=purpose, email=email, username=username,
                                  password_hash=password_hash, user_id=user_id, code_hash=hash_secret(code),
                                  expires_at=now() + timedelta(minutes=10)))
            session.add(MailDelivery(purpose=purpose, status="accepted"))
        return {"challenge_id": challenge_id, "email": email, "expires_in": 600, "resend_after": 60}

    def register(self, body):
        with self.db.session() as session:
            self.unique(session, body.username, body.email)
        return self.issue("register", body.email, username=body.username, password_hash=hash_secret(body.password))

    def resend(self, challenge_id):
        with self.db.session() as session:
            row = session.get(Challenge, challenge_id)
            if not row or row.consumed_at:
                problem("Бұл сұрау жарамсыз. Қайта бастаңыз.", 400)
            values = {key: getattr(row, key) for key in ("purpose", "email", "username", "password_hash", "user_id")}
        return self.issue(**values)

    def verify(self, body, actor=None):
        failed = False
        try:
            with self.db.session.begin() as session:
                row = session.scalar(select(Challenge).where(Challenge.id == body.challenge_id).with_for_update())
                if not row or row.consumed_at or row.expires_at <= now() or row.attempts >= 5:
                    problem("Код жарамсыз, мерзімі өткен немесе әрекет саны аяқталған. Жаңа код сұратыңыз.", 400, "code")
                if row.purpose == "email" and (not actor or actor.id != row.user_id):
                    problem("Бұл сұрауға рұқсат жоқ.", 403)
                if row.purpose == "reset" and (body.new_password is None or len(body.new_password) < 4 or body.new_password != body.password_confirmation):
                    problem("Жаңа құпиясөз кемінде 4 таңба болып, қайталауымен сәйкес келуі керек.", field="new_password")
                row.attempts += 1
                if not check_secret(body.code, row.code_hash):
                    failed = True
                else:
                    if row.purpose == "register":
                        self.unique(session, row.username, row.email)
                        admin = bool(self.settings.bootstrap_admin_email and
                                     row.email == self.settings.bootstrap_admin_email.casefold() and
                                     not session.scalar(select(User.id).where(User.role == "admin")))
                        user = User(username=row.username, email=row.email, password_hash=row.password_hash,
                                    role="admin" if admin else "user")
                        session.add(user)
                        session.flush()
                    else:
                        user = session.get(User, row.user_id)
                        if not user or user.blocked:
                            problem("Аккаунт қолжетімсіз.", 403)
                        if row.purpose == "reset":
                            if user.email != row.email:
                                problem("Пошта өзгерген. Жаңа қалпына келтіру кодын сұратыңыз.", 400)
                            user.password_hash = hash_secret(body.new_password)
                            session.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
                            session.add(Audit(actor_id=user.id, target_id=user.id, action="password_reset"))
                        else:
                            self.unique(session, email=row.email, except_id=user.id)
                            user.email = row.email
                            session.add(Audit(actor_id=user.id, target_id=user.id, action="email_changed"))
                        session.execute(delete(Challenge).where(Challenge.user_id == user.id, Challenge.id != row.id))
                    row.consumed_at = now()
                    row.password_hash = None
                    row.code_hash = "consumed"
                    # Registration completes once, and only after the code check.
                    result = (user, row.purpose)
            if failed:
                problem("Код дұрыс емес. Қайталап енгізіңіз.", 400, "code")
            return result
        except IntegrityError:
            problem("Логин немесе email басқа аккаунтқа тіркеліп үлгерді. Қайта тексеріңіз.", 409)

    def login(self, body):
        identifier = body.identifier.strip().casefold()
        self.limit("login:" + identifier, 10, 300)
        with self.db.session() as session:
            user = session.scalar(select(User).where((User.username == identifier) | (User.email == identifier)))
            valid = check_secret(body.password, user.password_hash if user else DUMMY_HASH)
            if not valid or not user:
                problem("Логин/email немесе құпиясөз дұрыс емес.", 401)
            if user.blocked:
                problem("Аккаунт бұғатталған. Әкімшіге хабарласыңыз.", 403)
            return user

    def new_session(self, user_id):
        token = secrets.token_urlsafe(32)
        with self.db.session.begin() as session:
            session.execute(delete(LoginSession).where(LoginSession.expires_at < now()))
            session.add(LoginSession(token_hash=token_hash(token), user_id=user_id,
                                     expires_at=now() + timedelta(days=self.settings.session_days)))
        return token

    def session_user(self, token):
        if not token:
            return None
        with self.db.session() as session:
            return session.scalar(select(User).join(LoginSession, LoginSession.user_id == User.id).where(
                LoginSession.token_hash == token_hash(token), LoginSession.expires_at > now(), User.blocked.is_(False)))

    def forgot(self, identifier):
        identifier = identifier.strip().casefold()
        with self.db.session() as session:
            user = session.scalar(select(User).where((User.email == identifier) | (User.username == identifier)))
        if user and not user.blocked:
            data = self.issue("reset", user.email, user_id=user.id)
        else:
            self.limit("unknown-mail:" + identifier, 1, 60)
            data = {"challenge_id": str(uuid4()), "expires_in": 600, "resend_after": 60}
        return {key: value for key, value in data.items() if key != "email"} | {
            "message": "Аккаунт бар болса, қалпына келтіру коды поштаға жіберілді."}

    def assert_owner(self, user_id, essay_id):
        with self.db.session() as session:
            if not session.scalar(select(EssayOwner).where(EssayOwner.essay_id == essay_id, EssayOwner.user_id == user_id)):
                raise HTTPException(404, "Эссе табылмады.")


def service(request: Request):
    db = request.app.state.database
    if db is None:
        raise HTTPException(503, "PostgreSQL бапталмаған.")
    return AccountService(db, request.app.state.settings, request.app.state.mailer)


def current_user(request: Request):
    user = service(request).session_user(request.cookies.get(COOKIE))
    if not user:
        raise HTTPException(401, "Жалғастыру үшін аккаунтыңызға кіріңіз.")
    return user


def admin_user(request: Request):
    user = current_user(request)
    if user.role != "admin":
        raise HTTPException(403, "Әкімші құқығы қажет.")
    return user
