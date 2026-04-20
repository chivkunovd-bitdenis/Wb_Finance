import app.env_bootstrap  # noqa: F401 — .env до импорта роутеров/сервисов

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import auth, sync, dashboard, billing
from app.routers import daily_brief
from app.middleware.request_logging import RequestLoggingMiddleware

app = FastAPI(title="WB Finance API", version="0.1.0")
app.add_middleware(RequestLoggingMiddleware)

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

app.include_router(auth.router)
app.include_router(sync.router)
app.include_router(dashboard.router)
app.include_router(billing.router)
app.include_router(daily_brief.router)


@app.get("/health")
def health():
    return {"status": "ok"}
