# План: полная генерация товара (ИИ) — для Builder

**Роль:** planner → **следующий шаг:** builder выполняет фазы по `PRODUCT_GEN_TASKS.md` (**v2:** сначала **A → B** — каскад изображений без обязательной карточки; товар/WB — **D**), не перескакивая.  
**Источник нарезки:** `PRODUCT_GEN_TASKS.md` (версия **v2**, 2026-05-13).  
**Дата плана:** 2026-05-12.

> **Актуализация (2026-05-13):** UX и бэклог разделены на поток **IMAGE** и **PRODUCT/WB** — см. `PRODUCT_GEN_TASKS.md` v2. **§2–§4** ниже приведены в соответствие (PG-A.0).

---

## 1. Зафиксированные решения (из дискавери)

| Тема | Решение |
|------|---------|
| Доступ | Только `user.is_admin` |
| UI | Мастер по шагам: **сначала** референсы + запуск каскада фото (без обязательной полной карточки); **потом** (по желанию/после готовности) форма «Создать товар» с размерами/ценой/артикулом; прогресс в **карточке задачи**; фоновая генерация |
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
| Скелет `wb_image_pipeline_service/` | Связка с монолитом **PG-3.4** — факт; дальше **фаза B** (реальный каскад вместо stub) |

---

## 2. Scenario contract (PG-A.0) — два потока

### 2.1 Поток **IMAGE** (каскад фото)

**User action:** Админ открывает мастер «Полная генерация товара», загружает ≥1 референс, при необходимости вводит пользовательский текст (описание для промпта), нажимает **«Запустить генерацию фото»** (или эквивалент старта). Поля карточки WB (артикул, размеры, цена, габариты, название, бренд) **могут быть пустыми**.

**Expected visible result:** В списке задача в статусе «В процессе»; позже — превью/галерея сгенерированных кадров (когда будут **PG-C.1** и реальный каскад **B**); пользователь может **сохранить фото и закрыть** без создания товара.

**System steps:**

1. `POST /ai/product-generation/jobs` — черновик (часто только `description_user` и пустые поля карточки).
2. `POST .../references` — файлы на диск монолита, в JSON появляются `asset_id`.
3. `POST .../start` — монолит проверяет только `draft` + ≥1 референс; **не** требует заполненной карточки.
4. Монолит вызывает `POST /internal/v1/runs` с `payload`, где обязательны **`reference_asset_ids`**; остальные поля карточки опциональны/`null` (PG-A.2).
5. Сервис: очередь — структуризация → 4 изображения → пауза → (после команды) серия 8 — по мере реализации **фазы B** в `wb_image_pipeline_service`.

**Frontend calls:** `POST/GET/PATCH` job; `POST` references; `POST` start; далее поллинг списка/деталки; `GET` прокси сгенерированных файлов (когда появится **PG-C.1**).

**What must NOT happen:** Старт IMAGE блокируется только отсутствием референсов или неверным статусом, а не пустым `vendor_code`/`sizes`; не-админ не получает 200.

**Happy-path verification (IMAGE):** Создать черновик с пустыми полями карточки → upload ref → start → `pipeline_run_id` (UUID удалённого сервиса или `local-*`) → статус `in_progress`.

**Error/retry verification:** Нет референсов → 400; image-сервис недоступен → 503, job остаётся `draft`.

### 2.2 Поток **PRODUCT / WB** (карточка и публикация)

**User action:** После готовности фото (или когда админ готов) — **«Создать товар»** / дозаполнение формы: размеры, цена, артикул, название, бренд, габариты и т.д.; затем выбор главного/серии, SEO, **«Опубликовать»**.

**Expected visible result:** Данные карточки сохранены в job (`PATCH`); при публикации — `barcodes` + `cards/upload` без characteristics на первом шаге; ошибки WB — человекочитаемо + retry.

**System steps:** как в прежнем плане PG-5: PATCH полей → выбор ассетов → publish через монолит.

**What must NOT happen:** Публикация без явного действия; потеря черновика при обновлении страницы.

**Happy-path / error:** как в §2.1 дополнение — полный путь «фото готовы → карточка → publish».

---

## 3. Flow trace (PG-A.0)

### 3.1 IMAGE

```
POST /ai/product-generation/jobs (опционально только description_user)
  → POST .../references (multipart)
  → POST .../start
  → POST wb_image_pipeline /internal/v1/runs  { monolith_job_id, payload: { reference_asset_ids, … } }
  → Celery (сервис): structure LLM → 4× image → пауза → (main selected) → 8 prompts → 8× image
  → GET поллинг статуса job (монолит, поле image_pipeline при удалённом run)
  → GET /ai/product-generation/jobs/{id}/generated-assets/... (прокси, PG-C.1 — когда будет)
```

### 3.2 PRODUCT / WB

```
PATCH /ai/product-generation/jobs/{id}  (полная карточка)
  → UI: выбор main / series, SEO, цена
  → POST publish → httpx WB barcodes + cards/upload
```

Уточнение для Builder: точные пути эндпоинтов сервиса (в т.ч. `main selected`) зафиксировать в OpenAPI/README сервиса при реализации **PG-B.4**.

---

## 4. UX/UI Contract (PG-A.0)

- **Entry point:** Вкладка «ИИ», кнопка только при `is_admin`.
- **Pattern:** Мастер по шагам (`AiModule.jsx`): этап **prepare** — референсы + пользовательский текст + кнопка запуска генерации фото; **afterPhotos** — статус/скачивание/закрыть; **createProduct** — прежняя форма карточки (размеры, цена, …) только когда пользователь идёт к товару.
- **Старт IMAGE:** Не требовать заполнения таблицы размеров, цены, артикула; валидация карточки — на этапе отправки формы «Создать товар» / PATCH (фаза **D**).
- **После старта:** Карточка задачи в списке, бейдж статуса; деталка — галерея + lightbox (после прокси), SEO, цена, чекбоксы, «Опубликовать» / retry.
- **Состояния:** loading при upload/start; error с текстом; success.
- **Close:** Можно закрыть мастер после фото без перехода к форме товара.
- **A11y:** focus trap в модалке; ESC закрывает мастер.

**UI Scenario Proof:** happy: минимальный ввод → start → in_progress; error: start без референсов; (позже) publish error → retry.

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
- **PG-3.3 (факт):** HTTP `POST /internal/v1/runs` (создание run + постановка PG-3.2 chain), `GET /internal/v1/runs/{id}` (статус, `payload`, шаги, ассеты); доступ по `Authorization: Bearer <WIP_INTERNAL_HMAC_SECRET>`; контракт в README сервиса и OpenAPI `/docs`.
- **PG-3.4 (факт):** монолит при `POST /ai/product-generation/jobs/{id}/start` и заданных `PRODUCT_GEN_IMAGE_PIPELINE_BASE_URL` + `PRODUCT_GEN_IMAGE_PIPELINE_SECRET` вызывает image-сервис `POST /internal/v1/runs`, сохраняет UUID в `pipeline_run_id`; ответы `GET` списка/деталки и `POST .../start` обогащаются снимком `GET /internal/v1/runs/{id}` (поле `image_pipeline`); UI поллит список раз в 4 с для строк с удалённым run; без пары env — прежний `local-*` + Celery-заглушка монолита.
- Redis: отдельный DB index от монолита (`/1` vs `/0`).
- Не публиковать порт 9100 наружу в прод compose без proxy.

---

## 8. Порядок работ для Builder (коммиты)

1. **PG-0 / PG-A.0:** scenario + flow + UX в этом файле (§2–4) синхронизированы с `PRODUCT_GEN_TASKS.md` v2.
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
