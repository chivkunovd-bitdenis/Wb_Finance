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
2. После загрузки ≥1 референса вызывает `POST /internal/v1/runs` с **`payload.reference_asset_ids`** (и опциональными полями карточки, часто `null` на фазе IMAGE — см. контракт HTTP ниже).
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

**PG-B.1 (фаза IMAGE):** `WIP_IMAGE_PROMPT_TEMPLATE` (полный текст с `{user_text}`; пусто — встроенный шаблон), `WIP_IMAGE_PROMPT_USER_TEXT_MAX_CHARS`. После `run_created` в `GET /internal/v1/runs/{id}` в `payload` появляются `wip_effective_image_prompt`, `wip_prompt_template_version`, `wip_prompt_template_hash`.

**PG-B.2:** `WIP_OPENAI_API_KEY`, `WIP_OPENAI_MODEL_STRUCTURE`, `WIP_OPENAI_TIMEOUT_SEC` — вызов OpenAI на шаге `structure_main`; при ошибке шаг `failed`, текст в `error_message`.

## Вынос в новый проект

1. Скопировать всю папку `wb_image_pipeline_service/` в корень нового репо.
2. Задать `.env` из `.env.example`.
3. `docker compose -f docker-compose.example.yml up -d --build`.
4. Подключить ваш продукт как «монолит» по HTTP/mTLS контракту.

## Статус

- **PG-3.1:** своя БД сервиса — таблицы `wip_runs`, `wip_steps`, `wip_assets` (SQLAlchemy + Alembic `a1b2c3d4e501`). При старте контейнера выполняется `alembic upgrade head`. `docker-compose.example` монтирует **`wip_data:/data`** (общий SQLite и каталог `media` для API и worker).
- **PG-3.2:** Celery-цепочка **`wb_image_pipeline.run_created` → `wb_image_pipeline.structure_main` → `wb_image_pipeline.step_done`** (`celery_app/pipeline_tasks.py`, `enqueue_pg32_stub_chain`). Шаг **`structure_main`** (PG-B.2): OpenAI `chat/completions` с `response_format: json_object`, модель `WIP_OPENAI_MODEL_STRUCTURE`, результат в `wip_steps.meta_json` (`seo_title`, `seo_description`, `main_prompts` ×4). Шаг **`pg32_stub`** по-прежнему финализирует run для dev. Воркер — `celery -A celery_app.celery_app worker`; Redis — брокер. Идемпотентность по `wip_runs` / `wip_steps`.
- **PG-3.3:** HTTP **`POST /internal/v1/runs`**, **`GET /internal/v1/runs/{id}`** — реализация в `app/api/internal_runs.py`, логика в `app/services/internal_runs_service.py`, схемы в `app/schemas/internal_runs.py`. Аутентификация: `Authorization: Bearer <WIP_INTERNAL_HMAC_SECRET>`. После успешного `POST` ставится очередь PG-3.2. Подробный контракт — ниже.
- **PG-3.4:** связка с монолитом wb-finance — при старте задачи (`POST /ai/product-generation/jobs/{id}/start`) монолит может вызвать этот сервис (см. **`PRODUCT_GEN_IMAGE_PIPELINE_*`** в `backend/.env.example`); `pipeline_run_id` в монолите = UUID run; поллинг статуса — через `GET` jobs в монолите (он проксирует `GET /internal/v1/runs/{id}` в поле `image_pipeline`).
- Дальше по плану wb-finance: **PG-3.5** (mTLS / прод-HMAC, см. `docs/mtls.md`).

### HTTP внутренний API (PG-3.3)

Префикс **`/internal/v1`**. До mTLS (PG-3.5) используйте приватную сеть и секрет из **`WIP_INTERNAL_HMAC_SECRET`** (см. `.env.example`, `docs/mtls.md`).

| Метод | Путь | Назначение |
|-------|------|------------|
| `POST` | `/internal/v1/runs` | Создать run, сохранить связь с монолитом и метаданные, поставить Celery-цепочку PG-3.2 |
| `GET` | `/internal/v1/runs/{id}` | Статус run, `payload`, шаги и ассеты из БД сервиса |

**Заголовок:** `Authorization: Bearer <WIP_INTERNAL_HMAC_SECRET>`

**`POST /internal/v1/runs` — тело (JSON):**

- `monolith_job_id` (string, обязательный, 1…64 символов) — идентификатор задачи/черновика в монолите
- `payload` (object, **обязателен**, PG-A.2) — JSON для фазы **IMAGE**; сохраняется в `wip_runs.payload_json`. Минимум:
  - `reference_asset_ids`: непустой массив непустых строк (id референсов, загруженных в монолит);
  - остальное опционально: `description_user`, `title`, `vendor_code`, `brand`, `wb_subject_id`, `seo_description`, `price_kopeks`, габариты, `sizes_json` и т.д. — **допускаются `null`**, воркер каскада не должен требовать их для старта run.
  - неизвестные ключи допускаются (форвард-совместимость).

**Ответ `201`:** `{ "id": "<uuid>", "status": "created" }`

**Ошибки:** `401` — нет/неверный Bearer; `422` — нет `payload` или невалидные `reference_asset_ids`; `503` — не удалось поставить задачу в Celery (run уже записан в БД со статусом `created` — см. политику повторов в PG-3.4).

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
