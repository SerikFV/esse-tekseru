from sqlalchemy import select, func

from backend.accounts import hash_secret
from backend.auth_models import User, EssayOwner
from backend.config import Settings
from backend.mailer import SMTPMailer
from backend.models import Essay
from scripts.claim_legacy import claim
from test_accounts import Mailbox
from test_api import database  # noqa: F401


def test_smtp_adapter_uses_tls_before_login_and_sends_code(monkeypatch):
    calls = []

    class Transport:
        def __init__(self, host, port, **kwargs):
            assert host == "smtp.gmail.com" and port == 587
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def starttls(self, context):
            calls.append("tls")
        def login(self, username, password):
            assert calls == ["tls"]
            assert username == "sender@example.com" and password == "test-app-password"
            calls.append("login")
        def send_message(self, message):
            assert calls == ["tls", "login"]
            assert message["To"] == "recipient@example.com"
            assert "123456" in message.get_content()
            calls.append("send")

    monkeypatch.setattr("backend.mailer.smtplib.SMTP", Transport)
    settings = Settings(_env_file=None, smtp_username="sender@example.com", smtp_password="test-app-password", smtp_from_email="sender@example.com")
    SMTPMailer(settings).send_code("recipient@example.com", "123456", "register")
    assert calls == ["tls", "login", "send"]


def test_admin_setup_requires_email_proof_and_does_not_store_plain_password(database, monkeypatch, capsys):
    from scripts import setup_admin
    database.initialize()
    mailbox = Mailbox()
    settings = Settings(_env_file=None)
    monkeypatch.setattr(setup_admin, "Settings", lambda: settings)
    monkeypatch.setattr(setup_admin, "Database", lambda _: database)
    monkeypatch.setattr(setup_admin, "SMTPMailer", lambda _: mailbox)
    answers = iter(["admin", "admin@example.com"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    def secret(prompt):
        if "коды" in prompt:
            with database.session() as session:
                assert session.scalar(select(func.count()).select_from(User)) == 0
            return mailbox.sent[-1]["code"]
        return "abcd"
    monkeypatch.setattr(setup_admin, "getpass", secret)
    setup_admin.main()
    with database.session() as session:
        admin = session.scalar(select(User))
        assert admin.role == "admin" and admin.email == "admin@example.com"
        assert admin.password_hash != "abcd"
    output = capsys.readouterr().out
    assert "abcd" not in output and mailbox.sent[-1]["code"] not in output


def test_legacy_claim_is_explicit_preserves_other_users_and_is_idempotent(database):
    database.initialize()
    with database.session.begin() as session:
        admin = User(username="admin", email="admin@example.com", password_hash=hash_secret("abcd"), role="admin")
        other = User(username="other", email="other@example.com", password_hash=hash_secret("abcd"))
        session.add_all([admin, other]); session.flush()
        old = Essay(title="Бұрынғы жұмыс", essay_text="Сақталатын мәтін.", word_count=2)
        owned = Essay(title="Жеке жұмыс", essay_text="Басқа адамның мәтіні.", word_count=3)
        session.add_all([old, owned]); session.flush()
        session.add(EssayOwner(essay_id=owned.id, user_id=other.id))
        old_id, owned_id, admin_id, other_id = old.id, owned.id, admin.id, other.id
    assert claim(database, "admin") == 1
    with database.session() as session:
        assert session.get(EssayOwner, old_id) is None
    assert claim(database, "admin", True) == 1
    assert claim(database, "admin", True) == 0
    with database.session() as session:
        assert session.get(EssayOwner, old_id).user_id == admin_id
        assert session.get(EssayOwner, owned_id).user_id == other_id
        assert session.get(Essay, old_id).essay_text == "Сақталатын мәтін."
