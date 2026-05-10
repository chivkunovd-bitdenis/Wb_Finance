import os


def is_daily_brief_enabled() -> bool:
    raw = (os.getenv("DAILY_BRIEF_ENABLED") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def is_ai_daily_analytics_beat_enabled() -> bool:
    raw = (os.getenv("AI_DAILY_ANALYTICS_BEAT_ENABLED") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}

