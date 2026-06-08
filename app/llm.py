"""MiniMax LLM client (via Anthropic-compatible endpoint).
Reads config from runtime_settings (DB-overridable), falls back to .env."""
from typing import Iterable, List, Dict, Any
from anthropic import Anthropic
from . import runtime_settings as rs


def get_client() -> Anthropic:
    """Always builds a fresh client based on current DB/env settings.
    Cheap to construct; avoids stale state when admin updates key in UI."""
    key = rs.llm_api_key()
    if not key or key.startswith("sk-cp-REPLACE"):
        raise RuntimeError("MINIMAX_API_KEY 未配置，请在【系统状态】或 .env 中设置")
    return Anthropic(api_key=key, base_url=rs.llm_base_url())


def chat(messages: List[Dict[str, Any]], system: str | None = None,
         max_tokens: int | None = None, model: str | None = None) -> str:
    """Single-turn synchronous chat. Returns assistant text."""
    client = get_client()
    kwargs = {
        "model": model or rs.llm_model(),
        "max_tokens": max_tokens or rs.llm_max_tokens(),
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    resp = client.messages.create(**kwargs)
    parts = []
    for block in resp.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "".join(parts)


def chat_stream(messages: List[Dict[str, Any]], system: str | None = None,
                max_tokens: int | None = None) -> Iterable[str]:
    client = get_client()
    kwargs = {
        "model": rs.llm_model(),
        "max_tokens": max_tokens or rs.llm_max_tokens(),
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    with client.messages.stream(**kwargs) as stream:
        for delta in stream.text_stream:
            yield delta


def healthcheck() -> dict:
    try:
        txt = chat([{"role": "user", "content": "ping"}], max_tokens=8)
        return {"ok": True, "model": rs.llm_model(), "base_url": rs.llm_base_url(),
                "sample": txt[:50]}
    except Exception as e:
        return {"ok": False, "model": rs.llm_model(), "base_url": rs.llm_base_url(),
                "error": str(e)}
