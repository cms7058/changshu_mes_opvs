"""Issues router — manual CRUD + AI-powered extraction from documents."""
import re, json, sys, traceback
from datetime import datetime
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select
from ..auth import get_current_user, require_project_access, require_role
from ..db import get_session, engine
from ..models import Issue, Document, User, AuditLog
from .. import llm

router = APIRouter(prefix="/api/issues", tags=["issues"])

# Bounded pool — LLM calls are slow; 2 concurrent is safe on 2C2G
EXTRACT_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="extract")


# ========== Schemas ==========
class IssueOut(BaseModel):
    id: int
    project_id: int
    code: str
    category: str
    severity: str
    title: str
    description: str
    proposed_solution: Optional[str]
    required_inputs: Optional[str]
    status: str
    source_doc_id: Optional[int]
    source_section: Optional[str] = None  # extracted from description
    owner_id: Optional[int]
    approved_by: Optional[int]
    approved_at: Optional[datetime]
    created_at: datetime


class IssueIn(BaseModel):
    category: str
    severity: str = "mid"
    title: str
    description: str
    proposed_solution: Optional[str] = None
    required_inputs: Optional[str] = None
    source_doc_id: Optional[int] = None


class IssueUpdate(BaseModel):
    category: Optional[str] = None
    severity: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    proposed_solution: Optional[str] = None
    required_inputs: Optional[str] = None
    status: Optional[str] = None


class ExtractIn(BaseModel):
    project_id: int
    doc_ids: List[int]
    replace_existing: bool = False  # if True, delete previous AI-extracted issues for these docs


# ========== Helpers ==========
def _gen_code(s: Session, project_id: int, category: str) -> str:
    """Generate unique sequential code like A1, B2, ..."""
    prefix = (category or "X")[0].upper()
    rows = s.exec(
        select(Issue).where(Issue.project_id == project_id)
    ).all()
    used_nums = []
    for r in rows:
        m = re.match(rf"{prefix}(\d+)", r.code or "")
        if m: used_nums.append(int(m.group(1)))
    n = max(used_nums) + 1 if used_nums else 1
    return f"{prefix}{n}"


EXTRACT_SYSTEM_PROMPT = """你是一位资深 MES/WMS 运维顾问。任务是从用户上传的项目文档中提取业务问题、风险点、待办事项。

**输出要求**：
- 必须输出**纯 JSON 数组**，不要任何解释文字、不要 ```json 标记
- 每个问题对象包含字段：category / severity / title / description / proposed_solution / required_inputs / source_section
- category 取值：
  - "A集成与主数据" / "B入库" / "C出库与COGI" / "D车间协同" / "E退货调拨" / "F上线切换" / "G其他"
- severity 取值: "high" / "mid" / "low"
- title 简短（≤25字）
- description ≤200字，明确问题是什么
- proposed_solution ≤200字，给出初步解决思路
- required_inputs ≤120字，说明需要甲方提供什么（如数据库字段、流程定义、参数等）
- source_section 标注来源（如"slide 5"、"step 50"、"5.3 章节"等）

不要编造，文档没明确提到的问题不要写。如果文档很短或没有可提取的问题，输出 `[]`。"""


def _extract_json_array(text: str) -> list:
    """Robustly extract JSON array from LLM output."""
    text = text.strip()
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try to find first [ ... ] block
    m = re.search(r'\[[\s\S]*\]', text)
    if m:
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # Try removing trailing commas
            try:
                cleaned = re.sub(r',(\s*[}\]])', r'\1', candidate)
                return json.loads(cleaned)
            except Exception:
                pass
    return []


def _extract_one_doc(project_id: int, doc_id: int, user_id: int, replace: bool):
    """Background: extract issues from one document via LLM."""
    print(f"[extract] START project={project_id} doc_id={doc_id}", flush=True)
    try:
        with Session(engine) as s:
            doc = s.get(Document, doc_id)
            if not doc:
                print(f"[extract] doc {doc_id} not found", flush=True); return
            if not doc.extracted_text or len(doc.extracted_text.strip()) < 50:
                print(f"[extract] doc {doc_id} no extractable text", flush=True); return

            # Optionally delete previous AI-extracted issues for this doc
            if replace:
                old = s.exec(select(Issue).where(
                    Issue.project_id == project_id,
                    Issue.source_doc_id == doc_id,
                    Issue.status.in_(["open", "ai_draft"]),
                )).all()
                for o in old: s.delete(o)
                s.commit()
                print(f"[extract] deleted {len(old)} old issues for doc {doc_id}", flush=True)

            # Cap text — M2.7 is a reasoning model, long input → very long thinking
            text = doc.extracted_text[:12000]
            user_msg = f"文档名：{doc.filename}\n\n文档内容：\n{text}\n\n请按要求提取问题，输出 JSON 数组。"

            # Call LLM via streaming so we get progress visibility
            print(f"[extract] doc {doc_id} calling LLM (text={len(text)} chars)...", flush=True)
            import time
            t0 = time.time()
            last_log = [t0]
            def progress(text_so_far, _delta):
                now = time.time()
                if now - last_log[0] >= 5:
                    print(f"[extract] doc {doc_id} streaming... {len(text_so_far)} chars, {int(now-t0)}s elapsed", flush=True)
                    last_log[0] = now
            response = llm.collect_stream(
                [{"role": "user", "content": user_msg}],
                system=EXTRACT_SYSTEM_PROMPT,
                max_tokens=4000,
                on_chunk=progress,
                timeout=180.0,
            )
            print(f"[extract] doc {doc_id} got {len(response)} chars in {int(time.time()-t0)}s", flush=True)
            # Print first 200 chars of response for debugging
            preview = response[:200].replace("\n", " ")
            print(f"[extract] doc {doc_id} preview: {preview}", flush=True)

            issues = _extract_json_array(response)
            print(f"[extract] doc {doc_id} parsed {len(issues)} issues", flush=True)

            # Insert into DB
            inserted = 0
            for iss in issues:
                try:
                    category = iss.get("category", "G其他")[:32]
                    code = _gen_code(s, project_id, category)
                    row = Issue(
                        project_id=project_id,
                        code=code,
                        category=category,
                        severity=iss.get("severity", "mid"),
                        title=(iss.get("title") or "")[:200],
                        description=(iss.get("description") or "")[:2000]
                                    + (f"\n\n[来源: {iss['source_section']}]" if iss.get("source_section") else ""),
                        proposed_solution=iss.get("proposed_solution"),
                        required_inputs=iss.get("required_inputs"),
                        source_doc_id=doc_id,
                        status="ai_draft",
                        created_by=user_id,
                    )
                    s.add(row)
                    s.commit()
                    inserted += 1
                except Exception as e:
                    print(f"[extract] insert error: {e}", flush=True)
                    s.rollback()
            print(f"[extract] DONE doc_id={doc_id} inserted={inserted}", flush=True)
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[extract] EXCEPTION doc_id={doc_id}: {e}\n{tb}", file=sys.stderr, flush=True)


