"""Project space CRUD + membership."""
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from ..auth import get_current_user, require_role
from ..db import get_session
from ..models import Project, UserProject, User, AuditLog
from ..schemas import ProjectCreate, ProjectOut, GrantAccess

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=List[ProjectOut])
def list_projects(user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    if user.role == "admin":
        return session.exec(select(Project).order_by(Project.id.desc())).all()
    # only those user has access to
    ids = [m.project_id for m in session.exec(
        select(UserProject).where(UserProject.user_id == user.id)
    ).all()]
    if not ids: return []
    return session.exec(select(Project).where(Project.id.in_(ids))).all()


@router.post("", response_model=ProjectOut)
def create_project(
    data: ProjectCreate,
    user: User = Depends(require_role("admin", "engineer")),
    session: Session = Depends(get_session),
):
    p = Project(name=data.name, customer=data.customer, description=data.description, created_by=user.id)
    session.add(p)
    session.commit()
    session.refresh(p)
    # Creator gets admin permission
    session.add(UserProject(user_id=user.id, project_id=p.id, permission="admin"))
    session.add(AuditLog(user_id=user.id, action="project.create", payload=p.name))
    session.commit()
    return p


@router.post("/{project_id}/grant")
def grant_access(
    project_id: int,
    body: GrantAccess,
    actor: User = Depends(require_role("admin")),
    session: Session = Depends(get_session),
):
    if not session.get(Project, project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    if not session.get(User, body.user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    existing = session.exec(
        select(UserProject).where(UserProject.user_id == body.user_id,
                                  UserProject.project_id == project_id)
    ).first()
    if existing:
        existing.permission = body.permission
        session.add(existing)
    else:
        session.add(UserProject(user_id=body.user_id, project_id=project_id, permission=body.permission))
    session.add(AuditLog(user_id=actor.id, action="project.grant",
                         payload=f"prj={project_id} user={body.user_id} perm={body.permission}"))
    session.commit()
    return {"ok": True}


@router.get("/{project_id}/members")
def list_members(project_id: int, user: User = Depends(get_current_user),
                 session: Session = Depends(get_session)):
    rows = session.exec(
        select(UserProject, User)
        .where(UserProject.project_id == project_id, UserProject.user_id == User.id)
    ).all()
    return [{"user_id": u.id, "username": u.username, "role": u.role, "permission": m.permission}
            for m, u in rows]
