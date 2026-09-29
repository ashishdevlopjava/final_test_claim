from collections.abc import Generator

from sqlalchemy.engine import make_url
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings


def build_engine(database_url: str | None = None):
    url = make_url(database_url or get_settings().database_url)
    connect_args = {"check_same_thread": False} if url.drivername.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True)


engine = build_engine()


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
