"""MiniMax LLM client (via Anthropic-compatible endpoint)."""
from typing import Iterable, List, Dict, Any
from anthropic import Anthropic
from .config import settings


_client: Anthropic | None = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        if not settings.MINIMAX_API_KEY or settings.MINIMAX_API_KEY.startswith("sk-cp-REPLACE"):
            raise RuntimeError("MINIMAX_API_KEY 未配置，请编辑 .env 后重启服务")
        _client = Anthropic(
            api_key=settings.MINIMAX_API_KEY,
            base_url=settings.MINIMAX_BASE_URL,
        )
    return _client


def chat(messages: List[Dict[str, Any]], system: str | None = None,
         max_tokens: int | None = None, model: str | None = None) -> str:
    """Single-turn synchronous chat. Returns assistant text."""
    client = get_client()
    kwargs = {
        "model": model or settings.MINIMAX_MODEL,
        "max_tokens": max_tokens or settings.MINIMAX_MAX_TOKENS,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    resp = client.messages.create(**kwargs)
    # Concatenate text blocks
    parts = []
    for block in resp.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "".join(parts)


def chat_stream(messages: List[Dict[str, Any]], system: str | None = None,
                max_tokens: int | None = None) -> Iterable[str]:
    """Server-Sent-Events streaming generator yielding text deltas."""
    client = get_client()
    kwargs = {
        "model": settings.MINIMAX_MODEL,
        "max_tokens": max_tokens or settings.MINIMAX_MAX_TOKENS,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    with client.messages.stream(**kwargs) as stream:
        for delta in stream.text_stream:
            yield delta


def healthcheck() -> dict:
    """Lightweight ping — sends a 1-token request."""
    try:
        txt = chat([{"role": "user", "content": "ping"}], max_tokens=8)
        return {"ok": True, "model": settings.MINIMAX_MODEL, "sample": txt[:50]}
    except Exception as e:
        return {"ok": False, "error": str(e)}
