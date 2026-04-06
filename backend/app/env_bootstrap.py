"""
Подгрузка backend/.env до импорта сервисов.

В Docker файл монтируется как /app/.env. Сначала load_dotenv(override=False), чтобы не
перебить DATABASE_URL и др. из docker compose.

Отдельно: YOOKASSA_* из файла с непустым значением всегда записываются в os.environ.
Иначе compose/env_file могли передать пустые строки, и override=False не подхватит ключи из .env —
в итоге в ЮKassa уходят пустой логин/пароль и приходит 401.
"""

import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

_root = Path(__file__).resolve().parent.parent
_env_file = _root / ".env"
if _env_file.is_file():
    load_dotenv(_env_file, override=False)
    _yookassa_keys = (
        "YOOKASSA_SHOP_ID",
        "YOOKASSA_SECRET_KEY",
        "YOOKASSA_RETURN_URL",
        "YOOKASSA_WEBHOOK_SECRET",
    )
    for key, val in dotenv_values(_env_file).items():
        if key not in _yookassa_keys or val is None:
            continue
        s = str(val).strip()
        if s:
            os.environ[key] = s
