"""Document upload + listing (local FS, no MinIO for 2C2G)."""
import os, uuid
from datetime import datetime
from typing import List
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Form, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
from sqlmodel import Session, select
from ..config import settings
from ..auth import get_current_user, require_project_access
from ..db import get_session, engine
from ..models import Document, User, AuditLog
from ..schemas import DocumentOut
from ..parsers import parse_file, SUPPORTED

router = APIRouter(prefix="/api/documents", tags=["documents"])


ALLOWED = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/pdf": "pdf",
    "text/plain": "txt",
    "text/markdown": "md",
}


def _parse_document_task(doc_id: int):
    """Background: parse document, save text+html with inline image data URIs."""
    import traceback, sys
    print(f"[parse] START doc_id={doc_id}", flush=True)
    try:
        from sqlalchemy.orm import sessionmaker
        Session2 = sessionmaker(bind=engine)
        with Session2() as s:
            doc = s.get(Document, doc_id)
            if not doc:
                print(f"[parse] doc {doc_id} not found", flush=True)
                return
            ext = os.path.splitext(doc.filename)[1].lower()
            print(f"[parse] doc_id={doc_id} file={doc.filename} ext={ext}", flush=True)
            if ext not in SUPPORTED:
                doc.parse_status = "unsupported"
                doc.parse_error = f"暂不支持解析 {ext}（目前仅 .docx / .pptx）"
                doc.parsed_at = datetime.utcnow()
                s.add(doc); s.commit()
                print(f"[parse] doc_id={doc_id} unsupported", flush=True)
                return
            abs_file = os.path.join(settings.UPLOAD_DIR, doc.storage_path)
            asset_dir_rel = os.path.dirname(doc.storage_path) + f"/doc_{doc_id}_assets"
            asset_dir_abs = os.path.join(settings.UPLOAD_DIR, asset_dir_rel)
            print(f"[parse] doc_id={doc_id} abs_file={abs_file} exists={os.path.exists(abs_file)}", flush=True)
            result = parse_file(abs_file, asset_dir_abs)
            print(f"[parse] doc_id={doc_id} result.status={result['status']} text_len={len(result['text'])} html_len={len(result['html'])}", flush=True)
            doc.parse_status = result["status"]
            doc.parse_error = result["error"]
            doc.extracted_text = result["text"]
            doc.extracted_html = result["html"]
            doc.asset_dir = asset_dir_rel if result["status"] == "done" else None
            doc.parsed_at = datetime.utcnow()
            s.add(doc); s.commit()
            print(f"[parse] DONE doc_id={doc_id} status={result['status']}", flush=True)
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[parse] EXCEPTION doc_id={doc_id}: {e}\n{tb}", file=sys.stderr, flush=True)
        try:
            from sqlalchemy.orm import sessionmaker
            Session2 = sessionmaker(bind=engine)
            with Session2() as s:
                doc = s.get(Document, doc_id)
                if doc:
                    doc.parse_status = "failed"
                    doc.parse_error = f"{type(e).__name__}: {e}"
                    doc.parsed_at = datetime.utcnow()
                    s.add(doc); s.commit()
        except Exception:
            pass


@router.post("/upload", response_model=DocumentOut)
async def upload(
    background_tasks: BackgroundTasks,
    project_id: int = Form(...),
    kind: str = Form("orig"),
    version: str = Form("v1"),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    # permission
    require_project_access(project_id, user, session)

    # size cap
    contents = await file.read()
    if len(contents) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"超过 {settings.MAX_UPLOAD_MB}MB 上限")

    # mime
    mime = file.content_type or "application/octet-stream"

    # save to disk
    project_dir = os.path.join(settings.UPLOAD_DIR, f"project_{project_id}")
    os.makedirs(project_dir, exist_ok=True)
    safe_id = uuid.uuid4().hex[:12]
    safe_name = f"{safe_id}__{file.filename}"
    abs_path = os.path.join(project_dir, safe_name)
    with open(abs_path, "wb") as f:
        f.write(contents)

    doc = Document(
        project_id=project_id,
        filename=file.filename or safe_name,
        mime_type=mime,
        size_bytes=len(contents),
        storage_path=os.path.relpath(abs_path, settings.UPLOAD_DIR),
        kind=kind,
        version=version,
        uploaded_by=user.id,
    )
    session.add(doc)
    session.add(AuditLog(user_id=user.id, action="doc.upload",
                         payload=f"prj={project_id} name={file.filename}"))
    session.commit()
    session.refresh(doc)
    # trigger background parse
    background_tasks.add_task(_parse_document_task, doc.id)
    return doc


