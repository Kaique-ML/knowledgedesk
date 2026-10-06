from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from knowledgedesk.settings import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionFactory = sessionmaker(engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()
