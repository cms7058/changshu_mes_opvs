"""Runtime settings — admin can update via UI without editing .env."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from ..auth import require_role
from ..models import User, AuditLog
from ..db import get_session
from sqlmodel import Session
from .. import runtime_settings as rs

router = APIRouter(prefix="/api/settings", tags=["settings"])

EDITABLE_KEYS = {
    "MINIMAX_API_KEY",
    "MINIMAX_MODEL",
    "MINIMAX_BASE_URL",
    "MINIMAX_MAX_TOKENS",
}


class SettingsOut(BaseModel):
    minimax_model: str
    minimax_base_url: str
    minimax_max_tokens: int
    minimax_api_key_set: bool  # never return the key itself
    minimax_api_key_preview: str


class SettingsIn(BaseModel):
    minimax_api_key: str | None = None       # blank/None = no change
    minimax_model: str | None = None
    minimax_base_url: str | None = None
    minimax_max_tokens: int | None = None


@router.get("", response_model=SettingsOut)
def get_settings(_=Depends(require_role("admin"))):
    key = rs.llm_api_key()
    preview = (key[:8] + "…" + key[-4:]) if key and len(key) > 14 else "（未设置）"
    return SettingsOut(
        minimax_model=rs.llm_model(),
        minimax_base_url=rs.llm_base_url(),
        minimax_max_tokens=rs.llm_max_tokens(),
        minimax_api_key_set=bool(key) and not key.startswith("sk-cp-REPLACE"),
        minimax_api_key_preview=preview,
    )


@router.put("", response_model=SettingsOut)
def update_settings(
    body: SettingsIn,
    user: User = Depends(require_role("admin")),
    session: Session = Depends(get_session),
):
    changed = []
    if body.minimax_api_key:
        rs.set_value("MINIMAX_API_KEY", body.minimax_api_key.strip(), user.id)
        changed.append("api_key")
    if body.minimax_model:
        rs.set_value("MINIMAX_MODEL", body.minimax_model.strip(), user.id)
        changed.append("model")
    if body.minimax_base_url:
        rs.set_value("MINIMAX_BASE_URL", body.minimax_base_url.strip(), user.id)
        changed.append("base_url")
    if body.minimax_max_tokens is not None:
        rs.set_value("MINIMAX_MAX_TOKENS", str(body.minimax_max_tokens), user.id)
        changed.append("max_tokens")
    if changed:
        session.add(AuditLog(user_id=user.id, action="settings.update",
                             payload=",".join(changed)))
        session.commit()
    return get_settings()
