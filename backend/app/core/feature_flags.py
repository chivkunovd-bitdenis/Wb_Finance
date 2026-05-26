import os

from app.models.user import User


def _parse_csv_emails(raw: str) -> set[str]:
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def get_ai_module_allowlist_emails() -> set[str] | None:
    """
    Если переменная задана — ИИ-модуль доступен только перечисленным email (lower-case).
    Если пусто — ограничение по allowlist не применяется (как раньше).
    """
    raw = (os.getenv("AI_MODULE_ALLOWLIST_EMAILS") or "").strip()
    if not raw:
        return None
    emails = _parse_csv_emails(raw)
    return emails if emails else None


def is_ai_module_enabled_for_user(user: User) -> bool:
    allowlist = get_ai_module_allowlist_emails()
    if allowlist is None:
        return True
    email = (getattr(user, "email", None) or "").strip().lower()
    return email in allowlist


def is_ai_module_product_gen_enabled_for_user(user: User) -> bool:
    allowlist = get_ai_module_allowlist_emails()
    if allowlist is not None:
        return is_ai_module_enabled_for_user(user)
    return bool(getattr(user, "is_admin", False))


def is_daily_brief_enabled() -> bool:
    raw = (os.getenv("DAILY_BRIEF_ENABLED") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def is_ai_daily_analytics_beat_enabled() -> bool:
    raw = (os.getenv("AI_DAILY_ANALYTICS_BEAT_ENABLED") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}

