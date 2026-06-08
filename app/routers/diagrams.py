"""Diagram editing — AI-powered Mermaid modification with project context."""
import re
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select
from ..auth import get_current_user, require_project_access
from ..db import get_session
from ..models import Issue, Document, User, AuditLog
from .. import llm

router = APIRouter(prefix="/api/diagrams", tags=["diagrams"])


class EditRequest(BaseModel):
    project_id: int
    mermaid_code: str
    target_node: Optional[str] = None     # the clicked node label, if any
    instruction: str                       # what user wants changed


class EditResponse(BaseModel):
    new_mermaid: str
    reasoning: str
    raw: str  # raw LLM output for debugging


EDIT_SYSTEM_PROMPT = """你是 Mermaid 图编辑助手，专精业务流程图、问题分布图、泳道图。

**任务**：根据用户指令修改现有 Mermaid 代码。

**输出格式（必须严格遵守）**：
```mermaid
<完整的修改后代码，不省略任何节点>
```

**修改说明**：
- 改动 1：xxx
- 改动 2：xxx
- 合理性判断：xxx（如果用户要求不合理或与文档矛盾，请指出）

**约束**：
1. 保留原图的整体结构与风格，只做用户请求的局部调整
2. 节点 ID 用英文/数字（如 A1、B2），节点标签用中文
3. 给问题节点用 `:::high / :::mid / :::low / :::problem` 类标记
4. 必须输出**完整**代码，不能用 "..." 省略
5. 如果用户要求增加节点但没说位置，根据上下文合理选择位置并说明
6. 如果用户要求与项目已知问题/文档矛盾，要明确指出
"""


def _extract_mermaid_and_explanation(text: str) -> tuple[str, str]:
    """Return (mermaid_code, explanation)."""
    m = re.search(r'```mermaid\s*\n([\s\S]*?)```', text)
    if not m:
        # Fallback: maybe AI wrote code without fence
        return text.strip(), ""
    code = m.group(1).strip()
    # Explanation = everything after the fence
    end_pos = m.end()
    explanation = text[end_pos:].strip()
    # Strip leading markdown like "**修改说明**：" etc.
    explanation = re.sub(r'^[#*\s]*修改说明[:：]?', '', explanation, flags=re.MULTILINE).strip()
    return code, explanation


def _project_context_brief(session: Session, project_id: int, limit: int = 12) -> str:
    """Brief summary of project issues to give AI context for sanity checks."""
    issues = session.exec(
        select(Issue).where(Issue.project_id == project_id)
        .order_by(Issue.code).limit(limit)
    ).all()
    if not issues:
        return "（项目尚未抽取过问题）"
    lines = []
    for i in issues:
        lines.append(f"- {i.code} [{i.severity}] {i.title}")
    return "\n".join(lines)


@router.post("/edit", response_model=EditResponse)
def edit_diagram(body: EditRequest,
                 user: User = Depends(get_current_user),
                 session: Session = Depends(get_session)):
    require_project_access(body.project_id, user, session)

    ctx = _project_context_brief(session, body.project_id)

    user_msg = f"""**当前 Mermaid 代码**：
```mermaid
{body.mermaid_code}
```
"""
    if body.target_node:
        user_msg += f"\n**用户选中的节点**：`{body.target_node}`\n"
    user_msg += f"\n**用户修改要求**：\n{body.instruction}\n"
    user_msg += f"\n**项目已知问题（供合理性判断参考）**：\n{ctx}\n"
    user_msg += "\n请输出修改后的完整 Mermaid 代码 + 修改说明。"

    try:
        raw = llm.chat(
            [{"role": "user", "content": user_msg}],
            system=EDIT_SYSTEM_PROMPT,
            max_tokens=4000,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM 调用失败: {e}")

    code, explanation = _extract_mermaid_and_explanation(raw)

    session.add(AuditLog(user_id=user.id, action="diagram.edit",
                         payload=f"prj={body.project_id} instr={body.instruction[:80]}"))
    session.commit()

    return EditResponse(new_mermaid=code, reasoning=explanation, raw=raw)


class SummarizeRequest(BaseModel):
    project_id: int
    mermaid_code: str


@router.post("/summarize")
def summarize_diagram(body: SummarizeRequest,
                      user: User = Depends(get_current_user),
                      session: Session = Depends(get_session)):
    """Quick AI summary of a chart — for the '📊 AI 总结这张图' button."""
    require_project_access(body.project_id, user, session)
    ctx = _project_context_brief(session, body.project_id)
    user_msg = f"""请对以下 Mermaid 图作快速分析：

```mermaid
{body.mermaid_code}
```

**项目已知问题**：
{ctx}

请输出（Markdown 格式，简短）：
1. **关键节点**（≤3 条）
2. **识别风险**（≤3 条）
3. **改进建议**（1~2 条）
4. **与项目已知问题的关联性**：哪些节点对应到具体问题编号？
"""
    try:
        text = llm.chat(
            [{"role": "user", "content": user_msg}],
            system="你是一位 MES/WMS 流程分析专家。回答简洁、有条理、使用 Markdown 列表。",
            max_tokens=2000,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM 调用失败: {e}")
    return {"summary": text}
