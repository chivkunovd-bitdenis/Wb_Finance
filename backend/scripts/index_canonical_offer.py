"""Проиндексировать канонический файл оферты WB в Qdrant заранее (например, на проде перед
первым запросом в новый ИИ-чат, чтобы не индексировать на холодную под нагрузкой).

Запуск из каталога backend:

  cd backend && python scripts/index_canonical_offer.py

На проде:

  docker compose exec api python scripts/index_canonical_offer.py

Файл резолвится так же, как в рантайме (app.services.canonical_offer): переменная
окружения OFFER_CANONICAL_PATH, иначе единственный/новейший .pdf|.txt|.html в OFFER_DATA_DIR
(по умолчанию /app/data/offers).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

_env = ROOT_DIR / ".env"
if _env.is_file():
    from dotenv import load_dotenv

    load_dotenv(_env, override=False)

from app.services.canonical_offer import resolve_canonical_offer_path  # noqa: E402
from app.services.canonical_offer import ensure_canonical_offer_indexed  # noqa: E402
from app.services.offer_rag_service import count_version_points  # noqa: E402


def main() -> int:
    try:
        path = resolve_canonical_offer_path()
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"canonical offer file: {path}")
    try:
        version = ensure_canonical_offer_indexed()
    except Exception as exc:  # noqa: BLE001
        print(f"error: indexing failed: {exc}", file=sys.stderr)
        return 1

    points = count_version_points(version=version)
    print(f"ok: version={version} indexed_points={points}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
