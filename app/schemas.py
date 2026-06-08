"""Pydantic request/response schemas."""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


# Auth
class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


# User
class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    email: Optional[str] = None
    full_name: Optional[str] = None
    role: str = Field(default="engineer")


class UserOut(BaseModel):
    id: int
    username: str
    email: Optional[str]
    full_name: Optional[str]
    role: str
    is_active: bool
    created_at: datetime


class UserUpdate(BaseModel):
    email: Optional[str] = None
    full_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None


# Project
class ProjectCreate(BaseModel):
    name: str
    customer: Optional[str] = None
    description: Optional[str] = None


class ProjectOut(BaseModel):
    id: int
    name: str
    customer: Optional[str]
    description: Optional[str]
    created_at: datetime


# Project membership
class GrantAccess(BaseModel):
    user_id: int
    permission: str = "read"  # read / write / admin


# Document
class DocumentOut(BaseModel):
    id: int
    project_id: int
    filename: str
    mime_type: str
    size_bytes: int
    kind: str
    version: str
    uploaded_at: datetime


# Chat
class ChatIn(BaseModel):
    session_id: Optional[int] = None
    project_id: int
    message: str
    stream: bool = False


class ChatOut(BaseModel):
    session_id: int
    reply: str
