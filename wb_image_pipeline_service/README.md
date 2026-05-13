# WB Image Pipeline Service

Отдельный сервис для **очередей, state machine и генерации изображений** по референсу. Папку можно **скопировать в новый репозиторий** и поднять с нуля (см. «Вынос в новый проект»).

## Зачем отдельно от монолита

- Переиспользование цепочки «промпт → референс → N картинок» во втором продукте.
- Изоляция тяжёлых воркеров и файлового volume.
- Собственный жизненный цикл релизов.

## Архитектура очередей (ответ на Q4)

**Рекомендуемый вариант:** вся **оркестрация шагов** и **Celery** живут **здесь**, в микросервисе.

| Компонент | Где |
|-----------|-----|
| Redis (broker/backend Celery) | Общий инстанс с отдельным **DB index** (например `/1`) или отдельный Redis — см. `.env.example` |
| БД job-ов, шагов, промптов, путей к файлам | **Своя** БД сервиса (SQLite в volume для dev; Postgres в prod) |
| Файлы изображений | Volume **этого** сервиса (согласовано с монолитом: метаданные + прокси URL отдаёт монолит) |
| Домен «задача продавца», WB publish, `is_admin` | Монолит wb-finance |

**Монолит:**

1. Создаёт у себя черновик «полная генерация товара» и проверяет `user.is_admin`.
2. Вызывает этот сервис: `POST /internal/v1/runs` (тело: ссылки на загруженные референсы или presigned payload — контракт уточняется при интеграции).
3. Сохраняет `run_id` (UUID) у черновика.
4. Поллит `GET /internal/v1/runs/{run_id}` **или** принимает webhook (опционально) о смене статуса.
5. Отдаёт фронту **реальные картинки** (не только JSON): прокси-эндпоинт в монолите тянет байты из сервиса по `asset_id` и отдаёт браузеру; во фронте — превью + lightbox на полный размер.

**Микросервис:**

1. Ставит в очередь цепочку: структуризация (вызов OpenAI в воркере) → 4 главных изображения → *пауза до команды монолита с выбранным main* → 8 серийных изображений.
2. Хранит промпты и результаты в своей БД (логи/аудит).
3. По TTL **14 дней** удаляет файлы с volume (задача cron/Celery beat внутри сервиса; дата успеха публикации приходит с монолита или по событию `run_archived`).

Человеческие ошибки WB при publish остаются **в монолите** (там вызов `cards/upload`), с понятным текстом для UI.

## Безопасность (Q3: mTLS)

Целевой режим: **mutual TLS** между контейнерами `api` (монолит) и `wb_image_pipeline_service` в Docker-сети.

- В dev можно временно ограничиться изоляцией сети + общим секретом; прод — выпуск CA, client cert для монолита, verify client на стороне сервиса (часто через reverse-proxy).
- Детали монтажа сертификатов — в `docs/mtls.md` (добавить при первом прод-вкате).

## Переменные окружения (LLM, Q7)

См. `.env.example`: модели текста зафиксированы как в дискавери (`gpt-4.1-mini` для структуризации, `gpt-4.1` для пакета промптов серии). Модель **изображений** — отдельная переменная (OpenAI image API).

## Вынос в новый проект

1. Скопировать всю папку `wb_image_pipeline_service/` в корень нового репо.
2. Задать `.env` из `.env.example`.
3. `docker compose -f docker-compose.example.yml up -d --build`.
4. Подключить ваш продукт как «монолит» по HTTP/mTLS контракту.

## Статус

- **PG-3.1:** своя БД сервиса — таблицы `wip_runs`, `wip_steps`, `wip_assets` (SQLAlchemy + Alembic `a1b2c3d4e501`). При старте контейнера выполняется `alembic upgrade head`. `docker-compose.example` монтирует **`wip_data:/data`** (общий SQLite и каталог `media` для API и worker).
- **PG-3.2:** Celery-цепочка-заглушка **`wb_image_pipeline.run_created` → `wb_image_pipeline.step_done`** (модуль `celery_app/pipeline_tasks.py`, постановка `enqueue_pg32_stub_chain(run_id)`). Воркер в compose — `celery -A celery_app.celery_app worker`; брокер/backend — **Redis** (`wip_redis` в примере). Логи на INFO; повторы задач идемпотентны по строкам `wip_runs` / `wip_steps` (шаг `pg32_stub`).
- **PG-3.3:** HTTP **`POST /internal/v1/runs`**, **`GET /internal/v1/runs/{id}`** — реализация в `app/api/internal_runs.py`, логика в `app/services/internal_runs_service.py`, схемы в `app/schemas/internal_runs.py`. Аутентификация: `Authorization: Bearer <WIP_INTERNAL_HMAC_SECRET>`. После успешного `POST` ставится очередь PG-3.2. Подробный контракт — ниже.
- Дальше по плану wb-finance: **PG-3.4** (связка монолит ↔ сервис: реальный `POST` при «Создать», сохранение `run_id`, поллинг).

### HTTP внутренний API (PG-3.3)

Префикс **`/internal/v1`**. До mTLS (PG-3.5) используйте приватную сеть и секрет из **`WIP_INTERNAL_HMAC_SECRET`** (см. `.env.example`, `docs/mtls.md`).

| Метод | Путь | Назначение |
|-------|------|------------|
| `POST` | `/internal/v1/runs` | Создать run, сохранить связь с монолитом и метаданные, поставить Celery-цепочку PG-3.2 |
| `GET` | `/internal/v1/runs/{id}` | Статус run, `payload`, шаги и ассеты из БД сервиса |

**Заголовок:** `Authorization: Bearer <WIP_INTERNAL_HMAC_SECRET>`

**`POST /internal/v1/runs` — тело (JSON):**

- `monolith_job_id` (string, обязательный, 1…64 символов) — идентификатор задачи/черновика в монолите
- `payload` (object, опционально) — произвольный JSON; сохраняется в `wip_runs.payload_json`

**Ответ `201`:** `{ "id": "<uuid>", "status": "created" }`

**Ошибки:** `401` — нет/неверный Bearer; `503` — не удалось поставить задачу в Celery (run уже записан в БД со статусом `created` — см. политику повторов в PG-3.4).

**`GET /internal/v1/runs/{id}` — ответ `200`:** поля `id`, `status`, `monolith_job_id`, `payload`, `created_at`, `updated_at`, массивы `steps` и `assets` (как в ORM: `step_key`, `ordinal`, `status`, `error_message`, `meta_json`, пути к файлам и т.д.).

**Ошибки:** `401`; `404` — run не найден.

Полная схема запросов/ответов также в **OpenAPI** (`/docs` на порту API, по умолчанию 9100).

### Миграции (локально)

```bash
cd wb_image_pipeline_service
export WIP_DATABASE_URL="sqlite:////tmp/wip_dev.db"
alembic upgrade head
```

### Тесты схемы

```bash
cd wb_image_pipeline_service
pip install -r requirements.txt pytest
pytest
```
