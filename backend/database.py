from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from .models import Base


class Database:
    def __init__(self, url: str):
        if make_url(url).get_backend_name() != "postgresql":
            raise ValueError("PostgreSQL DATABASE_URL қажет")
        self.engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
        self.session = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        Base.metadata.create_all(self.engine)

    def ping(self):
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
