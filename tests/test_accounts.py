"""Real PostgreSQL identity/security tests. Email is captured only by this test adapter."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func, update

from backend.accounts import RegisterInput, hash_secret, check_secret, COOKIE
from backend.auth_models import User, Challenge, AuthLimit, LoginSession, EssayOwner, now
from backend.config import Settings
from backend.main import create_app
from test_api import database  # noqa: F401
from test_analysis import ESSAY, analysis_json  # noqa: F401
from test_workflow import Service


class Mailbox:
    configured = True

    def __init__(self):
        self.sent = []
        self.fail = False

    def send_code(self, email, code, purpose):
        if self.fail:
            raise RuntimeError("provider private credential must never appear")
        self.sent.append({"email": email, "code": code, "purpose": purpose})


@pytest.fixture
def account_client(database):
    mail = Mailbox()
    app = create_app(Settings(_env_file=None, bootstrap_admin_email="owner@example.com"), database=database, mailer=mail)
    with TestClient(app, headers={"X-Requested-With": "tekSER"}) as client:
        yield client, mail, database


def register(client, username="reader", email="reader@example.com", password="abcd"):
    return client.post("/api/auth/register", json={"username": username, "email": email,
                       "password": password, "password_confirmation": password})


def verified(client, mail, username="reader", email="reader@example.com", password="abcd"):
    response = register(client, username, email, password)
    assert response.status_code == 200, response.text
    data = response.json()
    response = client.post("/api/auth/verify", json={"challenge_id": data["challenge_id"], "code": mail.sent[-1]["code"]})
    assert response.status_code == 200, response.text
    return response.json()["user"]


def allow_mail(db):
    with db.session.begin() as session:
        session.execute(update(AuthLimit).values(reset_at=now() - timedelta(seconds=1)))


@pytest.mark.parametrize("password", ["abcd", "1234", "    ", "әғқң", "a" * 1000])
def test_only_password_policy_is_four_characters(password):
    assert RegisterInput(username="reader", email="reader@example.com", password=password, password_confirmation=password)
    assert check_secret(password, hash_secret(password))


@pytest.mark.parametrize("password", ["", "a", "abc"])
def test_short_passwords_rejected(password):
    with pytest.raises(ValueError):
        RegisterInput(username="reader", email="reader@example.com", password=password, password_confirmation=password)


def test_no_user_until_verified_and_single_use_code(account_client):
    client, mail, db = account_client
    response = register(client)
    assert response.status_code == 200
    assert "code" not in response.json()
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0
        pending = session.get(Challenge, response.json()["challenge_id"])
        assert pending.code_hash != mail.sent[-1]["code"]
        assert pending.password_hash != "abcd"
    assert client.post("/api/auth/login", json={"identifier": "reader", "password": "abcd"}).status_code == 401
    body = {"challenge_id": response.json()["challenge_id"], "code": mail.sent[-1]["code"]}
    result = client.post("/api/auth/verify", json=body)
    assert result.status_code == 200
    assert "HttpOnly" in result.headers["set-cookie"]
    assert "password" not in str(result.json())
    assert client.get("/api/auth/me").json()["user"]["username"] == "reader"
    assert client.post("/api/auth/verify", json=body).status_code == 400
    client.post("/api/auth/logout")
    assert client.get("/api/essays").status_code == 401
    assert client.post("/api/auth/login", json={"identifier": "READER@EXAMPLE.COM", "password": "abcd"}).status_code == 200


def test_wrong_codes_committed_and_expiry_enforced(account_client):
    client, mail, db = account_client
    response = register(client).json()
    wrong = "000000" if mail.sent[-1]["code"] != "000000" else "111111"
    for _ in range(5):
        assert client.post("/api/auth/verify", json={"challenge_id": response["challenge_id"], "code": wrong}).status_code == 400
    assert client.post("/api/auth/verify", json={"challenge_id": response["challenge_id"], "code": mail.sent[-1]["code"]}).status_code == 400
    with db.session() as session:
        assert session.get(Challenge, response["challenge_id"]).attempts == 5
        assert session.scalar(select(func.count()).select_from(User)) == 0
    allow_mail(db)
    newer = client.post("/api/auth/resend", json={"challenge_id": response["challenge_id"]}).json()
    with db.session.begin() as session:
        session.get(Challenge, newer["challenge_id"]).expires_at = now() - timedelta(seconds=1)
    assert client.post("/api/auth/verify", json={"challenge_id": newer["challenge_id"], "code": mail.sent[-1]["code"]}).status_code == 400


def test_resend_cooldown_invalidates_previous_code(account_client):
    client, mail, db = account_client
    first = register(client).json()
    old_code = mail.sent[-1]["code"]
    assert client.post("/api/auth/resend", json={"challenge_id": first["challenge_id"]}).status_code == 429
    allow_mail(db)
    second = client.post("/api/auth/resend", json={"challenge_id": first["challenge_id"]}).json()
    assert second["challenge_id"] != first["challenge_id"]
    assert client.post("/api/auth/verify", json={"challenge_id": first["challenge_id"], "code": old_code}).status_code == 400
    assert client.post("/api/auth/verify", json={"challenge_id": second["challenge_id"], "code": mail.sent[-1]["code"]}).status_code == 200


def test_duplicate_identifiers_and_validation_dont_echo_secrets(account_client):
    client, mail, db = account_client
    verified(client, mail)
    assert register(client, "READER", "other@example.com").status_code == 409
    assert register(client, "other", "READER@EXAMPLE.COM").status_code == 409
    assert register(client, "bad space", "new@example.com").status_code == 422
    assert register(client, "new", "bad-email").status_code == 422
    secret = "PRIVATE_PASSWORD_SENTINEL"
    response = client.post("/api/auth/register", json={"username": "new", "email": "bad-email", "password": secret, "password_confirmation": secret})
    assert secret not in response.text
    assert client.post("/api/auth/validate", json={"username": "READER"}).json()["username"]["valid"] is False


def test_smtp_failure_does_not_create_account_or_challenge(account_client):
    client, mail, db = account_client
    mail.fail = True
    response = register(client)
    assert response.status_code == 503
    assert "credential" not in response.text
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0
        assert session.scalar(select(func.count()).select_from(Challenge)) == 0


def test_two_verifications_only_create_one_user(account_client):
    client, mail, db = account_client
    challenge = register(client).json()
    body = {"challenge_id": challenge["challenge_id"], "code": mail.sent[-1]["code"]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: client.post("/api/auth/verify", json=body).status_code, range(2)))
    assert sorted(statuses) == [200, 400]
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1


def test_final_verification_rechecks_username_uniqueness(account_client):
    client, mail, db = account_client
    first = register(client, "shared", "first@example.com").json(); code1 = mail.sent[-1]["code"]
    second = register(client, "shared", "second@example.com").json(); code2 = mail.sent[-1]["code"]
    assert client.post("/api/auth/verify", json={"challenge_id": first["challenge_id"], "code": code1}).status_code == 200
    assert client.post("/api/auth/verify", json={"challenge_id": second["challenge_id"], "code": code2}).status_code == 409


def test_reset_password_revokes_existing_sessions(account_client):
    client, mail, db = account_client
    verified(client, mail)
    old_token = client.cookies.get(COOKIE)
    allow_mail(db)
    challenge = client.post("/api/auth/forgot", json={"identifier": "reader@example.com"}).json()
    response = client.post("/api/auth/verify", json={"challenge_id": challenge["challenge_id"], "code": mail.sent[-1]["code"], "new_password": "zzzz", "password_confirmation": "zzzz"})
    assert response.status_code == 200
    client.cookies.set(COOKIE, old_token)
    assert client.get("/api/auth/me").json()["user"] is None
    assert client.post("/api/auth/login", json={"identifier": "reader", "password": "abcd"}).status_code == 401
    assert client.post("/api/auth/login", json={"identifier": "reader", "password": "zzzz"}).status_code == 200


def test_profile_email_changes_only_after_verification(account_client):
    client, mail, db = account_client
    verified(client, mail)
    response = client.post("/api/account/email", json={"email": "new@example.com", "password": "abcd"})
    assert response.status_code == 200
    assert client.get("/api/auth/me").json()["user"]["email"] == "reader@example.com"
    assert client.post("/api/auth/verify", json={"challenge_id": response.json()["challenge_id"], "code": mail.sent[-1]["code"]}).status_code == 200
    assert client.get("/api/auth/me").json()["user"]["email"] == "new@example.com"


def test_csrf_and_admin_access_blocking(account_client):
    client, mail, db = account_client
    assert client.get("/profile", follow_redirects=False).status_code == 303
    assert client.get("/admin", follow_redirects=False).status_code == 303
    normal = verified(client, mail)
    assert normal["role"] == "user"
    assert client.get("/api/admin/overview").status_code == 403
    assert client.get("/admin").status_code == 403
    assert client.get("/profile").status_code == 200
    assert client.post("/api/auth/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/auth/logout", headers={"X-Requested-With": ""}).status_code == 403
    admin = verified(client, mail, "owner", "owner@example.com")
    assert admin["role"] == "admin"
    assert client.get("/admin").status_code == 200
    assert client.get("/api/admin/overview").status_code == 200
    for endpoint in ("users", "pending", "services", "audit"):
        response = client.get("/api/admin/" + endpoint)
        assert response.status_code == 200
        assert "password_hash" not in response.text and "code_hash" not in response.text
    assert client.patch(f"/api/admin/users/{normal['id']}", json={"blocked": True}).status_code == 200
    assert client.post("/api/auth/login", json={"identifier": "reader", "password": "abcd"}).status_code == 403
    assert client.patch(f"/api/admin/users/{normal['id']}", json={"blocked": False}).status_code == 200
    assert client.patch(f"/api/admin/users/{admin['id']}", json={"blocked": True}).status_code == 409


def test_owned_essays_are_isolated_even_for_admin(database, analysis_json):
    mail, analyzer = Mailbox(), Service(analysis_json)
    app = create_app(Settings(_env_file=None, bootstrap_admin_email="owner@example.com"), analyzer, database, mail)
    with TestClient(app, headers={"X-Requested-With": "tekSER"}) as client:
        owner = verified(client, mail)
        first = client.post("/api/essays/analyze", json={"title": "Білім", "essay_text": ESSAY}).json()
        assert first["id"]
        verified(client, mail, "owner", "owner@example.com")
        essay_id = first["id"]
        assert client.get("/api/essays").json() == []
        for suffix in ("", "/versions", "/comparison"):
            assert client.get(f"/api/essays/{essay_id}" + suffix).status_code == 404
        assert client.post(f"/api/essays/{essay_id}/retry/grade").status_code == 404
        assert client.delete(f"/api/essays/{essay_id}").status_code == 404
        assert client.post("/api/essays/analyze", json={"title": "Білім", "essay_text": ESSAY, "parent_id": essay_id}).status_code == 404
        assert analyzer.grade_calls == 1


def test_delete_account_removes_essays_and_sessions(database, analysis_json):
    mail = Mailbox()
    app = create_app(Settings(_env_file=None), Service(analysis_json), database, mail)
    with TestClient(app, headers={"X-Requested-With": "tekSER"}) as client:
        user = verified(client, mail)
        first = client.post("/api/essays/analyze", json={"title": "Білім", "essay_text": ESSAY}).json()
        client.post("/api/essays/analyze", json={"title": "Білім", "essay_text": ESSAY, "parent_id": first["id"]})
        assert client.request("DELETE", "/api/account", json={"username": "reader", "password": "abcd"}).status_code == 200
        assert client.get("/api/auth/me").json()["user"] is None
        with database.session() as session:
            assert session.get(User, user["id"]) is None
            assert session.scalar(select(func.count()).select_from(EssayOwner)) == 0
