# План: полная генерация товара (ИИ) — для Builder

**Роль:** planner → **следующий шаг:** builder выполняет фазы по `PRODUCT_GEN_TASKS.md`, не перескакивая.  
**Источник нарезки:** `PRODUCT_GEN_TASKS.md`.  
**Дата плана:** 2026-05-12.

---

## 1. Зафиксированные решения (из дискавери)

| Тема | Решение |
|------|---------|
| Доступ | Только `user.is_admin` |
| UI | Мастер по шагам; после «Создать» форма закрывается; прогресс в **карточке задачи**; фоновая генерация |
| Категория WB | Не обязательна |
| Characteristics в upload | Пока не отправляем; при ошибке WB — **человекочитаемое** сообщение + статус + повтор публикации |
| Размеры | Таблица: `techSize` + `wbSize` на строку |
| Цена | Одна на все размеры |
| Publish UX | Ошибка → статус + retry; успех → сохранить идентификаторы WB если есть |
| Лимиты генераций | Нет в MVP (риск затрат — в README фичи) |
| Фото в UI | Реальные превью + lightbox; браузер **не** ходит на порт микросервиса напрямую |
| Файлы | Volume у **микросервиса** для сгенерированных изображений; референсы пользователя — **монолит** (PG-2.2) |
| Очереди / state image-цепочки | В **`wb_image_pipeline_service`** |
| Связь сервисов | Цель: **mTLS**; до внедрения — private network + HMAC (см. `wb_image_pipeline_service/docs/mtls.md`) |
| LLM (env) | `WIP_OPENAI_MODEL_STRUCTURE=gpt-4.1-mini`, `WIP_OPENAI_MODEL_PROMPT_PACK=gpt-4.1`, image model в `WIP_OPENAI_IMAGE_MODEL` |
| TTL медиа | 14 дней после успешной публикации |
| Скелет `wb_image_pipeline_service/` | **Оставить** как заготовку PG-3; не подключать к монолиту до PG-3.4 |

---

## 2. Scenario contract (PG-0.1)

**User action:** Админ на вкладке «ИИ» открывает мастер «Полная генерация товара», заполняет поля, на последнем шаге нажимает «Создать», закрывает форму.

**Expected visible result:** В списке появляется карточка задачи со статусом «В процессе»; позже статус меняется на готовность к проверке; пользователь открывает карточку, видит фото и SEO, выбирает главное/серию, правит текст и цену, жмёт «Опубликовать» или «Повторить» при ошибке.

**System steps:**

1. Монолит сохраняет черновик (формы + пути референсов). **Категория WB** (`wb_subject_id`, ID предмета) **опциональна**: черновик и старт пайплайна допускаются без неё; при необходимости значение можно задать или изменить позже (`PATCH`).
2. Монолит создаёт **run** в `wb_image_pipeline_service`, передаёт ссылки/ID входных ассетов и текстовые поля.
3. Сервис в очереди: структуризация (SEO + 4 промпта главного) → 4 изображения → **пауза** до команды монолита с выбранным главным.
4. Монолит фиксирует выбор пользователя → вызов сервиса «main selected».
5. Сервис: 8 промптов серии → 8 изображений; монолит синхронизирует статусы/метаданные (поллинг или webhook — выбрать в PG-3, минимум поллинг).
6. Пользователь отмечает галочками фото для публикации, редактирует SEO и цену.
7. Монолит: `barcodes` (count = число строк размеров) → `cards/upload` с артикулом, названием, брендом, описанием, габаритами, sizes+barcodes, **без** characteristics на первом этапе.

**Frontend calls (черновик):** `POST/GET/PATCH` черновика; `POST` старт run; `POST` выбор главного фото; `GET` прокси изображения; `POST` publish.

**Backend endpoints (монолит):** префикс например `/ai/product-generation` (все за `require_admin`), не смешивать бизнес-логику в роуте — только `app/services/`.

**Background:** Celery **в микросервисе** для цепочки картинок; монолит может использовать Celery только для «обёрток» (опционально), но state machine изображений — в сервисе.

