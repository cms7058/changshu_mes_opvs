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


def _migrate_sqlite():
    """Idempotent column additions for SQLite (we don't use alembic to keep simple)."""
    from sqlalchemy import text
    migrations = {
        "document": [
            ("parse_status", "TEXT DEFAULT 'pending'"),
            ("parse_error", "TEXT"),
            ("extracted_text", "TEXT"),
            ("extracted_html", "TEXT"),
            ("asset_dir", "TEXT"),
            ("parsed_at", "TIMESTAMP"),
        ],
    }
    with engine.connect() as conn:
        for table, cols in migrations.items():
            # Get existing columns
            try:
                existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            except Exception:
                existing = set()
            for col_name, col_def in cols:
                if col_name not in existing:
                    try:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))
                        print(f"[migrate] added {table}.{col_name}")
                    except Exception as e:
                        print(f"[migrate] skip {table}.{col_name}: {e}")
        conn.commit()


def init_db() -> None:
    """Create tables and seed admin user if missing."""
    from . import models  # noqa: F401 — register tables
    SQLModel.metadata.create_all(engine)
    _migrate_sqlite()

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