# ========== Routes ==========
@router.get("/by_project/{project_id}", response_model=List[IssueOut])
def list_issues(project_id: int, user: User = Depends(get_current_user),
                session: Session = Depends(get_session)):
    require_project_access(project_id, user, session)
    rows = session.exec(
        select(Issue).where(Issue.project_id == project_id).order_by(Issue.code)
    ).all()
    return rows


@router.post("/extract")
def extract_issues(body: ExtractIn,
                   user: User = Depends(get_current_user),
                   session: Session = Depends(get_session)):
    require_project_access(body.project_id, user, session)
    docs = session.exec(
        select(Document).where(
            Document.id.in_(body.doc_ids),
            Document.project_id == body.project_id,
            Document.parse_status == "done",
        )
    ).all()
    if not docs:
        raise HTTPException(status_code=400, detail="所选文档都未完成解析或不属于该项目")
    for d in docs:
        EXTRACT_POOL.submit(_extract_one_doc, body.project_id, d.id, user.id, body.replace_existing)
    session.add(AuditLog(user_id=user.id, action="issue.extract",
                         payload=f"prj={body.project_id} docs={len(docs)}"))
    session.commit()
    return {"queued": len(docs), "doc_ids": [d.id for d in docs]}


@router.post("", response_model=IssueOut)
def create_issue(body: IssueIn,
                 user: User = Depends(get_current_user),
                 session: Session = Depends(get_session)):
    # Use project_id inferred from source_doc if available, else needs explicit
    # For manual creation we'll require source_doc_id OR add project_id field
    if body.source_doc_id:
        doc = session.get(Document, body.source_doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="源文档不存在")
        project_id = doc.project_id
    else:
        raise HTTPException(status_code=400, detail="请提供 source_doc_id 以推断 project_id")
    require_project_access(project_id, user, session)
    code = _gen_code(session, project_id, body.category)
    issue = Issue(
        project_id=project_id,
        code=code,
        category=body.category,
        severity=body.severity,
        title=body.title,
        description=body.description,
        proposed_solution=body.proposed_solution,
        required_inputs=body.required_inputs,
        source_doc_id=body.source_doc_id,
        status="open",
        created_by=user.id,
    )
    session.add(issue); session.commit(); session.refresh(issue)
    return issue


@router.patch("/{issue_id}", response_model=IssueOut)
def update_issue(issue_id: int, body: IssueUpdate,
                 user: User = Depends(get_current_user),
                 session: Session = Depends(get_session)):
    issue = session.get(Issue, issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="问题不存在")
    require_project_access(issue.project_id, user, session)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(issue, k, v)
    issue.updated_at = datetime.utcnow()
    session.add(issue); session.commit(); session.refresh(issue)
    return issue


@router.post("/{issue_id}/approve")
def approve_issue(issue_id: int,
                  user: User = Depends(require_role("admin")),
                  session: Session = Depends(get_session)):
    issue = session.get(Issue, issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="问题不存在")
    issue.status = "approved"
    issue.approved_by = user.id
    issue.approved_at = datetime.utcnow()
    session.add(issue)
    session.add(AuditLog(user_id=user.id, action="issue.approve", payload=f"id={issue_id}"))
    session.commit()
    return {"ok": True}


@router.delete("/{issue_id}")
def delete_issue(issue_id: int,
                 user: User = Depends(get_current_user),
                 session: Session = Depends(get_session)):
    issue = session.get(Issue, issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="问题不存在")
    require_project_access(issue.project_id, user, session)
    session.delete(issue)
    session.add(AuditLog(user_id=user.id, action="issue.delete", payload=f"id={issue_id}"))
    session.commit()
    return {"ok": True}
