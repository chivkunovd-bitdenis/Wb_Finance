import os
from pathlib import Path

from fastapi import FastAPI
from alembic import command
from alembic.config import Config

from app.routers import auth, sync

app = FastAPI(title="WB Finance API", version="0.1.0")

# Корень backend (в Docker это /app)
BASE_DIR = Path(__file__).resolve().parent.parent

app.include_router(auth.router)
app.include_router(sync.router)


@app.on_event("startup")
def run_migrations():
    alembic_cfg = Config(BASE_DIR / "alembic.ini")
    alembic_cfg.set_main_option("script_location", str(BASE_DIR / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", os.getenv("DATABASE_URL", ""))
    if alembic_cfg.get_main_option("sqlalchemy.url"):
        command.upgrade(alembic_cfg, "head")


@app.get("/health")
def health():
    return {"status": "ok"}
