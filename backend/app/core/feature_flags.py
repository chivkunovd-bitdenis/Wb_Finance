import os

from app.models.user import User


def _parse_csv_emails(raw: str) -> set[str]:
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


_DEFAULT_EXTRA_EMAILS = "vitalik-hors@mail.ru"


def get_ai_module_allowlist_emails() -> set[str]:
    """
    Дополнительные email (кроме is_admin), кому виден ИИ-модуль.
    По умолчанию — Vitalik; переопределяется AI_MODULE_ALLOWLIST_EMAILS.
    """
    raw = (os.getenv("AI_MODULE_ALLOWLIST_EMAILS") or _DEFAULT_EXTRA_EMAILS).strip()
    emails = _parse_csv_emails(raw)
    return emails if emails else _parse_csv_emails(_DEFAULT_EXTRA_EMAILS)


def is_ai_module_enabled_for_user(user: User) -> bool:
    if bool(getattr(user, "is_admin", False)):
        return True
    email = (getattr(user, "email", None) or "").strip().lower()
    return email in get_ai_module_allowlist_emails()


def is_ai_module_product_gen_enabled_for_user(user: User) -> bool:
    return is_ai_module_enabled_for_user(user)


def is_daily_brief_enabled() -> bool:
    raw = (os.getenv("DAILY_BRIEF_ENABLED") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def is_ai_daily_analytics_beat_enabled() -> bool:
    raw = (os.getenv("AI_DAILY_ANALYTICS_BEAT_ENABLED") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}

