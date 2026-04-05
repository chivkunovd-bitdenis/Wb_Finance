import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from alembic import command
from alembic.config import Config

from app.routers import auth, sync, dashboard, billing
from app.routers import daily_brief

app = FastAPI(title="WB Finance API", version="0.1.0")

_default_origins = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
    "https://sellerfocus.pro",
    "https://www.sellerfocus.pro",
    "https://app.sellerfocus.pro",
]
_raw_cors = (os.getenv("CORS_ORIGINS") or "").strip()
_cors_origins = (
    [o.strip() for o in _raw_cors.split(",") if o.strip()] if _raw_cors else _default_origins
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Корень backend (в Docker это /app)
BASE_DIR = Path(__file__).resolve().parent.parent

app.include_router(auth.router)
app.include_router(sync.router)
app.include_router(dashboard.router)
app.include_router(billing.router)
app.include_router(daily_brief.router)


@app.on_event("startup")
def run_migrations():
    alembic_cfg = Config(BASE_DIR / "alembic.ini")
    alembic_cfg.set_main_option("script_location", str(BASE_DIR / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", os.getenv("DATABASE_URL", ""))
    if alembic_cfg.get_main_option("sqlalchemy.url"):
        # Repo currently has multiple alembic heads; apply all on startup.
        command.upgrade(alembic_cfg, "heads")


@app.get("/health")
def health():
    return {"status": "ok"}
