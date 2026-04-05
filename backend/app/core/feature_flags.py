import os


def is_daily_brief_enabled() -> bool:
    raw = (os.getenv("DAILY_BRIEF_ENABLED") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}

