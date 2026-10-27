"""Explicit local migration of formerly shared essays; dry run unless --apply."""
import argparse

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from backend.accounts import lock
from backend.auth_models import User, EssayOwner, Audit
from backend.config import Settings
from backend.database import Database
from backend.models import Essay


def claim(db, username, apply=False):
    with db.session.begin() as session:
        lock(session, "claim-legacy")
        user = session.scalar(select(User).where(User.username == username.casefold(), User.role == "admin"))
        if not user:
            raise ValueError("Расталған әкімші логині табылмады.")
        ids = session.scalars(select(Essay.id).outerjoin(EssayOwner).where(EssayOwner.essay_id.is_(None))).all()
        if apply:
            for essay_id in ids:
                session.add(EssayOwner(essay_id=essay_id, user_id=user.id))
            session.add(Audit(actor_id=user.id, target_id=user.id, action="legacy_essays_claimed"))
        return len(ids)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db = None
    try:
        db = Database(Settings().database_url.get_secret_value())
        db.initialize()
        count = claim(db, args.username, args.apply)
        print(f"{'Бекітілді' if args.apply else 'Бекітуге дайын'}: {count} бұрынғы эссе.")
        if not args.apply:
            print("Өзгерістер енгізілген жоқ. Бекіту үшін осы пәрменге --apply қосыңыз.")
    except ValueError as exc:
        print(str(exc))
    except SQLAlchemyError:
        print("Деректер базасына қосылу мүмкін болмады.")
    finally:
        if db:
            db.engine.dispose()


if __name__ == "__main__":
    main()
