"""Document upload + listing (local FS, no MinIO for 2C2G)."""
import os, uuid
from typing import List
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Form
from fastapi.responses import FileResponse
from sqlmodel import Session, select
from ..config import settings
from ..auth import get_current_user, require_project_access
from ..db import get_session
from ..models import Document, User, AuditLog
from ..schemas import DocumentOut

router = APIRouter(prefix="/api/documents", tags=["documents"])


ALLOWED = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/pdf": "pdf",
    "text/plain": "txt",
    "text/markdown": "md",
}


@router.post("/upload", response_model=DocumentOut)
async def upload(
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
    return doc


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