**DB/state (монолит):** таблица черновика/задачи: статус, `pipeline_run_id`, поля формы, SEO (редактируемый), цена, выбранные asset ids, ошибка WB (текст), ответ WB (JSON/text).

**DB/state (сервис):** runs, steps, assets, промпты (лог).

**What must NOT happen:** Не-админ не видит UI и не получает 200 на API; потеря черновика при обновлении страницы; открытый порт image-сервиса в интернет без mTLS/HMAC; публикация без явного нажатия «Опубликовать».

**Happy-path verification:** Создать → дождаться статуса → выбрать фото → опубликовать → в БД сохранён успех/WB id.

**Error/retry verification:** Сломать WB mock → человеческая ошибка → retry → успех или снова понятная ошибка.

---

## 3. Flow trace (PG-0.1)

```
Мастер «Создать»
  → POST /ai/product-generation/jobs (монолит) → сервис product_generation_service → ORM
  → POST wb_image_pipeline /internal/v1/runs (монолит как клиент)
  → Celery (сервис): structure LLM → 4× image → (user) PATCH main_asset_id
  → POST /internal/v1/runs/{id}/main (или аналог) → 8 prompts → 8× image
  → GET поллинг статуса / деталка job (монолит агрегирует для UI)
  → GET /ai/product-generation/jobs/{id}/assets/{asset_id}/file (прокси, admin)
  → POST publish → httpx WB barcodes + cards/upload → обновление job
```

Уточнение для Builder: точные пути эндпоинтов сервиса зафиксировать в OpenAPI/README сервиса в PG-3.3.

---

## 4. UX/UI Contract (PG-0.2)

- **Entry point:** Вкладка «ИИ», кнопка только при `is_admin`.
- **Pattern:** Мастер по шагам (modal или full-screen — как у существующих модалок `AiModule.jsx`).
- **Шаги (логика):** (1) Референсы + текст + габариты + цена + артикул + название + бренд + **опционально** ID предмета WB (`wb_subject_id`) + таблица размеров; (2) подтверждение; (3) **Создать** → закрытие.
- **После создания:** Отдельная **карточка задачи** в списке (новый тип или секция «Генерация товара»), статус бейдж «В процессе» / «Ошибка» / «Готово к публикации» / «Опубликовано».
- **Карточка деталки:** Галерея с **lightbox**; SEO textarea; цена; чекбоксы по фото; кнопки «Опубликовать», «Повторить публикацию».
- **Состояния:** loading при создании/публикации; error с текстом; success с сохранением данных.
- **Close:** После «Создать» мастер закрывается без блокировки фона на долгие запросы.
- **A11y:** focus trap в модалке, ESC закрывает только мастер (не карточку задачи без подтверждения если есть несохранённое — на MVP можно без драфта в мастере после submit).

**UI Scenario Proof (чеклист для QA после реализации):** happy create → poll → select main → series visible → publish; error path WB → message → retry.

---

## 5. Данные монолита (черновик PG-1.2)

Минимальные поля (расширяем JSON при необходимости):

- `id`, `user_id`, `status` (enum строкой с CheckConstraint)
- `pipeline_run_id` (UUID string, nullable)
- `vendor_code`, `title`, `brand`, `wb_subject_id` (integer, nullable — ID предмета WB; **не обязателен** для черновика и старта генерации, PG-2.4), `description_user` (исходный текст)
- `seo_description` (nullable до LLM)
- `price` (integer, копейки или рубли — **один раз выбрать и зафиксировать в схеме**, согласовать с WB API)
- `dimensions_length`, `dimensions_width`, `dimensions_height`, `weight_brutto` (numeric, nullable где уместно)
- `sizes_json` (JSONB: `[{tech_size, wb_size}]`)
- `reference_asset_ids` или `reference_paths_json` (после PG-2.2)
- `selected_main_asset_id`, `selected_series_asset_ids` (JSONB array)
- `wb_publish_error` (Text nullable), `wb_response_json` (JSONB nullable)
- `created_at`, `updated_at`

