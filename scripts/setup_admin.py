"""Create/promote the first administrator only after a real mailbox challenge.

Credentials are read interactively, never written to source, config or logs.
"""
from getpass import getpass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from backend.accounts import AccountService, RegisterInput, LoginInput, VerifyInput, email_address, lock
from backend.auth_models import User, Audit
from backend.config import Settings
from backend.database import Database
from backend.mailer import SMTPMailer


def main():
    settings = Settings()
    db = None
    try:
        db = Database(settings.database_url.get_secret_value())
        db.initialize()
        with db.session() as session:
            if session.scalar(select(User.id).where(User.role == "admin")):
                print("Әкімші бұрыннан бар. Құпиясөзді ұмытсаңыз, сайттағы қалпына келтіруді қолданыңыз.")
                return
        username = input("Әкімші логині [admin]: ").strip() or "admin"
        email = email_address(input("Әкімші email мекенжайы: "))
        password = getpass("tekSER құпиясөзі (кемінде 4 таңба): ")
        confirmation = getpass("Құпиясөзді қайталау: ")
        body = RegisterInput(username=username, email=email, password=password, password_confirmation=confirmation)
        svc = AccountService(db, settings, SMTPMailer(settings))
        with db.session() as session:
            existing = session.scalar(select(User).where(User.email == email))
        if existing:
            actor = svc.login(LoginInput(identifier=email, password=password))
            pending = svc.issue("email", email, user_id=actor.id)
        else:
            actor = None
            pending = svc.register(body)
        print("Растау коды поштаға жіберілді. Код расталғанша әкімші құрылмайды.")
        user = None
        for _ in range(5):
            try:
                user, _ = svc.verify(VerifyInput(challenge_id=pending["challenge_id"], code=getpass("Email растау коды: ")), actor)
                break
            except (HTTPException, ValueError):
                print("Код жарамсыз. Мерзімін және енгізілген сандарды тексеріңіз.")
        if user is None:
            print("Растау аяқталмады. Кейінірек қайта бастаңыз.")
            return
        with db.session.begin() as session:
            lock(session, "bootstrap-admin")
            if session.scalar(select(User.id).where(User.role == "admin")):
                print("Әкімші басқа баптау әрекетінде құрылды. Жаңа құқық берілмеді.")
                return
            session.get(User, user.id).role = "admin"
            session.add(Audit(actor_id=user.id, target_id=user.id, action="admin_initialized"))
        print("Пошта расталды. Әкімші дайын. Сайтқа email және tekSER құпиясөзімен кіріңіз.")
    except HTTPException as exc:
        print(exc.detail.get("message", "Баптау аяқталмады.") if isinstance(exc.detail, dict) else exc.detail)
    except ValueError:
        print("Деректер жарамсыз. Логинді, email мен құпиясөздердің сәйкестігін тексеріңіз.")
    except SQLAlchemyError:
        print("Деректер базасына қосылу мүмкін болмады. DATABASE_URL баптауын тексеріңіз.")
    finally:
        if db:
            db.engine.dispose()


if __name__ == "__main__":
    main()
