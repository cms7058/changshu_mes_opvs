"""Runtime settings that can be overridden via DB (admin-editable in UI).
Fallback to .env values if not present in DB."""
from datetime import datetime
from typing import Optional
from sqlmodel import Session, select
from .db import engine
from .models import AppSetting
from .config import settings as env_settings

# Cache: read-through DB once, refresh on update
_cache: dict[str, str] = {}
_cache_loaded = False


def _load_cache():
    global _cache, _cache_loaded
    _cache = {}
    with Session(engine) as s:
        for row in s.exec(select(AppSetting)).all():
            _cache[row.key] = row.value
    _cache_loaded = True


def get(key: str, default: Optional[str] = None) -> Optional[str]:
    if not _cache_loaded:
        _load_cache()
    return _cache.get(key, default)


def set_value(key: str, value: str, user_id: int) -> None:
    with Session(engine) as s:
        row = s.get(AppSetting, key)
        if row:
            row.value = value
            row.updated_by = user_id
            row.updated_at = datetime.utcnow()
        else:
            row = AppSetting(key=key, value=value, updated_by=user_id)
        s.add(row)
        s.commit()
    _cache[key] = value


# ===== Helpers for LLM (DB overrides env) =====
def llm_api_key() -> str:
    return get("MINIMAX_API_KEY") or env_settings.MINIMAX_API_KEY


def llm_model() -> str:
    return get("MINIMAX_MODEL") or env_settings.MINIMAX_MODEL


def llm_base_url() -> str:
    return get("MINIMAX_BASE_URL") or env_settings.MINIMAX_BASE_URL


def llm_max_tokens() -> int:
    v = get("MINIMAX_MAX_TOKENS")
    return int(v) if v else env_settings.MINIMAX_MAX_TOKENS
