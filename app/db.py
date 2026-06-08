"""Database engine + session helpers."""
import os
from sqlmodel import SQLModel, create_engine, Session
from .config import settings


# Ensure data dir exists
os.makedirs(os.path.dirname(settings.DB_PATH) or ".", exist_ok=True)

# SQLite + WAL for better concurrency
DATABASE_URL = f"sqlite:///{settings.DB_PATH}"
engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    """Create tables and seed admin user if missing."""
    from . import models  # noqa: F401 — register tables
    SQLModel.metadata.create_all(engine)

    # Seed admin
    from .auth import hash_password
    from .models import User
    with Session(engine) as s:
        existing = s.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        if not existing:
            admin = User(
                username=settings.ADMIN_USERNAME,
                password_hash=hash_password(settings.ADMIN_PASSWORD),
                email=settings.ADMIN_EMAIL,
                full_name="超级管理员",
                role="admin",
            )
            s.add(admin)
            s.commit()
            print(f"[init_db] Created admin user: {settings.ADMIN_USERNAME}")


def get_session():
    with Session(engine) as session:
        yield session