@router.post("/upload_batch")
async def upload_batch(
    background_tasks: BackgroundTasks,
    project_id: int = Form(...),
    kind: str = Form("orig"),
    version: str = Form("v1"),
    files: List[UploadFile] = File(...),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Upload multiple files. Returns {uploaded:[...], failed:[...]} as plain dicts."""
    require_project_access(project_id, user, session)
    project_dir = os.path.join(settings.UPLOAD_DIR, f"project_{project_id}")
    os.makedirs(project_dir, exist_ok=True)

    uploaded: list[dict] = []
    failed: list[dict] = []
    cap = settings.MAX_UPLOAD_MB * 1024 * 1024

    for f in files:
        try:
            data = await f.read()
            if len(data) > cap:
                failed.append({"filename": f.filename, "error": f"超过 {settings.MAX_UPLOAD_MB}MB 上限"})
                continue
            safe_id = uuid.uuid4().hex[:12]
            safe_name = f"{safe_id}__{f.filename}"
            abs_path = os.path.join(project_dir, safe_name)
            with open(abs_path, "wb") as fp:
                fp.write(data)
            doc = Document(
                project_id=project_id,
                filename=f.filename or safe_name,
                mime_type=f.content_type or "application/octet-stream",
                size_bytes=len(data),
                storage_path=os.path.relpath(abs_path, settings.UPLOAD_DIR),
                kind=kind,
                version=version,
                uploaded_by=user.id,
            )
            session.add(doc)
            session.commit()
            session.refresh(doc)
            background_tasks.add_task(_parse_document_task, doc.id)
            uploaded.append({
                "id": doc.id,
                "project_id": doc.project_id,
                "filename": doc.filename,
                "mime_type": doc.mime_type,
                "size_bytes": doc.size_bytes,
                "kind": doc.kind,
                "version": doc.version,
                "uploaded_at": doc.uploaded_at.isoformat(),
            })
        except Exception as e:
            failed.append({"filename": getattr(f, "filename", "?"), "error": str(e)})

    session.add(AuditLog(user_id=user.id, action="doc.upload_batch",
                         payload=f"prj={project_id} ok={len(uploaded)} fail={len(failed)}"))
    session.commit()
    return {"uploaded": uploaded, "failed": failed}


@router.get("/by_project/{project_id}", response_model=List[DocumentOut])
def list_docs(project_id: int, user: User = Depends(get_current_user),
              session: Session = Depends(get_session)):
    require_project_access(project_id, user, session)
    return session.exec(
        select(Document).where(Document.project_id == project_id).order_by(Document.id.desc())
    ).all()


@router.get("/{doc_id}/download")
def download(doc_id: int, user: User = Depends(get_current_user),
             session: Session = Depends(get_session)):
    doc = session.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    require_project_access(doc.project_id, user, session)
    abs_path = os.path.join(settings.UPLOAD_DIR, doc.storage_path)
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=410, detail="文件已丢失")
    return FileResponse(abs_path, filename=doc.filename, media_type=doc.mime_type)


@router.get("/{doc_id}/preview", response_class=HTMLResponse)
def preview(doc_id: int, user: User = Depends(get_current_user),
            session: Session = Depends(get_session)):
    doc = session.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    require_project_access(doc.project_id, user, session)
    if doc.parse_status != "done":
        return HTMLResponse(
            f'<div style="padding:30px;color:#6b7280;font-family:sans-serif">'
            f'解析状态：<b>{doc.parse_status}</b><br>'
            f'{html_escape(doc.parse_error or "请稍候或点击重新解析")}</div>'
        )
    css = """<style>
      body{font:14px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#fff;color:#1f2937;padding:24px;max-width:920px;margin:0 auto}
      h1{font-size:22px;margin:14px 0 8px;text-align:center}
      h2{font-size:17px;margin:18px 0 8px;color:#1e40af;border-bottom:1px solid #e5e7eb;padding-bottom:4px}
      h3{font-size:14px;margin:14px 0 6px;color:#334155}
      p{margin:5px 0}
      p.center{text-align:center}
      table.doc-tbl{border-collapse:collapse;width:100%;margin:10px 0;font-size:13px}
      table.doc-tbl td{border:1px solid #cbd5e1;padding:7px 9px;vertical-align:top}
      td.cell-head{background:#B4C7E7;font-weight:600;text-align:center}
      td.cell-new{background:#DCE7F5}
      td.cell-rev{background:#FFF2CC}
      img.doc-img{max-width:100%;display:block;margin:10px auto;border:1px solid #e5e7eb;border-radius:6px}
      section.slide{border:1px solid #e5e7eb;border-radius:10px;padding:16px 22px;margin:14px 0;background:#fafafa}
      .slide-no{font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px}
    </style>"""
    return HTMLResponse(f"<!doctype html><html><head><meta charset='utf-8'>{css}</head><body>{doc.extracted_html or ''}</body></html>")


def html_escape(s: str) -> str:
    import html as _h
    return _h.escape(s or "")


@router.get("/{doc_id}/asset/{name}")
def get_asset(doc_id: int, name: str, user: User = Depends(get_current_user),
              session: Session = Depends(get_session)):
    doc = session.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    require_project_access(doc.project_id, user, session)
    if not doc.asset_dir:
        raise HTTPException(status_code=404, detail="无资源目录")
    # security: only basename allowed
    safe = os.path.basename(name)
    abs_path = os.path.join(settings.UPLOAD_DIR, doc.asset_dir, safe)
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="资源不存在")
    return FileResponse(abs_path)


@router.post("/reparse_all/{project_id}")
def reparse_all(project_id: int, background_tasks: BackgroundTasks,
                user: User = Depends(get_current_user),
                session: Session = Depends(get_session)):
    """Re-queue parse for all non-done docs in a project (useful after migration)."""
    require_project_access(project_id, user, session)
    docs = session.exec(
        select(Document).where(
            Document.project_id == project_id,
            Document.parse_status != "done",
        )
    ).all()
    for d in docs:
        d.parse_status = "pending"
        d.parse_error = None
        session.add(d)
    session.commit()
    for d in docs:
        background_tasks.add_task(_parse_document_task, d.id)
    return {"queued": len(docs), "doc_ids": [d.id for d in docs]}


@router.post("/{doc_id}/reparse")
def reparse(doc_id: int, background_tasks: BackgroundTasks,
            user: User = Depends(get_current_user),
            session: Session = Depends(get_session)):
    doc = session.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    require_project_access(doc.project_id, user, session)
    doc.parse_status = "pending"
    doc.parse_error = None
    session.add(doc); session.commit()
    background_tasks.add_task(_parse_document_task, doc_id)
    return {"ok": True}


@router.delete("/{doc_id}")
def delete_doc(doc_id: int, user: User = Depends(get_current_user),
               session: Session = Depends(get_session)):
    doc = session.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    require_project_access(doc.project_id, user, session)
    abs_path = os.path.join(settings.UPLOAD_DIR, doc.storage_path)
    if os.path.exists(abs_path):
        os.remove(abs_path)
    session.delete(doc)
    session.add(AuditLog(user_id=user.id, action="doc.delete", payload=f"id={doc_id}"))
    session.commit()
    return {"ok": True}
