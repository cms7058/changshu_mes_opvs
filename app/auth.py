"""Authentication: bcrypt + JWT."""
from datetime import datetime, timedelta
from typing import Optional
from passlib.context import CryptContext
from jose import jwt, JWTError
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session
from .config import settings
from .db import get_session
from .models import User, UserProject

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def hash_password(plain: str) -> str:
    return pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd.verify(plain, hashed)


def create_token(user_id: int, username: str, role: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=settings.JWT_EXPIRE_HOURS)
    payload = {"sub": str(user_id), "username": username, "role": role, "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"凭证无效: {e}")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> User:
    payload = decode_token(token)
    user_id = int(payload.get("sub", "0"))
    user = session.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="用户已禁用或不存在")
    return user


def require_role(*roles: str):
    """Dependency factory: require user to have one of given roles."""
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail=f"需要角色: {roles}")
        return user
    return checker


def require_project_access(
    project_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> User:
    """Ensure user can access the given project (admin sees all)."""
    if user.role == "admin":
        return user
    membership = session.query(UserProject).filter(
        UserProject.user_id == user.id,
        UserProject.project_id == project_id,
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="无此项目访问权限")
    return user
