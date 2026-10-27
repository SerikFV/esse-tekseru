"""Verified test identities use real database sessions, never an app auth bypass."""
from contextlib import contextmanager
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend.accounts import AccountService, COOKIE, hash_secret
from backend.auth_models import User


@contextmanager
def client_for(app):
    with TestClient(app, headers={"X-Requested-With": "tekSER"}) as client:
        db = app.state.database
        if db is not None and hasattr(db, "session"):
            with db.session.begin() as session:
                user = session.scalar(select(User).where(User.username == "workflow_user"))
                if not user:
                    user = User(username="workflow_user", email="workflow@example.com", password_hash=hash_secret("test"))
                    session.add(user)
                    session.flush()
            svc = AccountService(db, app.state.settings, app.state.mailer)
            client.cookies.set(COOKIE, svc.new_session(user.id))
            client.test_user_id = user.id
        yield client
