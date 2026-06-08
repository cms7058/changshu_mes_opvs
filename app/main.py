"""FastAPI app entry."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os
from .config import settings
from .db import init_db
from .routers import auth, users, projects, documents, chat, settings as settings_router, issues, diagrams

app = FastAPI(title=settings.APP_NAME, version="0.1.0")

# CORS — open for internal tool; tighten in prod via reverse proxy
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    init_db()


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME, "env": settings.APP_ENV}


# Routers
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(projects.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(settings_router.router)
app.include_router(issues.router)
app.include_router(diagrams.router)

# Static frontend
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def root():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    @app.get("/login")
    def login_page():
        return FileResponse(os.path.join(STATIC_DIR, "login.html"))
