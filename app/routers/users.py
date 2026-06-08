"""User management — admin-only operations."""
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from ..auth import require_role, hash_password
from ..db import get_session
from ..models import User, AuditLog
from ..schemas import UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("", response_model=List[UserOut])
def list_users(_=Depends(require_role("admin")), session: Session = Depends(get_session)):
    return session.exec(select(User).order_by(User.id)).all()


@router.post("", response_model=UserOut)
def create_user(
    data: UserCreate,
    actor: User = Depends(require_role("admin")),
    session: Session = Depends(get_session),
):
    if session.exec(select(User).where(User.username == data.username)).first():
        raise HTTPException(status_code=400, detail="用户名已存在")
    if data.role not in {"admin", "engineer", "viewer"}:
        raise HTTPException(status_code=400, detail="role 仅支持 admin/engineer/viewer")
    user = User(
        username=data.username,
        password_hash=hash_password(data.password),
        email=data.email,
        full_name=data.full_name,
        role=data.role,
    )
    session.add(user)
    session.add(AuditLog(user_id=actor.id, action="user.create", payload=data.username))
    session.commit()
    session.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    data: UserUpdate,
    actor: User = Depends(require_role("admin")),
    session: Session = Depends(get_session),
):
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if data.email is not None: user.email = data.email
    if data.full_name is not None: user.full_name = data.full_name
    if data.role is not None:
        if data.role not in {"admin", "engineer", "viewer"}:
            raise HTTPException(status_code=400, detail="非法角色")
        user.role = data.role
    if data.is_active is not None: user.is_active = data.is_active
    if data.password: user.password_hash = hash_password(data.password)
    session.add(user)
    session.add(AuditLog(user_id=actor.id, action="user.update", payload=f"id={user_id}"))
    session.commit()
    session.refresh(user)
    return user


@router.delete("/{user_id}")
def delete_user(
    user_id: int,
    actor: User = Depends(require_role("admin")),
    session: Session = Depends(get_session),
):
    if user_id == actor.id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    session.delete(user)
    session.add(AuditLog(user_id=actor.id, action="user.delete", payload=f"id={user_id}"))
    session.commit()
    return {"ok": True}
