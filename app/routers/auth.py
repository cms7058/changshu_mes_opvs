"""Login + current user info."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session, select
from ..auth import verify_password, create_token, get_current_user
from ..db import get_session
from ..models import User, AuditLog
from ..schemas import TokenOut, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends(), session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.username == form.username)).first()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账户已禁用")
    token = create_token(user.id, user.username, user.role)
    session.add(AuditLog(user_id=user.id, action="login"))
    session.commit()
    return TokenOut(
        access_token=token,
        user={"id": user.id, "username": user.username, "role": user.role, "full_name": user.full_name},
    )


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
