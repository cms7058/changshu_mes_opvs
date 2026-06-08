"""Chat endpoint — calls MiniMax (Anthropic-compatible) and persists messages."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select
from ..auth import get_current_user, require_project_access
from ..db import get_session
from ..llm import chat as llm_chat, chat_stream, healthcheck
from ..models import ChatSession, ChatMessage, User, AuditLog, Document
from ..schemas import ChatIn, ChatOut

# Cap each document context to avoid token blow-ups (M2.7 has long context but we still cap)
PER_DOC_CHAR_CAP = 30000
TOTAL_CONTEXT_CAP = 120000

router = APIRouter(prefix="/api/chat", tags=["chat"])

SYSTEM_PROMPT = """你是一位资深 MES/WMS 运维顾问，服务对象是工厂运维工程师。
回答需：① 引用上下文中的项目蓝图与历史方案；② 给出可操作的步骤；
③ 标注需要客户/甲方提供的输入；④ 避免虚构 SAP 移动类型、字段名等技术细节，不确定时明说"需要确认"。
回答风格简洁、结构化，优先用 Markdown 表格与列表。"""


@router.get("/health")
def health(user: User = Depends(get_current_user)):
    """Test MiniMax connectivity. Useful for first-time setup."""
    return healthcheck()


@router.post("", response_model=ChatOut)
def send(body: ChatIn, user: User = Depends(get_current_user),
         session: Session = Depends(get_session)):
    require_project_access(body.project_id, user, session)

    # get or create session
    if body.session_id:
        cs = session.get(ChatSession, body.session_id)
        if not cs or cs.user_id != user.id:
            raise HTTPException(status_code=404, detail="会话不存在")
    else:
        cs = ChatSession(user_id=user.id, project_id=body.project_id,
                         title=body.message[:30])
        session.add(cs); session.commit(); session.refresh(cs)

    # load history
    history = session.exec(
        select(ChatMessage).where(ChatMessage.session_id == cs.id).order_by(ChatMessage.id)
    ).all()
    messages = [{"role": m.role, "content": m.content} for m in history]

    # build context from referenced documents
    context_block = ""
    used_docs = []
    if body.referenced_doc_ids:
        docs = session.exec(
            select(Document).where(
                Document.id.in_(body.referenced_doc_ids),
                Document.project_id == body.project_id,
            )
        ).all()
        total = 0
        chunks = []
        for d in docs:
            if not d.extracted_text:
                continue
            txt = d.extracted_text
            if len(txt) > PER_DOC_CHAR_CAP:
                txt = txt[:PER_DOC_CHAR_CAP] + "\n…[已截断]"
            block = f"\n\n===== 文档：{d.filename} =====\n{txt}"
            if total + len(block) > TOTAL_CONTEXT_CAP:
                block = block[: TOTAL_CONTEXT_CAP - total] + "\n…[整体截断]"
                chunks.append(block)
                total += len(block)
                used_docs.append(d.filename + "（部分）")
                break
            chunks.append(block)
            total += len(block)
            used_docs.append(d.filename)
        if chunks:
            context_block = (
                "以下是用户提供的项目文档原文，请基于这些文档回答问题。"
                "若文档中无明确依据，请说明"\
                "不要凭空编造。"
                + "".join(chunks)
                + "\n\n===== 用户问题 =====\n"
            )

    user_content = context_block + body.message if context_block else body.message
    messages.append({"role": "user", "content": user_content})

    # persist user msg (store original, not the augmented one)
    refs_label = f"  [📎参考: {', '.join(used_docs)}]" if used_docs else ""
    session.add(ChatMessage(session_id=cs.id, role="user", content=body.message + refs_label))
    session.commit()

    if body.stream:
        def gen():
            collected = []
            try:
                for delta in chat_stream(messages, system=SYSTEM_PROMPT):
                    collected.append(delta)
                    yield delta
            finally:
                full = "".join(collected)
                with Session(session.bind) as s2:
                    s2.add(ChatMessage(session_id=cs.id, role="assistant", content=full))
                    s2.add(AuditLog(user_id=user.id, action="chat.stream",
                                    payload=f"session={cs.id}"))
                    s2.commit()
        return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")

    try:
        reply = llm_chat(messages, system=SYSTEM_PROMPT)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"模型调用失败: {e}")
    session.add(ChatMessage(session_id=cs.id, role="assistant", content=reply))
    session.add(AuditLog(user_id=user.id, action="chat", payload=f"session={cs.id}"))
    session.commit()
    return ChatOut(session_id=cs.id, reply=reply)


@router.get("/sessions/{project_id}")
def list_sessions(project_id: int, user: User = Depends(get_current_user),
                  session: Session = Depends(get_session)):
    require_project_access(project_id, user, session)
    rows = session.exec(
        select(ChatSession).where(ChatSession.user_id == user.id,
                                  ChatSession.project_id == project_id)
        .order_by(ChatSession.id.desc())
    ).all()
    return rows


@router.get("/session/{session_id}/messages")
def list_messages(session_id: int, user: User = Depends(get_current_user),
                  session: Session = Depends(get_session)):
    cs = session.get(ChatSession, session_id)
    if not cs or cs.user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session.exec(
        select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id)
    ).all()
