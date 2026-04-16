from sqlmodel import SQLModel, create_engine, Session
from core.config import DB_PATH

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)


def init_db():
    from models.contact import AppConfig  # avoid circular at module level
    SQLModel.metadata.create_all(engine)
    # Seed the singleton config row if it doesn't exist yet
    with Session(engine) as s:
        if not s.get(AppConfig, 1):
            s.add(AppConfig(id=1, serper_queries_used=0))
            s.commit()


def get_session():
    with Session(engine) as session:
        yield session
