"""SQLModel data models — single source of truth for schema + Pydantic."""
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field, Relationship


# ============== Users ==============
class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True, max_length=64)
    password_hash: str
    email: Optional[str] = Field(default=None, max_length=128)
    full_name: Optional[str] = Field(default=None, max_length=64)
    role: str = Field(default="engineer")  # admin / engineer / viewer
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============== Projects ==============
class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, max_length=128)
    customer: Optional[str] = Field(default=None, max_length=128)
    description: Optional[str] = None
    created_by: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class UserProject(SQLModel, table=True):
    """N:N user ↔ project with per-project permission."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    permission: str = Field(default="read")  # read / write / admin


# ============== Documents ==============
class Document(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    filename: str
    mime_type: str
    size_bytes: int
    storage_path: str  # local fs path relative to UPLOAD_DIR
    version: str = Field(default="v1")
    kind: str = Field(default="orig")  # orig / rev / new (legacy support)
    uploaded_by: int = Field(foreign_key="user.id")
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)


# ============== Issues / Solutions (for knowledge base seed) ==============
class Issue(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    code: str = Field(index=True, max_length=16)  # A1, B2, ...
    category: str = Field(max_length=32)
    severity: str = Field(default="mid")  # high / mid / low
    title: str
    description: str
    proposed_solution: Optional[str] = None
    required_inputs: Optional[str] = None  # what customer must provide
    status: str = Field(default="open")  # open / reviewing / approved / closed
    source_doc_id: Optional[int] = Field(default=None, foreign_key="document.id")
    owner_id: Optional[int] = Field(default=None, foreign_key="user.id")
    created_by: int = Field(foreign_key="user.id")
    approved_by: Optional[int] = Field(default=None, foreign_key="user.id")
    approved_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ============== Chat sessions ==============
class ChatSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    title: str = Field(default="新对话", max_length=128)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChatMessage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="chatsession.id", index=True)
    role: str  # user / assistant / system
    content: str
    tokens: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============== Runtime settings (overrides .env) ==============
class AppSetting(SQLModel, table=True):
    key: str = Field(primary_key=True, max_length=64)
    value: str
    updated_by: Optional[int] = Field(default=None, foreign_key="user.id")
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ============== Audit log ==============
class AuditLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    action: str = Field(index=True, max_length=64)
    payload: Optional[str] = None
    ip: Optional[str] = Field(default=None, max_length=64)
    ts: datetime = Field(default_factory=datetime.utcnow)