Индексы: `user_id`, `status`, `created_at`.

---

## 6. API монолита (черновик PG-1.3+)

Все за `Depends(require_admin_user)` (паттерн как в `offer_ai` / `require_admin`).

| Метод | Назначение |
|-------|------------|
| `POST /ai/product-generation/jobs` | Создать черновик (пустой или из формы) |
| `GET /ai/product-generation/jobs` | Список текущего админа |
| `GET /ai/product-generation/jobs/{id}` | Деталка |
| `PATCH /ai/product-generation/jobs/{id}` | Обновление полей (SEO, цена, выборы — по мере фаз) |
| `POST /ai/product-generation/jobs/{id}/references` | Multipart: референсы на диск монолита + `reference_paths_json` (PG-2.2) |
| `GET /ai/product-generation/jobs/{id}/references/{asset_id}/file` | Скачать/превью референса (admin) |
| `POST /ai/product-generation/jobs/{id}/start` | Старт pipeline (PG-2.3 / PG-3.4) |

Прокси **сгенерированных** файлов из image-сервиса и publish — отдельные маршруты в PG-4 / PG-5.

---

## 7. Микросервис (PG-3)

- Расширить скелет: модели SQLAlchemy + Alembic **внутри папки сервиса** (отдельный `alembic.ini` или скрипт migrate).
- **PG-3.1 (факт):** таблицы `wip_runs`, `wip_steps`, `wip_assets` (поле связи с монолитом — `wip_runs.monolith_job_id`); миграция `a1b2c3d4e501`; старт контейнера — `alembic upgrade head`; dev SQLite на общем volume `wip_data:/data` вместе с `WIP_MEDIA_ROOT`.
- **PG-3.2 (факт):** Celery `chain(run_created → step_done)` — заглушка: run `created`→`running`→`completed`, один шаг `pg32_stub` `pending`→`running`→`done`; брокер `WIP_REDIS_URL` (в примере compose — `wip_redis`); идемпотентность под ретраи (`SQLAlchemyError` autoretry).
- Redis: отдельный DB index от монолита (`/1` vs `/0`).
- Не публиковать порт 9100 наружу в прод compose без proxy.

---

## 8. Порядок работ для Builder (коммиты)

1. **PG-0:** этот файл уже закрывает 0.1–0.2; 0.3 — зафиксировано в §1. Обновить `PRODUCT_GEN_TASKS.md` чекбоксы.
2. **PG-1.1–1.3:** модель, миграция, роутер + сервис + pytest (403 для не-админа, CRUD).
3. **PG-1.4:** UI кнопка + заглушка/пустой список jobs.
4. Далее строго по таблице фаз 2→6.

---

## 9. Верификация (гейты)

После каждой фазы: `ruff check .`, `mypy .`, `pytest`.  
Если затронут `frontend/src/`: `npm run lint`, `npm run build`, закоммитить `frontend/dist` при политике репо.

**Замечание CI:** каталог `wb_image_pipeline_service/` в корне — при появлении проблем в `ruff`/`mypy` с корня: либо `exclude` в конфиге репо, либо привести сервис к тем же гейтам (решение принимает builder при первом красном прогоне).

---

## 10. Риски

| Риск | Митигация |
|------|-----------|
| WB отклоняет карточку без characteristics | Человеческая ошибка + итерация PG-5 (добавить минимальный набор по subject) |
| Расхождение цены (руб/коп) | Зафиксировать в схеме и в клиенте WB одну единицу |
| Двойной Celery в двух кодовых базах | Чёткий контракт: воркеры изображений только в сервисе |
| Стоимость OpenAI без лимитов | Документировать; позже PG-6.2 / флаг |

---

## 11. Handoff → Builder

**Старт:** `PRODUCT_GEN_TASKS.md` фаза 1, строки PG-1.1.  
**Не делать до PG-1:** реальные вызовы OpenAI, WB, интеграция run.  
**Первый PR:** миграция + CRUD + тесты + `include_router` в `app/main.py`.

---

*Конец плана планировщика.*
