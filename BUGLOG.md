# BUGLOG

Единый журнал багов/регрессий: бизнесовое, процессное и техническое описание + root cause и профилактика.

## Правила ведения
- **Новые записи добавлять сверху** (самая свежая — первой).
- **Нумерация**: `BUG-<N>` где \(N\) = последний номер + 1.
- **Дата**: YYYY-MM-DD.
- **Автоматизация**: указывать `да/нет` и чем выявлено/что автоматизировали (тест, алерт, CI, ручной репорт).

---
ID: BUG-44
Дата: 2026-05-14
Статус: fixed
Автоматизация: да (`wb_image_pipeline_service/tests/test_structure_main_openai_env.py`, `wb_image_pipeline_service/tests/test_image_generation_prompts.py`)

## Бизнес-описание
Первые 4 фото полной генерации товара должны помогать пользователю выбрать визуальный стиль будущей карточки, но фактически получались почти одинаковые: белый/нейтральный фон, похожая модельная подача, обрезанные крупности и мало различий по локации/образу. Пользователь не мог выбрать направление фотосессии, потому что варианты выглядели как технические дубли.

## Процесс / сценарий
1) Пользователь загружает референс товара и задаёт текстовое пожелание.
2) Система должна сгенерировать 4 разных главных варианта одного товара: premium studio, old money / quiet luxury, casual lifestyle, bold editorial или адаптированные предметные аналоги.
3) Ожидаемо: максимум один белый/студийный вариант, остальные — разные lifestyle/editorial сцены с отличающимся styling.
4) Фактически: prompt-planner оставался слишком осторожным и тянул все варианты к светлому минималистичному фону и похожей крупности.

## Техническое описание
`structure_main_openai._STRUCTURE_SYSTEM` требовал разные ракурсы/крупности, но не фиксировал, что 4 `main_prompts` — это именно 4 разных визуальных направления будущей фотосессии. `build_main_image_prompt` также слишком общо разрешал менять фон/стиль и не подталкивал image model уходить из белого фона, если planner просит lifestyle/editorial.

## Root cause (почему произошло)
- Недоучтён кейс: для первого этапа нужна не просто вариативность ракурсов, а выбор визуального мира карточки.
- Не было инварианта “белый/студийный фон максимум в одном варианте”.
- Не было теста, который фиксирует обязательные style slots и разрешение styling-окружения при сохранении товара.

## Исправление (что сделали)
`structure_main_openai._STRUCTURE_SYSTEM` переписан под 4 style slots: premium e-commerce, old money / quiet luxury, casual weekend, bold fashion / editorial. Явно закреплено: товар одинаковый, но модель/образ/фон/локация/настроение/styling должны отличаться; белый/нейтральный фон максимум в одном варианте. `build_main_image_prompt` теперь разрешает менять нижнюю одежду, обувь, аксессуары, локацию и styling, если сам товар сохраняется точным.

## Профилактика (как не повторить)
Добавлены тесты, которые проверяют наличие style slots, лимит белого фона и разрешение styling в main image wrapper при строгом сохранении товара.

## Проверка
- Команды: `python3 -m pytest -q tests/test_structure_main_openai_env.py tests/test_image_generation_prompts.py tests/test_pipeline_images_step.py` в `wb_image_pipeline_service`, `ruff check .`, `mypy .`, `pytest -q`, `python3 -m pytest -q` в `wb_image_pipeline_service`.
- Сценарии: prompt-planner contract для 4 main теперь требует разные visual directions; image wrapper для 4 main не запрещает styling/аксессуары/локации вокруг товара.

Затронутые файлы: `wb_image_pipeline_service/app/services/structure_main_openai.py`, `wb_image_pipeline_service/app/services/image_generation_prompts.py`, `wb_image_pipeline_service/tests/test_structure_main_openai_env.py`, `wb_image_pipeline_service/tests/test_image_generation_prompts.py`, `BUGLOG.md`, `TASKLOG.md`

---
ID: BUG-43
Дата: 2026-05-14
Статус: fixed
Автоматизация: да (`backend/tests/test_product_generation_api.py`, `wb_image_pipeline_service/tests/test_internal_runs_http.py`)

## Бизнес-описание
При локальной генерации товара упавшие или зависшие черновики могли оставаться в состоянии, где старые queued шаги WIP теоретически продолжили бы генерацию после повторного старта worker. Это создавало риск внезапного расхода OpenAI-токенов на пачку черновиков без явного действия пользователя.

## Процесс / сценарий
1) Пользователь запускает генерацию 4 первых фото по референсам.
2) Если пайплайн упал или завис, система должна остановить старый run/steps и больше не продолжать их автоматически.
3) Пользователь должен видеть аккуратную кнопку повторного запуска: для первого этапа — «Повторить генерацию фото», для второго этапа — «Сгенерировать контент снова».
4) Готовые задачи «К публикации» должны остаться как есть.
5) Факт: retry/stop-контракт был неявным; старые run/steps не имели HTTP stop-контракта, а UI не давал отдельного безопасного действия остановки/повтора.

## Техническое описание
Монолитный PG API имел только `/start` и `/generate-content`. `/start` был разрешён только из `draft`, а WIP не имел internal stop endpoint. При ручных/аварийных состояниях `in_progress/error` не было гарантированного действия, которое помечает старый WIP run/steps так, чтобы queued tasks не дошли до токен-затратных шагов.

## Root cause (почему произошло)
- Не был зафиксирован инвариант: старый failed/stuck WIP run не должен продолжаться без клика пользователя.
- UI показывал ошибку, но не давал явного безопасного retry/stop действия.
- WIP internal API не имел операции stop для pending/running steps.

## Исправление (что сделали)
Добавлен WIP endpoint `POST /internal/v1/runs/{run_id}/stop`: без main-frame ассетов он переводит run в `cancelled`, pending/running steps — в `failed`; если main-frame уже есть, run остаётся retryable для content flow через `failed`, а pending/running content steps переводятся в `failed`. Монолит получил `POST /ai/product-generation/jobs/{job_id}/stop` и перед повторным `/start` из `error` сначала останавливает старый remote run, затем создаёт новый. UI получил кнопки `Остановить`, `Повторить генерацию фото`, `Сгенерировать контент снова` без перегруза карточки.

## Профилактика (как не повторить)
Регрессионные тесты проверяют stop-контракт WIP, остановку монолитной PG-задачи и повторный старт из `error` через новый remote run с предварительным stop старого run.

## Проверка
- Команды: `ruff check .`, `mypy .`, `pytest`, `python3 -m pytest -q` в `wb_image_pipeline_service`, `npm run lint`, `npm run build`.
- Сценарии: active PG stop -> job `error` + WIP `/stop`; retry first photos from `error` -> old run stopped + new run created; content retry still starts from saved main-frame selection; WIP worker не запускался.

Затронутые файлы: `backend/app/routers/product_generation.py`, `backend/app/services/product_generation_service.py`, `backend/app/services/product_generation_image_pipeline.py`, `backend/tests/test_product_generation_api.py`, `wb_image_pipeline_service/app/api/internal_runs.py`, `wb_image_pipeline_service/app/services/internal_runs_service.py`, `wb_image_pipeline_service/tests/test_internal_runs_http.py`, `frontend/src/api.js`, `frontend/src/screens/AiModule.jsx`, `frontend/dist/*`, `BUGLOG.md`, `TASKLOG.md`

---
ID: BUG-42
Дата: 2026-05-14
Статус: fixed
Автоматизация: да (`backend/tests/test_ai_daily_analytics_beat.py`, `backend/tests/test_ai_module_api.py`)

## Бизнес-описание
AI-модуль должен ежедневно обновлять задачи и гипотезы по данным WB «Сравнение карточек», но после первого успешного запуска система не поддерживала полный цикл: не пыталась перечитать отчёт через живую сессию, не создавала понятную задачу при истечении 3-дневного доступа к отчёту и не отделяла платное переоткрытие отчёта от протухшей WB-сессии.

## Процесс / сценарий
1) Пользователь один раз выдаёт доступ к WB через отдельную браузерную сессию и SMS-код.
2) Каждый день система должна пробовать использовать сохранённый `storage_state`, чтобы считать отчёт и создать задачи/гипотезы.
3) Если WB-сессия протухла, пользователь должен увидеть задачу «Дать доступ к кабинету WB».
4) Если сам отчёт сравнения истёк или WB показывает платную плашку переоткрытия, пользователь должен увидеть задачу «Обновить отчёт сравнения с конкурентами».
5) Факт: daily beat только запускал аналитику по уже готовому актуальному отчёту и просто пропускал stale-отчёты; paid-prompt не распознавался как отдельный пользовательский сценарий.

## Техническое описание
`run_ai_daily_analytics_beat_cycle` проверял только `status=ready` и `valid_until>=today`, затем вызывал `run_daily_analytics`. Playwright-fetch существовал только за ручной задачей `competitor_report_refresh`, а `PlaywrightBlockedError`/общие ошибки не разделяли сценарии auth failure и WB paid reopen prompt.

## Root cause (почему произошло)
- Daily beat был реализован как повторный расчёт аналитики по сохранённому отчёту, а не как оркестратор ежедневного WB-считывания.
- Контракт истечения отчёта WB (3 дня) не был превращён в задачу пользователю.
- Не было отдельного сигнала `paid_reopen_required`, поэтому система не могла безопасно остановиться до платного действия.
- Не хватало регрессионных тестов на queue/background сценарии: TTL expired, paid prompt, auth failure и daily fetch.

## Исправление (что сделали)
Добавлен idempotent helper задач пользователя (`wb_access_grant`, `competitor_report_refresh`). Daily beat теперь проверяет доступ WB, создаёт задачу перезахода при невалидном `storage_state`, создаёт задачу продления при expired TTL, а для готового Playwright-отчёта запускает headless fetch (daily keepalive + импорт + аналитика). Playwright получил конфигурируемый детектор paid-prompt через `WB_COMPETITOR_PAID_REOPEN_SELECTOR` / `WB_COMPETITOR_PAID_REOPEN_TEXTS` и отдельную ошибку `paid_reopen_required`, без клика по платному действию.

## Профилактика (как не повторить)
Добавлены регрессионные тесты на daily orchestration: fetch без отчёта, fetch готового Playwright-отчёта, expired TTL, paid prompt, auth failure и worker-обработку paid prompt. Для будущих WB DOM-изменений paid-prompt остаётся env-конфигурируемым.

## Проверка
- Команды: `pytest backend/tests/test_ai_daily_analytics_beat.py backend/tests/test_ai_module_api.py::test_competitor_report_paid_prompt_creates_refresh_task backend/tests/test_ai_module_api.py::test_competitor_report_playwright_failure_sets_reconnect_flag`, `ruff check .`, `mypy .`, `pytest`.
- Сценарии: happy path daily fetch; error path auth failure -> `wb_access_grant`; error path paid/TTL -> `competitor_report_refresh`; без автооплаты WB.

Затронутые файлы: `backend/app/services/ai_daily_analytics_beat_service.py`, `backend/app/services/ai_task_ensurer.py`, `backend/app/services/ai_competitor_playwright.py`, `backend/celery_app/tasks.py`, `backend/app/routers/ai_module.py`, `backend/tests/test_ai_daily_analytics_beat.py`, `backend/tests/test_ai_module_api.py`, `backend/.env.example`, `docker-compose.yml`, `BUGLOG.md`, `TASKLOG.md`

---
ID: BUG-41
Дата: 2026-05-13
Статус: fixed
Автоматизация: нет (выявлено ручным UI-репортом; проверено `npm run lint`, `npm run build`, `ruff check .`, `mypy .`, `pytest`)

## Бизнес-описание
При запуске генерации контента пользователь оставался в модалке задачи, но окно визуально увеличивалось и вылезало за экран. На фоне список задач каждые несколько секунд дёргался из-за автопроверки статусов, что создавало ощущение нестабильного интерфейса во время ожидания результата.

## Процесс / сценарий
1) Пользователь открывает задачу полной генерации товара.
2) Выбирает одно из 4 фото и нажимает «Сгенерировать контент».
3) Ожидание: модалка остаётся стабильного размера внутри экрана, прокручивается только её содержимое; фон не мигает и не дёргается.
4) Факт: содержимое после смены состояния могло выталкивать модалку за viewport, а фоновой `setInterval` списка вызывал видимые перерисовки/мигание статусов.

## Техническое описание
`ModalShell` не ограничивал высоту контейнера и не разделял header/body/footer на фиксированные и скроллируемую зоны. `ProductGenerationAdminCard` запускал polling списка задач каждые 4 секунды даже поверх открытой модалки и включал видимый `loading` на каждом автообновлении.

## Root cause (почему произошло)
- UI-контейнер модалки был рассчитан на короткие формы и не имел инварианта `max-height + inner scroll`.
- Автопроверка статусов была реализована как обычная ручная загрузка списка, поэтому меняла визуальное состояние карточек/toolbar.
- Не было отдельной проверки сценария ожидания генерации контента с открытой модалкой.

## Исправление (что сделали)
`ModalShell` стал flex-контейнером с `maxHeight: calc(100vh - 32px)`: header/footer не сжимаются, body скроллится внутри окна. Автопроверка списка задач теперь паузится, пока открыта модалка задачи или лог, а interval-загрузка работает в silent-режиме без видимого `loading`.

## Профилактика (как не повторить)
Для PG-модалок фиксировать инвариант: рост контента не меняет размер окна за пределы viewport, фоновые poll-запросы не должны вызывать видимые layout-сдвиги поверх активного диалога.

## Проверка
- Команды: `npm run lint`, `npm run build`, `ruff check .`, `mypy .`, `pytest`.
- Сценарии: UI flow «выбрать 1 из 4 фото → нажать “Сгенерировать контент” → ждать генерацию в открытой модалке»; ожидаемый результат — окно остаётся в пределах viewport, фоновой список не дёргается.

Затронутые файлы: `frontend/src/screens/AiModule.jsx`, `frontend/dist/index.html`, `frontend/dist/assets/*`, `BUGLOG.md`, `TASKLOG.md`

---
ID: BUG-40
Дата: 2026-05-13
Статус: fixed
Автоматизация: да (`backend/tests/test_product_generation_api.py`, `wb_image_pipeline_service/tests/test_images_main_openai.py`, `test_pipeline_images_step.py`, `test_reference_fetch_client.py`, `test_pg32_celery_chain.py`, `test_internal_runs_http.py`)

## Бизнес-описание
Пользователь загружал референс товара и описание, но сгенерированное фото могло быть вообще не про исходный предмет: например вместо женского поло появлялись тапки. Это ломало ключевую ценность сценария — получить 4 варианта главного фото именно того товара, который продавец приложил.

## Процесс / сценарий
1) Админ создаёт задачу полной генерации товара.
2) Загружает reference image и пишет, что это за вещь.
3) Ожидание: система генерирует 4 разных главных фото WB-карточки строго по предмету с референса, меняя только подачу, модель, локацию, свет и стиль.
4) Факт: WIP отдавал в OpenAI только текстовые prompts, а файл референса не передавался в image API.

## Техническое описание
`wb_image_pipeline_service` хранил `reference_asset_ids` в payload, но `images_main_openai` вызывал `/v1/images/generations` с JSON `{prompt, n=1}` без `image[]`. Поэтому reference ids были только метаданными run, а не входом модели. Промпты также не закрепляли строгое правило same-product.

## Root cause (почему произошло)
- Контракт PG-B.3 был принят как “4 картинки есть”, но не проверял “OpenAI получил reference image”.
- В тестах не было инварианта: при наличии `reference_asset_ids` запрещено генерировать без reference file.
- Backlog считал B.3 закрытой, хотя это была только текстовая генерация.

## Исправление (что сделали)
WIP теперь получает reference-файлы из монолита по защищённому internal endpoint, вызывает OpenAI image edit/reference flow через `/v1/images/edits` с `image[]`, а для каждого `main_frame` сохраняет prompt и reference metadata в `wip_assets.meta_json`. Стандартный prompt и structure prompt усилены правилом “тот же товар с референса”.

## Профилактика (как не повторить)
Добавлены регрессионные тесты на internal reference fetch, запрет image generation без reference image, multipart `image[]` в OpenAI request, failed-сценарий без reference file и сохранение prompt/reference metadata на ассетах.

## Проверка
- Команды: `ruff check .`, `mypy .`, `pytest`, `python3 -m pytest -q` в `wb_image_pipeline_service/`.
- Сценарии: happy path WIP создаёт 4 `main_frame` через mocked reference images; error path без `monolith_job_id`/reference file переводит `images_main` и run в `failed`; backend internal endpoint требует Bearer token.

Затронутые файлы: `backend/app/routers/product_generation.py`, `backend/app/services/product_generation_service.py`, `backend/app/services/product_generation_image_pipeline.py`, `backend/tests/test_product_generation_api.py`, `backend/.env.example`, `docker-compose.yml`, `wb_image_pipeline_service/app/services/reference_fetch_client.py`, `wb_image_pipeline_service/app/services/images_main_openai.py`, `wb_image_pipeline_service/app/services/pipeline_images_step.py`, `wb_image_pipeline_service/app/services/image_run_prompt.py`, `wb_image_pipeline_service/app/services/structure_main_openai.py`, `wb_image_pipeline_service/app/config.py`, `wb_image_pipeline_service/tests/*`, `wb_image_pipeline_service/README.md`, `wb_image_pipeline_service/.env.example`, `Требоваия/ИИ модуль/PRODUCT_GEN_TASKS.md`, `Требоваия/ИИ модуль/PRODUCT_GEN_PLAN.md`

---
ID: BUG-39
Дата: 2026-05-13
Статус: fixed
Автоматизация: да (`backend/tests/test_product_generation_api.py`, `wb_image_pipeline_service/tests/test_internal_runs_http.py`)

## Бизнес-описание
После успешной генерации фото пользователь видел статус **«К публикации»**, но кнопка **«Скачать фото»** скачивала исходные референсы или не давала доступ к новым картинкам. Сами сгенерированные файлы были спрятаны внутри volume WIP.

## Процесс / сценарий
1) Пользователь запускает полную генерацию товара.
2) WIP создаёт 4 `main_frame` ассета в `/data/media/<run_id>/`.
3) Ожидание: кнопка «Скачать фото» скачивает эти 4 сгенерированные картинки.
4) Факт: монолит не имел endpoint для generated assets, UI работал с `reference_paths_json`.

## Техническое описание
`wb_image_pipeline_service` отдавал assets только в JSON `GET /internal/v1/runs/{id}`, но не отдавал файл ассета. Монолит не проксировал WIP media, а фронт вызывал download референса.

## Root cause (почему произошло)
- Не был реализован контур доставки WIP-generated files до пользователя.
- Название кнопки «Скачать фото» подразумевало generated фото, а код скачивал uploaded references.

## Исправление (что сделали)
- WIP: `GET /internal/v1/runs/{run_id}/assets/{asset_id}/file`.
- Монолит: `GET /ai/product-generation/jobs/{job_id}/generated-assets/{asset_id}/file`.
- `image_pipeline.generated_assets` в ответе API.
- UI: «Скачать фото» скачивает `generated_assets`, а не `reference_paths_json`.

## Профилактика (как не повторить)
- API-тест проксирования generated asset.
- WIP-тест file endpoint для asset.

## Проверка
- Команды: `ruff check .`, `mypy .`, `pytest -q`; `python3 -m pytest tests -q` в `wb_image_pipeline_service`; `npm run lint`, `npm run build`.
- Сценарии: smoke задачи `05e0b11e...`: `generated_assets=4`; скачан `main_frame_0.png` через монолит, PNG 1024×1024, 1 752 985 байт.

Затронутые файлы: `wb_image_pipeline_service/app/api/internal_runs.py`, `wb_image_pipeline_service/tests/test_internal_runs_http.py`, `backend/app/services/product_generation_image_pipeline.py`, `backend/app/routers/product_generation.py`, `backend/tests/test_product_generation_api.py`, `frontend/src/api.js`, `frontend/src/screens/AiModule.jsx`, `frontend/dist/*`, `BUGLOG.md`, `TASKLOG.md`
---
ID: BUG-38
Дата: 2026-05-13
Статус: fixed
Автоматизация: да (`backend/tests/test_product_generation_api.py`)

## Бизнес-описание
После успешного завершения WIP image-run пользователь всё ещё видел задачу как **«В процессе»**, и кнопка **«Создать товар»** оставалась недоступной. Генерация фактически завершена, но пользователь не мог перейти дальше.

## Процесс / сценарий
1) Пользователь запускает полную генерацию товара с референсом и текстом.
2) WIP выполняет `structure_main`, `images_main`, `pg32_stub`.
3) Ожидание: при WIP `completed` задача в мастере становится **«К публикации»**, кнопка «Создать товар» доступна.
4) Факт: монолит продолжал отдавать `status=in_progress`; UI считал задачу незавершённой.

## Техническое описание
`enrich_job_out_with_image_pipeline` добавлял `image_pipeline.remote_status`, но не мапил статус удалённого run в поле `ProductGenerationJobOut.status`. UI ориентируется именно на `status` задачи (`ready_to_publish` / `published`), а не только на `image_pipeline.remote_status`.

## Root cause (почему произошло)
- Не был реализован контракт PG-B.6: WIP `completed/failed` → статус задачи монолита для пользовательского сценария.

## Исправление (что сделали)
- Для ответа API: если задача монолита `in_progress`, то WIP `completed` отдаётся как `ready_to_publish`, WIP `failed/error` — как `error`.
- Добавлены проверки в API-тесты списка задач.

## Профилактика (как не повторить)
- API-тесты фиксируют маппинг статусов WIP в статус задачи.

## Проверка
- Команды: `ruff check .`, `mypy .`, `pytest backend/tests/test_product_generation_api.py backend/tests/test_product_generation_image_pipeline.py -q`.
- Сценарии: локальный smoke задачи `05e0b11e...`: API вернул `status=ready_to_publish`, `remote=completed`, `last_error=None`, 3 шага `done`, `timeline_count=4`.

Затронутые файлы: `backend/app/services/product_generation_image_pipeline.py`, `backend/tests/test_product_generation_api.py`, `BUGLOG.md`, `TASKLOG.md`
---
ID: BUG-37
Дата: 2026-05-13
Статус: fixed
Автоматизация: да (`wb_image_pipeline_service/tests/test_wip_openai_httpx.py`)

## Бизнес-описание
В мастере полной генерации товара после запуска пайплайна отображалась ошибка вида **`[Errno 111] Connection refused`** при статусе image-run **failed**, хотя ключ OpenAI мог быть настроен.

## Процесс / сценарий
1) В `backend/.env` для обхода региона OpenAI заданы `HTTPS_PROXY` / `HTTP_PROXY` (часто на `host.docker.internal:7890`).
2) Прокси на хосте в момент запуска **не слушает** порт.
3) Ожидание: либо прямой выход к OpenAI без прокси, либо явная ошибка прокси.
4) Факт: воркер WIP подхватывал системный прокси через httpx (`trust_env=True` по умолчанию) и падал на **Connection refused**.

## Техническое описание
`wb_image_pipeline_service`: `structure_main_openai`, `images_main_openai` создавали `httpx.Client(timeout=...)` без `trust_env=False`, из-за чего использовались переменные окружения прокси из общего `backend/.env`.

## Root cause (почему произошло)
- Дефолт httpx `trust_env=True` + общий `.env` с «заготовленным» под VPN прокси, который не всегда поднят.

## Исправление (что сделали)
- Модуль `wip_openai_httpx.openai_httpx_client`: `trust_env=False`; опционально явный прокси через `WIP_HTTPS_PROXY` / `WIP_HTTP_PROXY`.
- Подключение в `structure_main_openai` и `images_main_openai`. Документация в `backend/.env.example`, `wb_image_pipeline_service/.env.example`, `КАК_ЗАПУСТИТЬ.md`.

## Профилактика (как не повторить)
- Тест на передачу `trust_env=False` и на явный `WIP_HTTPS_PROXY`.

## Проверка
- Команды: `ruff check .`, `mypy .`, `pytest` (корень), `python3 -m pytest` в `wb_image_pipeline_service/`.
- Сценарии: вызов OpenAI без прокси при «мёртвом» глобальном `HTTPS_PROXY` в env (не должен использоваться).

Затронутые файлы: `wb_image_pipeline_service/app/services/wip_openai_httpx.py`, `structure_main_openai.py`, `images_main_openai.py`, `tests/test_wip_openai_httpx.py`, `backend/.env.example`, `wb_image_pipeline_service/.env.example`, `КАК_ЗАПУСТИТЬ.md`, `BUGLOG.md`
---
ID: BUG-36
Дата: 2026-05-13
Статус: fixed
Автоматизация: нет (ручной репорт: 502 после `docker compose up`; логи `api`)

## Бизнес-описание
После перезапуска Docker весь фронт через Caddy получал **502 Bad Gateway** — приложение как «упало».

## Процесс / сценарий
1) `docker compose up -d --build`.
2) Ожидание: `https://localhost:8444` и API отвечают.
3) Факт: контейнер `api` в цикле **Restarting**, Caddy не достучался до upstream.

## Техническое описание
`docker_entrypoint_api.py` вызывает `alembic upgrade head`. Ревизия `9e1f2a3b4c5d` делала безусловный `CREATE TABLE product_generation_jobs`; в БД таблица уже существовала (дрейф/ручной прогон), а версия Alembic была ниже — миграция падала с `DuplicateTable`, процесс завершался до uvicorn.

## Root cause (почему произошло)
- Неидемпотентная миграция при уже существующей таблице; расхождение `alembic_version` и фактической схемы.

## Исправление (что сделали)
- В `9e1f2a3b4c5d_product_generation_jobs.py`: перед `create_table` — `inspect.has_table`; если таблица есть — выходим из `upgrade()` без ошибки.

## Профилактика (как не повторить)
- Для DDL-миграций на существующих окружениях предпочитать проверку `has_table` / `IF NOT EXISTS` там, где допустим дрейф.

## Проверка
- Команды: `docker compose run --rm api python -m alembic upgrade head`; `docker compose restart api`; `docker compose ps api` (healthy).
- Сценарии: старт API после upgrade на БД с уже существующей `product_generation_jobs`.

Затронутые файлы: `backend/alembic/versions/9e1f2a3b4c5d_product_generation_jobs.py`

---
ID: BUG-35
Дата: 2026-05-12
Статус: fixed
Автоматизация: да (`pytest` в `test_ai_module_api.py`)

## Бизнес-описание
При ошибке Playwright при выгрузке отчёта сравнения (например таймаут клика) файл `storage_state` оставался на диске, и задача «Дать доступ к кабинету WB» не появлялась и могла автозакрываться — пользователь не получал явный сигнал переподключить сессию.

## Процесс / сценарий
1) Сохранён валидный `storage_state`, headless-забор отчёта падает с ошибкой.
2) Ожидание: снова видна задача на переподключение / шаг 2 онбординга.
3) Факт (до фикса): только запись в журнале отчёта; задача `wb_access_grant` не создавалась из‑за логики «файл есть = доступ ок».

## Техническое описание
`GET /ai/tasks` использовал наличие файла `{user_id}.json` как единственный критерий; при успешном списке открытая `wb_access_grant` автоматически переводилась в `completed`.

## Root cause (почему произошло)
- Не учтён кейс «файл есть, сессия в кабинете уже не работает»; маркер отличался только от полного отсутствия файла.

## Исправление (что сделали)
- Файл-маркер `{user_id}.reconnect_required` рядом с `storage_state`; выставляется при ошибках fetch в режиме `storage_state` (и при `PlaywrightBlockedError` при включённом флаге фичи); снимается при успешном fetch, загрузке/сохранении нового `storage_state`.
- `wb_headless_access_effective`: задача и автозакрытие завязаны на «эффективный» доступ без маркера.
- API `GET /ai/wb-access/status`: поле `reconnect_required`; фронт считает доступ сохранённым только если нет `reconnect_required`.

## Профилактика (как не повторить)
- Три pytest: маркер после падения воркера, список задач с маркером, запрет завершить `wb_access_grant` пока маркер есть.

## Проверка
- Команды: `ruff check .`, `mypy .`, `pytest`, `npm run lint`, `npm run build`
- Сценарии: см. тесты выше

Затронутые файлы: `backend/app/services/ai_wb_access_service.py`, `backend/app/routers/ai_module.py`, `backend/app/services/ai_module_service.py`, `backend/app/wb_auth_manager.py`, `backend/celery_app/tasks.py`, `backend/tests/test_ai_module_api.py`, `frontend/src/api.js`, `frontend/src/screens/AiModule.jsx`, `frontend/dist/*`

---
ID: BUG-34
Дата: 2026-05-12
Статус: fixed
Автоматизация: нет (обнаружено при `docker compose exec api alembic upgrade head` на локальной БД)

## Бизнес-описание
После поднятия Docker API мог не стартовать из‑за падения миграции Alembic при уже существующей колонке `review_created_at` (рассинхрон: колонка добавлена DDL тестов/ручной правкой, а `alembic_version` ещё не на head).

## Процесс / сценарий
1) `alembic upgrade head` на контейнере `api`.
2) Ожидание: миграция проходит, API healthy.
3) Факт (до фикса): `DuplicateColumn: review_created_at already exists` → контейнер `api` в restart loop.

## Техническое описание
Ревизия `2d4f3a1c0b11` делала безусловный `ADD COLUMN`.

## Root cause (почему произошло)
- Локальная БД уже имела колонку (например из `ALTER TABLE ... IF NOT EXISTS` в тестовом DDL), а миграция не была идемпотентной.

## Исправление (что сделали)
В `upgrade`/`downgrade` ревизии `2d4f3a1c0b11` добавлена проверка через `inspect`: колонку добавляем/удаляем только если её ещё нет/есть.

## Профилактика (как не повторить)
- Для additive-колонок в окружениях с возможным дрейфом схемы — идемпотентный `ADD COLUMN` (как для `ai_review_replies` table migration).

## Проверка
- Команды: `docker compose exec api alembic upgrade head`, `docker compose exec api alembic current`, `curl http://localhost:8000/health`
- Сценарии: API `healthy`, revision `2d4f3a1c0b11 (head)`.

Затронутые файлы: `backend/alembic/versions/2d4f3a1c0b11_ai_review_replies_review_created_at.py`, `BUGLOG.md`

---
ID: BUG-33
Дата: 2026-05-12
Статус: fixed
Автоматизация: да (pytest: контракт API поля даты)

## Бизнес-описание
В модалке задачи “Ответить на отзывы” в карточке отзыва отображалась дата “первого обнаружения” в системе, а пользователю нужна дата самого отзыва на WB.

## Процесс / сценарий
1) Открыть “ИИ → Задачи” → “Ответить на отзывы”.
2) Ожидание: в строке/карточке отзыва отображается реальная дата отзыва на WB.
3) Факт (до фикса): отображалась `first_seen_date` (когда мы впервые синкнули отзыв).

## Техническое описание
WB API `GET /api/v1/feedbacks` возвращает `createdDate`, но мы не сохраняли её и в UI показывали `first_seen_date`.

## Root cause (почему произошло)
- На MVP шаге сохраняли только “дату появления в системе” и не вывели отдельное поле “дата отзыва”.

## Исправление (что сделали)
Добавлено поле `review_created_at` (WB `createdDate`) в `ai_review_replies`; при sync сохраняем/обновляем его и отдаём в API, а UI показывает именно эту дату (с fallback на `first_seen_date` для старых строк).

## Профилактика (как не повторить)
- Для сущностей, синхронизируемых из внешнего источника, хранить “source created_at” отдельно от “first_seen” и показывать в UI по смыслу.

## Проверка
- Команды: `ruff check .`, `mypy .`, `pytest`, `npm run lint`, `npm run build`
- Сценарии: открыть модалку → дата совпадает с WB `createdDate`; publish/reties не сломаны.

Затронутые файлы: `backend/app/models/ai_review_reply.py`, `backend/app/services/ai_review_replies_service.py`, `backend/app/routers/ai_module.py`, `backend/alembic/versions/2d4f3a1c0b11_ai_review_replies_review_created_at.py`, `backend/tests/test_ai_module_api.py`, `frontend/src/screens/AiModule.jsx`, `BUGLOG.md`

---
ID: BUG-27
Дата: 2026-05-12
Статус: fixed
Автоматизация: нет (ручной репорт в UI)

## Бизнес-описание
В разделе «ИИ → Задачи» список задач долго висел на «Загрузка…», из‑за чего создавалось ощущение, что модуль зависает или не работает.

## Процесс / сценарий
1) Открыть «ИИ модуль» → вкладка «Задачи».
2) Ожидание: задачи отображаются быстро; синхронизация отзывов (WB) не должна блокировать UI.
3) Факт (до фикса): экран ждёт завершения синка отзывов, поэтому загрузка задач откладывается и может занимать заметное время.

## Техническое описание
В `frontend/src/screens/AiModule.jsx` `TasksTab.reload()` делал `await /ai/review-replies/sync` перед `GET /ai/tasks`.
Синк включает сетевые вызовы WB + генерацию ответов AI и может быть медленным, поэтому `loading=true` держался дольше ожидаемого.

## Root cause (почему произошло)
- Попытка “сделать лучше” автосинком была реализована как блокирующий `await`, хотя по контракту должен быть best-effort и не мешать отображению задач.

## Исправление (что сделали)
Синхронизация отзывов запускается в фоне (без `await`) и по завершении делает best-effort refresh списка задач; первичная отрисовка задач больше не зависит от скорости синка.

## Профилактика (как не повторить)
- Для любых best-effort prefetch/sync в UI: не ставить блокирующий `await` перед критичным `GET` списка.

## Проверка
- Команды: `npm run lint`, `npm run build`
- Сценарии: открыть «ИИ → Задачи» — задачи появляются быстро; позже (после завершения синка) задача “Ответить на отзывы” подтягивается сама при наличии отзывов.

Затронутые файлы: `frontend/src/screens/AiModule.jsx`, `frontend/dist/*`, `BUGLOG.md`

---
ID: BUG-28
Дата: 2026-05-12
Статус: fixed
Автоматизация: нет (ручной репорт в UI)

## Бизнес-описание
В модалке задачи “Ответить на отзывы” таблица занимала слишком много места (ощущалась как “не стандартная”), отображался лишний `feedback_id`, а после публикации не было понятного статуса “Опубликовано/Ошибка публикации” напротив отзыва.

## Процесс / сценарий
1) Открыть “ИИ → Задачи” → “Ответить на отзывы”.
2) Нажать “Опубликовать” у строки.
3) Ожидание: компактная стандартная модалка с прокруткой, без лишнего ID; после ответа WB видно понятный статус по конкретной строке.
4) Факт (до фикса): `feedback_id` занимал колонку; модалка визуально “раздута”; статус публикации не отображался в строке.

## Техническое описание
`ReviewRepliesApproval` рендерил таблицу с колонкой `feedback_id` и отдельной кнопкой “Опубликовать” без явной индикации результата. Модалка была шире ожидаемого и не ограничивала высоту контента.

## Root cause (почему произошло)
- UI MVP не включал UX-контракт по размерам модалки и состояниям “publish ok/error” на строке.

## Исправление (что сделали)
- Убрали колонку `feedback_id`.
- Модалка стала “стандартнее” (уже по ширине) и с контролируемой прокруткой.
- В строке отображается статус публикации: зелёный “Опубликовано” при 200, красный “Ошибка публикации” при ошибке (с возможностью повторить).

## Профилактика (как не повторить)
- Для действий с side-effect на строке (publish) всегда иметь UI state “pending/ok/error” рядом с действием.

## Проверка
- Команды: `npm run lint`, `npm run build`
- Сценарии: открыть модалку → скролл работает → publish ok показывает “Опубликовано”, publish error показывает “Ошибка публикации”.

Затронутые файлы: `frontend/src/screens/AiModule.jsx`, `frontend/dist/*`, `BUGLOG.md`

---
ID: BUG-29
Дата: 2026-05-12
Статус: fixed
Автоматизация: нет (ручной репорт в UI)

## Бизнес-описание
В модалке “Ответить на отзывы” таблица требовала горизонтального скролла, колонка “Товар” была слишком широкой и без аккуратного переноса, не хватало даты отзыва, а заголовки колонок выглядели “скудно” и не были выровнены.

## Процесс / сценарий
1) Открыть “ИИ → Задачи” → “Ответить на отзывы”.
2) Ожидание: таблица помещается по ширине без горизонтального скролла; “Товар” компактный с переносом; есть дата; заголовки выровнены и визуально аккуратные.
3) Факт (до фикса): ширина таблицы “раздувалась” (minWidth), из‑за чего появлялся горизонтальный скролл; колонки были несбалансированы.

## Техническое описание
`ReviewRepliesApproval` использовал `minWidth` и свободный layout, из-за чего таблица выходила за ширину модалки. Не было отдельной колонки даты (хотя в данных есть `first_seen_date`).

## Root cause (почему произошло)
- Не был задан фиксированный layout таблицы и явные ширины ключевых колонок.

## Исправление (что сделали)
- Таблица переведена на `tableLayout: fixed` + `width: 100%`, отключён горизонтальный overflow.
- Колонка “Товар” стала уже, добавлен перенос/word-break.
- Добавлена колонка “Дата” (используем `first_seen_date`).
- Заголовки колонок выровнены по центру и стилизованы.
- Верхний блок модалки оформлен как лёгкая “шапка” с количеством найденных отзывов.

## Профилактика (как не повторить)
- Для таблиц в модалках всегда задавать `tableLayout: fixed` и ограничения по overflow.

## Проверка
- Команды: `npm run lint`, `npm run build`
- Сценарии: открыть модалку → нет горизонтального скролла → колонки компактные → дата видна → заголовки по центру.

Затронутые файлы: `frontend/src/screens/AiModule.jsx`, `frontend/dist/*`, `BUGLOG.md`

---
ID: BUG-30
Дата: 2026-05-12
Статус: fixed
Автоматизация: нет (ручной репорт в UI)

## Бизнес-описание
В модалке “Ответить на отзывы” при табличной раскладке текст отзыва мог превращаться в “вертикальные буквы” из-за узкой колонки, а таблица визуально выглядела перегруженной и плохо адаптировалась по ширине.

## Процесс / сценарий
1) Открыть “ИИ → Задачи” → “Ответить на отзывы”.
2) На строках с длинным отзывом увидеть, что колонка “Отзыв” становится слишком узкой и текст переносится по буквам.
3) Ожидание: читабельная раскладка без горизонтального скролла и без “ломания” текста.

## Техническое описание
Таблица с `tableLayout: fixed` и фиксированными ширинами колонок могла оставлять слишком мало места для “Отзыв”, что приводило к переносам по символам. UI был лучше реализовать как карточки-строки.

## Root cause (почему произошло)
- Попытка вместить много колонок в модалку через table-layout без адаптивного поведения контента.

## Исправление (что сделали)
Список отзывов переведён на карточки:
- верхняя строка: дата + оценка + статус;
- отдельно показываем товар, отзыв и textarea ответа;
- действия публикации/ретрая и статус остаются на карточке.

## Профилактика (как не повторить)
- Для мобильных/узких модалок сложные таблицы лучше реализовывать карточками.

## Проверка
- Команды: `npm run lint`, `npm run build`
- Сценарии: открыть модалку на узком окне — текст отзывов читабелен, горизонтального скролла нет.

Затронутые файлы: `frontend/src/screens/AiModule.jsx`, `frontend/dist/*`, `BUGLOG.md`

---
ID: BUG-31
Дата: 2026-05-12
Статус: fixed
Автоматизация: да (pytest: публикация вызывает httpx.post на правильный WB endpoint)

## Бизнес-описание
При нажатии “Опубликовать” ответ на отзыв не публиковался в WB, а UI показывал ошибку `405 Method Not Allowed`.

## Процесс / сценарий
1) Открыть “ИИ → Задачи” → “Ответить на отзывы”.
2) Нажать “Опубликовать” у конкретного отзыва.
3) Ожидание: WB принимает ответ, статус строки становится “Опубликовано”.
4) Факт (до фикса): WB отвечал 405, публикации не происходило.

## Техническое описание
Сервис `publish_review_reply` отправлял запрос `PATCH /api/v1/feedbacks`, тогда как по контракту WB публикация ответа делается через `POST /api/v1/feedbacks/answer` (а `PATCH /feedbacks/answer` — это редактирование ответа).

## Root cause (почему произошло)
- Перепутан endpoint/HTTP method WB API для публикации ответа.

## Исправление (что сделали)
- Публикация переведена на `POST https://feedbacks-api.wildberries.ru/api/v1/feedbacks/answer` с payload `{id, text}`.
- Обновлён тест: мокается `httpx.post`, проверяется успешное выставление `published`.

## Профилактика (как не повторить)
- Контрактный pytest на HTTP method/endpoint публикации.

## Проверка
- Команды: `ruff check .`, `mypy .`, `pytest`
- Сценарии: publish с реальным токеном WB должен возвращать 200 и менять статус на “Опубликовано”.

Затронутые файлы: `backend/app/services/ai_review_replies_service.py`, `backend/tests/test_ai_review_replies_api.py`, `BUGLOG.md`

---
ID: BUG-32
Дата: 2026-05-12
Статус: fixed
Автоматизация: да (pytest: publish успех при WB 204)

## Бизнес-описание
После смены WB-токена публикация ответа на отзыв всё равно показывалась как “Ошибка публикации”, хотя WB возвращал `204 No Content`.

## Процесс / сценарий
1) Нажать “Опубликовать” в задаче “Ответить на отзывы”.
2) WB отвечает `204 No Content`.
3) Ожидание: статус “Опубликовано”.
4) Факт (до фикса): backend трактовал 204 как ошибку, UI показывал “Ошибка публикации”.

## Техническое описание
`publish_review_reply` принимал успех только при `status_code == 200`.
WB для `POST /feedbacks/answer` может возвращать `204` как успешный ответ без тела.

## Root cause (почему произошло)
- Слишком узкая проверка успешного HTTP-кода.

## Исправление (что сделали)
- Успехом считаем `200` и `204`.
- Обновлён pytest: успешная публикация при мокнутом `204`.

## Профилактика (как не повторить)
- В контрактных тестах учитывать “без тела” ответы для write-операций внешних API.

## Проверка
- Команды: `ruff check .`, `mypy .`, `pytest`
- Сценарии: WB 204 → статус в UI “Опубликовано”.

Затронутые файлы: `backend/app/services/ai_review_replies_service.py`, `backend/tests/test_ai_review_replies_api.py`, `BUGLOG.md`

---
ID: BUG-26
Дата: 2026-05-11
Статус: fixed
Автоматизация: да (pytest на смесь долей и п.п. в строке конверсии)

## Бизнес-описание
В отчёте WB «Показатели» конверсии выглядели нормально, а в гипотезе ИИ медиана конкурентов по «Конверсия в заказ, %» уходила в **100** или иной мусор — создавалось ощущение, что медиану «не берут из отчёта».

## Процесс / сценарий
1) Excel: в одной строке смешаны форматы (часть ячеек как доли Excel 0.12, часть уже как 15 п.п.).
2) Импорт → в БД некорректная `competitor_median_value`.
3) Текст гипотезы показывает неверную медиану.

## Техническое описание
`parse_wb_competitor_excel` считал `median()` по **сырым** значениям ячеек без приведения строки «Конверсия …, %» к одной шкале (процентные пункты).

## Root cause (почему произошло)
- Нет нормализации долей (0–1) и п.п. в одной строке до агрегата; `median(15, 0.12, …)` математически не соответствует тому, что видит пользователь в таблице.

## Исправление (что сделали)
- Перед mean/median по конкурентам для `funnel_cart` / `funnel_order`: `_normalize_funnel_row_to_percent_points` (эвристика смешанной шкалы, как в комментариях к CTR).
- Pytest: смесь 15 + 0.12/0.18 → медиана 15 п.п.; строка только из долей → ×100.

## Профилактика (как не повторить)
- Регрессионные тесты парсера на смешанную шкалу.

## Проверка
- Команды: `ruff`, `mypy`, `pytest`.

Затронутые файлы: `backend/app/services/ai_competitor_excel_parser.py`, `backend/tests/test_ai_competitor_excel_parser_cards_comparison.py`

---
ID: BUG-25
Дата: 2026-05-11
Статус: fixed
Автоматизация: да (pytest `test_presentable_hypothesis_repairs_human_copy_when_median_is_ceiling_100`)

## Бизнес-описание
После перехода на «человеческий» `trigger_reason` без `vs median` подпись с **медианой 100.0** по конверсии продолжала отображаться в UI: пользователь видел нереалистичные «100% у конкурентов».

## Процесс / сценарий
1) В БД сохранена гипотеза content_change с текстом «…по медиане конкурентов 100.0».
2) `GET /ai/hypotheses` — тот же текст (repair не срабатывал: нет технических маркеров).

## Техническое описание
`presentable_hypothesis_fields` вызывал repair только при `hypothesis_api_copy_needs_repair`; новый формат триггера не попадал под правила.

## Root cause (почему произошло)
- Repair завязан на устаревшие шаблоны строк; человекочитаемый текст с тем же багом данных обходил ветку пересборки.

## Исправление (что сделали)
- Для `content_change` при наличии `competitor_median_metrics` сначала проверяем плаузибельность/порог по воронкам; при отсутствии валидного срабатывания отдаём блок «недостоверно» (до проверки needs_repair).
- В `hypothesis_api_copy_needs_repair` добавлен признак «по медиане конкурентов 100» для догона строк без JSON метрик.

## Профилактика (как не повторить)
- Pytest на человекочитаемый триггер + медиана 100 в JSON.

## Проверка
- Команды: `ruff`, `mypy`, `pytest`.

Затронутые файлы: `backend/app/services/ai_daily_analytics_service.py`, `backend/tests/test_ai_hypothesis_presentable_fields.py`

---
ID: BUG-24
Дата: 2026-05-11
Статус: fixed
Автоматизация: да (pytest `test_ai_daily_analytics_does_not_trigger_content_change_on_funnel_median_ceiling_100`)

## Бизнес-описание
В тексте гипотезы «контент» снова появлялась медиана конкурентов **100.0** по конверсии в заказ — выглядит как 100% конверсия и разрушает доверие к выводу.

## Процесс / сценарий
1) Импорт отчёта WB / метрики в БД с `competitor_median_value == 100` для `funnel_order` (или карт).
2) Прогон `run_daily_analytics`.
3) Факт: гипотеза создаётся, в `trigger_reason` фигурирует «по медиане конкурентов 100.0».
4) Ожидание: такая медиана не должна считаться надёжной опорой для правила (кэп выгрузки / артефакт).

## Техническое описание
`ai_daily_analytics_service._funnel_conversion_plausible` допускал `med <= 100` и `ours <= 100`, поэтому ровно 100.0 проходила проверку «похоже на п.п.» и попадала в `_human_funnel_trigger`.

## Root cause (почему произошло)
- На границе диапазона п.п. не отличали «реальные 100%» от типичного кэпа/ошибки строки в Excel.
- Число 100 приходит из парсера как медиана по ячейкам строки «Конверсия …, %», а не из константы UI.

## Исправление (что сделали)
- Для воронки: плаузибельность только при **строго** `ours < 100` и `med < 100` п.п.; ровно 100 больше не запускает правило content_change и не попадает в новый человекочитаемый триггер.

## Профилактика (как не повторить)
- Регрессионный pytest на импорт + аналитику с `competitor_median_value=100` для `funnel_order`.

## Проверка
- Команды: `ruff check`, `mypy`, `pytest`.
- Сценарии: ручной импорт с медианой 100 → нет новой гипотезы content_change.

Затронутые файлы: `backend/app/services/ai_daily_analytics_service.py`, `backend/tests/test_ai_module_api.py`

---
ID: BUG-23
Дата: 2026-05-11
Статус: fixed
Автоматизация: да (pytest на парсер + подмена текста гипотезы)

## Бизнес-описание
В ИИ-модуле у гипотезы «контент» и в карточках отображались «технические» строки вроде `funnel_cart: 40 vs median 200 (-80%)`. Пользователь воспринимал «200» как проценты и терял доверие к выводам.

## Процесс / сценарий
1) Импорт отчёта сравнения / старый прогон аналитики.
2) Открыть гипотезу «поменять контент».
3) Факт: в описании/триггере — коды метрик и медиана 200 при ожидаемых процентах до 100.
4) Ожидание: только понятный русский текст и корректная интерпретация чисел.

## Техническое описание
Парсер «Показатели» мог сматчить не ту строку (или в строке попали не процентные пункты, а большие абсолютные значения). В БД оставался старый человекочитаемый/технический `trigger_reason` от прошлых версий кода. API отдавал поля как в БД.

## Root cause (почему произошло)
- Слишком широкие ключи нормализации строки конверсии + отсутствие проверки «похоже ли на проценты п.п.».
- Нет подмены устаревшего текста при чтении гипотезы из API.

## Исправление (что сделали)
- Парсер: только явные ключи «Конверсия …, %» / «из показов»; строка конверсии не импортируется, если в ячейках есть значения > 100.
- Аналитика: правила воронки/CTR не срабатывают на метриках, где числа не похожи на процентные пункты (воронка ≤100, CTR ≤100).
- `GET /ai/hypotheses` и `GET /ai/hypotheses/{id}`: `presentable_hypothesis_fields` — подмена старого/технического текста на новый или пояснение про «медиана 200 не равно 200%».

## Профилактика (как не повторить)
- Pytest: строка конверсии с >100 не попадает в импорт; подмена legacy-триггера для API.

## Проверка
- Команды: `ruff check .`, `mypy .`, `pytest`
- Сценарии: парсер insane row; presentable_hypothesis_fields для legacy и для нормальных метрик

Затронутые файлы: `backend/app/services/ai_competitor_excel_parser.py`, `backend/app/services/ai_daily_analytics_service.py`, `backend/app/routers/ai_module.py`, `backend/tests/test_ai_competitor_excel_parser_cards_comparison.py`, `backend/tests/test_ai_hypothesis_presentable_fields.py`

---
ID: BUG-22
Дата: 2026-05-11
Статус: fixed
Автоматизация: да (pytest + контрактная проверка генерации/закрытия задачи)

## Бизнес-описание
В AI-модуле задача «Дать доступ к кабинету WB» появлялась снова сразу после нажатия «Готово», хотя пользователь ожидал: либо доступа нет → задача одна и ведёт в выдачу доступа, либо доступ уже выдан → таких задач нет вообще.

## Процесс / сценарий
1) Открыть AI-модуль → «Задачи».
2) При отсутствии доступа к WB появляется задача «Дать доступ к кабинету WB».
3) Нажать «Готово».
4) Факт (до фикса): задача закрывается и тут же появляется снова.
5) Ожидание: задача не должна быть “закрываемой” вручную до факта выдачи доступа; после сохранения доступа она должна исчезнуть (автозакрыться).

## Техническое описание
Генерация задачи в `GET /ai/tasks` была привязана к статусу WB-credentials (`/ai/wb-credentials/status == missing`), а не к реальному сигналу доступа (`storage_state` файл). Поэтому при отсутствии `storage_state` задача пересоздавалась после ручного закрытия. Дополнительно UI показывал кнопку “Готово”, что позволяло “закрыть” задачу без выполнения.

## Root cause (почему произошло)
- Смешаны два разных контракта: “есть креды” и “есть сохранённый WB-доступ (storage_state)”.
- Не было запрета на ручное завершение задачи типа `wb_access_grant` до факта выдачи доступа.

## Исправление (что сделали)
- `GET /ai/tasks` теперь создаёт/держит задачу `wb_access_grant` **только** если `storage_state` отсутствует; если `storage_state` появился — задача автозакрывается.
- `PATCH /ai/tasks/{id}` теперь запрещает завершать `wb_access_grant` в `completed`, пока `storage_state` не сохранён.
- В UI в карточке задачи `wb_access_grant` добавлена явная кнопка “Выдать доступ”, а кнопка “Готово” скрыта для этого типа задачи.
- Обновлены/добавлены pytest регрессии.

## Профилактика (как не повторить)
- Для “человеческих задач” держать единый источник истины: состояние доступа/файла/энтити, а не производные статусы.
- Любая автогенерируемая UX-задача должна иметь тест на “не пересоздаётся после ручного клика/refresh”.

## Проверка
- Команды: `ruff check .`, `mypy .`, `pytest`
- Сценарии: без storage_state задача есть и ведёт в “Выдать доступ”; после сохранения storage_state задача исчезает/закрывается и больше не появляется.

Затронутые файлы: `backend/app/routers/ai_module.py`, `backend/app/services/ai_module_service.py`, `backend/tests/test_ai_module_api.py`, `frontend/src/screens/AiModule.jsx`, `BUGLOG.md`
---

---
ID: BUG-21
Дата: 2026-05-11
Статус: fixed
Автоматизация: да (pytest регрессия на переход статуса задачи)

## Бизнес-описание
В AI-модуле пользователь не мог нажать «Готово» по задаче в статусе «Новая»: интерфейс показывал ошибку `Task transition not allowed: new -> completed`, и задача не закрывалась.

## Процесс / сценарий
1) Открыть AI-модуль → вкладка «Задачи и гипотезы».
2) Открыть задачу со статусом «Новая».
3) Нажать кнопку «Готово».
4) Ожидание: задача переходит в «Готово», исчезает из списка открытых.
5) Факт (до фикса): backend отвечал 409 с `Task transition not allowed: new -> completed`.

## Техническое описание
UI вызывал `PATCH /ai/tasks/{task_id}` со статусом `completed` из состояния `new`. В `update_task_status` (сервис `app/services/ai_module_service.py`) state-machine разрешала `completed` только из `in_progress`, поэтому переход `new -> completed` отклонялся.

## Root cause (почему произошло)
- Слишком строгая state-machine для MVP: UI контракт “закрыть задачу одной кнопкой” не совпал с backend контрактом “сначала in_progress”.
- Не было теста, который фиксирует допустимость `new -> completed`.

## Исправление (что сделали)
- Разрешили переход `new -> completed` в `update_task_status` (при этом выставляются `started_at` и `completed_at`).
- Добавлен pytest `test_ai_task_can_complete_from_new`.

## Профилактика (как не повторить)
- Держать UI и backend контракты статусов в одном источнике истины (tests).
- Любой UI action кнопки “Готово/Отменить” должен иметь контрактный тест на допустимый transition.

## Проверка
- Команды: `ruff check .`, `mypy .`, `pytest`
- Сценарии: “Новая задача” → нажать “Готово” → статус `completed` без промежуточного `in_progress`.

Затронутые файлы: `backend/app/services/ai_module_service.py`, `backend/tests/test_ai_module_api.py`, `BUGLOG.md`
---

---
ID: BUG-20
Дата: 2026-05-11
Статус: fixed
Автоматизация: нет (ручной репорт + smoke-check docker)

## Бизнес-описание
В AI-модуле после “Я вошёл” баннер “Дайте доступ к кабинету WB” продолжал висеть, хотя доступ (storage_state) уже был сохранён. Пользователь воспринимал это как “вход не сохранился / система не помнит сессию”.

## Процесс / сценарий
1) Открыть “ИИ модуль” → “Выдать доступ”.
2) Залогиниться в WB в remote окне → нажать “Я вошёл”.
3) Ожидание: баннер шага 2 исчезает.
4) Факт (до фикса): баннер оставался, даже при наличии файла storage_state.

## Техническое описание
`wb_auth` сохранял storage_state в `WB_PLAYWRIGHT_STORAGE_STATE_DIR=/app/data/wb_storage_states`, но `api`/`celery` использовали дефолтный путь (`tmp/wb_storage_states`). Из-за этого API `GET /ai/wb-access/status` не находил файл и возвращал `has_storage_state=false`, что оставляло onboarding step 2 видимым.

## Root cause (почему произошло)
- Несогласованная конфигурация env между сервисами: переменная `WB_PLAYWRIGHT_STORAGE_STATE_DIR` была задана только в `wb_auth`.
- Проверка “доступ выдан” опиралась на факт существования файла, но контейнеры смотрели в разные директории.

## Исправление (что сделали)
- В `docker-compose.yml` выставлен единый `WB_PLAYWRIGHT_STORAGE_STATE_DIR=/app/data/wb_storage_states` для `api`, `celery_worker`, `celery_beat` (как и у `wb_auth`).
- Пересобран/перезапущен compose; проверено, что файл виден и в `api`, и в `wb_auth`.

## Профилактика (как не повторить)
- Для “общих файлов состояния” всегда фиксировать единый путь в compose для всех сервисов, а не только для writer.
- Добавить smoke-check в релизный чеклист: файл storage_state виден из `api` контейнера.

## Проверка
- Команды: `docker compose up -d --build`, `docker compose exec api ls -la /app/data/wb_storage_states`
- Сценарии: “Я вошёл” → `has_storage_state=true` → баннер шага 2 исчезает.

Затронутые файлы: `docker-compose.yml`, `BUGLOG.md`
---

---
ID: BUG-19
Дата: 2026-05-11
Статус: fixed
Автоматизация: нет (ручной репорт)

## Бизнес-описание
В AI-модуле после успешной выдачи доступа к кабинету WB onboarding-плашка “Дайте доступ” не пропадала, из-за чего создавалось ощущение, что доступ не выдан. Также при открытии модалки “Выдать доступ” пользователю каждый раз приходилось вручную нажимать “Открыть окно”, хотя логично открывать окно сразу.

## Процесс / сценарий
1) Пройти шаг 1 (выбрать товар → OK).
2) Нажать “Выдать доступ” → в модалке авторизоваться → “Я вошёл” (окно закрывается).
3) Ожидание: onboarding-плашка исчезает (на экране остаётся только основной блок).
4) Факт (до фикса): плашка оставалась на шаге 2.
5) Дополнительно: при каждом открытии модалки нужно было вручную нажимать “Открыть окно”.

## Техническое описание
UI шагов на экране `frontend/src/screens/AiModule.jsx` опирался на `credsStatus` (статус WB-доступа), который обновлялся асинхронно после `onGranted()`. Если обновление занимало время/не успевало отрисоваться, step 2 оставался видимым. Также `WbAccessModal` не пыталась открыть remote окно автоматически при открытии.

## Root cause (почему произошло)
- Недостаточно “склеен” UI-контракт: успешное `onGranted()` не давало немедленного пользовательского результата (скрытия плашки), пока не обновится `credsStatus`.
- UX модалки “Выдать доступ” требовал лишнего шага (“Открыть окно”) при каждом открытии.

## Исправление (что сделали)
- В `AiModule` добавлен optimistic update `credsStatus={status:'ok'}` в обработчике `onGranted()`, чтобы onboarding-плашка скрывалась сразу после успешного сохранения доступа.
- Добавлена проверка активной remote-сессии: backend `/ai/wb-access/remote/status` (проксирует `wb_auth /status`), а UI скрывает шаг “Выдать доступ”, если remote-сессия уже активна.
- Пересобран `frontend/dist`.

## Профилактика (как не повторить)
- Зафиксировать правило: любые шаги onboarding должны менять видимое состояние **сразу** после success, не дожидаясь фонового refresh статусов.
- Добавить e2e на happy-path: “Я вошёл” → плашка исчезла (если будем расширять e2e для AI экрана).

## Проверка
- Команды: `npm run lint`, `npm run build`, `ruff check .`, `mypy .`, `pytest`
- Сценарии: шаг 1 → шаг 2 → “Я вошёл”/upload JSON → плашка исчезает; если remote-сессия уже открыта — шаг “Выдать доступ” не показывается.

Затронутые файлы: `frontend/src/screens/AiModule.jsx`, `frontend/src/api.js`, `frontend/dist/*`, `backend/app/routers/ai_module.py`, `backend/app/wb_auth_manager.py`, `BUGLOG.md`
---

---
ID: BUG-18
Дата: 2026-05-11
Статус: fixed
Автоматизация: да (pytest: `test_ai_competitor_report_status_falls_back_to_any_period_if_requested_missing`)

## Бизнес-описание
В AI-модуле пользователь нажимал «Я создал сравнение», хотя отчёт сравнения уже был в системе, но интерфейс показывал «Отчёт пока не найден / отчёт не создан» и не давал продолжить сценарий.

## Процесс / сценарий
1) Пользователь создаёт отчёт сравнения (например, в периоде “месяц”), отчёт импортирован и доступен в системе.
2) Открывает AI-модуль и нажимает «Я создал сравнение».
3) Ожидание: система видит, что отчёт уже есть, и не показывает состояние “missing”.
4) Факт (до фикса): запрос статуса делался с `period=week` и получал `missing`, поэтому UI показывал сообщение, что отчёта нет.

## Техническое описание
Эндпоинт `GET /ai/competitor-reports/status?period=week` искал отчёт только по запрошенному `period`. Если последний отчёт был сохранён под другим периодом (`month`/`quarter`), эндпоинт возвращал `status=missing`, хотя отчёт у пользователя в БД уже был.

## Root cause (почему произошло)
- В UI период статуса был захардкожен как `week`, а backend трактовал отсутствие отчёта именно в этом периоде как полное отсутствие отчёта.
- Не был зафиксирован контракт “status week должен не ложно сигналить missing при наличии отчёта другого периода”.

## Исправление (что сделали)
В `GET /ai/competitor-reports/status` добавлен fallback: если по запрошенному `period` отчёт не найден, берём самый свежий отчёт пользователя по любому периоду и возвращаем его статус (или `missing`, если отчётов нет вовсе).

## Профилактика (как не повторить)
- Добавлен регрессионный pytest на fallback поведения статуса при наличии отчёта другого периода.

## Проверка
- Команды: `ruff check .`, `mypy .`, `pytest`
- Сценарии: есть отчёт `period=month` → `GET /ai/competitor-reports/status?period=week` возвращает не `missing`.

Затронутые файлы: `backend/app/routers/ai_module.py`, `backend/tests/test_ai_module_api.py`, `BUGLOG.md`
---

---
ID: BUG-17
Дата: 2026-05-11
Статус: fixed
Автоматизация: нет (ручной репорт + curl-проверка)

## Бизнес-описание
В модалке «Выдать доступ к кабинету WB» встроенное окно вместо удалённого браузера (noVNC) показывало наш же интерфейс WB Finance Pro. Из‑за этого пользователь не мог выдать доступ к WB.

## Процесс / сценарий
1) Открыть AI-модуль → «Выдать доступ» → «Открыть окно».
2) Ожидание: загружается noVNC (`/wb-auth/vnc.html`) и видно удалённый браузер.
3) Факт (до фикса): iframe показывал наш SPA (как будто просто вложенная страница приложения).

## Техническое описание
Запрос `GET /wb-auth/vnc.html` на `https://localhost:8444` **не проксировался** в `wb_auth`, а падал в общий `handle` со `root /var/www/app` и `try_files ... /index.html`.
В итоге Caddy отдавал `frontend/dist/index.html`, и iframe отображал приложение вместо noVNC.

## Root cause (почему произошло)
- Конфигурация Caddy использовала `handle_path @wb_auth` (через named matcher), но матчинг не срабатывал для `/wb-auth/*`, поэтому запросы “проваливались” в SPA fallback.

## Исправление (что сделали)
- Переписали роутинг на буквальный путь: `handle_path /wb-auth/* { reverse_proxy wb_auth:6080 }` (и для localhost, и для `app.sellerfocus.pro`).
- Проверили `curl`: `https://localhost:8444/wb-auth/vnc.html` теперь возвращает HTML noVNC (WebSockify), а не `WB Finance Pro`.

## Профилактика (как не повторить)
- Для критичных прокси-путей (auth/remote tools) использовать явные path-handlers, чтобы запрос не мог “упасть” в `try_files /index.html`.
- Добавить smoke-check в релизный чеклист: `curl -I /wb-auth/vnc.html` должен вернуть `server: WebSockify`.

## Проверка
- Команды: `curl -k -I https://localhost:8444/wb-auth/vnc.html`, `docker compose restart caddy`
- Сценарии: “Открыть окно” в UI показывает noVNC (не SPA).

Затронутые файлы: `Caddyfile`, `BUGLOG.md`
---

---
ID: BUG-16
Дата: 2026-05-11
Статус: fixed
Автоматизация: нет (ручной репорт в локальном UI)

## Бизнес-описание
В модалке «Выдать доступ к кабинету WB» встроенное “удалённое окно” (noVNC) иногда показывало не WB-кабинет, а наш же интерфейс (как “дублирующий экран”). Пользователь не мог понять, что происходит, и не мог авторизоваться в WB.

## Процесс / сценарий
1) Открыть AI-модуль.
2) Нажать «Выдать доступ» → «Открыть окно».
3) Ожидание: внутри окна открывается страница `seller.wildberries.ru` для входа.
4) Факт (до фикса): иногда показывалось предыдущее содержимое удалённого браузера (например наш UI), если сессия уже была запущена ранее и пользователь в ней навигировал.

## Техническое описание
Сервис `wb_auth` переиспользует активную Playwright-сессию на пользователя. Эндпоинт `POST /start` при `already_started` не делал повторную навигацию на WB, поэтому в noVNC показывалась последняя открытая страница удалённого браузера.

## Root cause (почему произошло)
- Недоучтено, что пользователь может навигировать внутри удалённого браузера и “увести” вкладку с WB на другие страницы.
- При повторном открытии окна UI не принуждал сессию вернуться в ожидаемую стартовую точку (WB login root).
- Технический баг: `wb_auth_manager` использовал Playwright **Sync API** внутри FastAPI/asyncio окружения, что приводило к `500 Internal Server Error` на `POST /start`.

## Исправление (что сделали)
Сделали поведение “Открыть окно” детерминированным:
1) `POST /start` теперь **всегда пересоздаёт Playwright-сессию** (закрывает старый browser/context и создаёт новый), чтобы не зависеть от вкладок/навигации внутри удалённого браузера.
2) Во фронте iframe с noVNC теперь форсированно “переподключается” на каждый клик “Открыть окно” (смена `key`), чтобы не залипать на старом websocket-сеансе.
3) `wb_auth_manager` переведён на Playwright **Async API** (`async_playwright`), чтобы `POST /start` не падал с ошибкой “Sync API inside the asyncio loop”.

## Профилактика (как не повторить)
- В этом флоу держать инвариант: “каждое открытие окна показывает WB login root”.
- При необходимости — добавить health/telemetry на `wb_auth` (время старта, навигация, ошибки) и автосброс сессии при repeated failures.

## Проверка
- Команды: `ruff check .`, `mypy .`, `pytest`
- Сценарии: “Выдать доступ” → “Открыть окно” → если открыть окно повторно после навигации внутри — снова показывается WB login root.

Затронутые файлы: `backend/app/wb_auth_manager.py`, `frontend/src/screens/AiModule.jsx`, `frontend/dist/*`, `BUGLOG.md`
---

---
ID: BUG-15
Дата: 2026-05-11
Статус: fixed
Автоматизация: да (pytest + ручной репорт в локальном UI)

## Бизнес-описание
Пользователь нажимал «Выдать доступ» в AI-модуле и видел ошибку, доступ к кабинету WB не выдавался — нельзя было продолжить работу со сравнением и задачами/гипотезами.

## Процесс / сценарий
1) Открыть вкладку AI-модуля.
2) Нажать «Выдать доступ».
3) Ожидание: открывается окно WB, пользователь авторизуется, окно закрывается, доступ выдан.
4) Факт (до фикса): в локальном Docker на mac появлялась ошибка `Internal Server Error`, потому что Playwright headed не мог запуститься без X/Display.

## Техническое описание
Эндпоинт `POST /ai/wb-access/grant` запускал Playwright с `headless=False` внутри контейнера `api`, где нет X server / `$DISPLAY`.

## Root cause (почему произошло)
- Недоучтено окружение: в Docker по умолчанию нет GUI/Xserver, поэтому “интерактивный браузер” внутри контейнера не стартует.
- Не было fallback пути для выдачи доступа в headless окружениях.

## Исправление (что сделали)
Добавлен fallback: загрузка “файла доступа” (Playwright `storage_state` JSON) через `POST /ai/wb-access/storage-state`.
Эндпоинт `grant` теперь возвращает 503 с понятным текстом в окружениях без display.
UI показывает блок загрузки файла при такой ошибке.

## Профилактика (как не повторить)
- Держать интерактивные (GUI) флоу env-gated и иметь headless fallback (upload storage_state).
- Ручная проверка UX “Выдать доступ” на локальном Docker при изменениях в этом блоке.

## Проверка
- Команды: `ruff check .`, `mypy .`, `pytest`, `npm run lint`, `npm run build`
- Сценарии: в локальном Docker “Выдать доступ” → появляется загрузка файла доступа; upload JSON → OK.

Затронутые файлы: `backend/app/services/ai_wb_access_service.py`, `backend/app/routers/ai_module.py`, `frontend/src/screens/AiModule.jsx`, `frontend/src/api.js`, `frontend/dist/*`, `BUGLOG.md`
---

---
ID: BUG-14
Дата: 2026-05-10
Статус: fixed
Автоматизация: да (pytest: `test_start_trial_if_needed_skips_when_lifetime`)

## Бизнес-описание
Пользователю с вручную выданным пожизненным доступом (например `test@test.ru`) на странице «Подписка» показывалось «Истекла» / 0 дней и баннеры об окончании демо, хотя в БД лицензия была `lifetime`.

## Процесс / сценарий
1) Админ/скрипт выставляет `licenses.status = lifetime` для пользователя.
2) У пользователя задан WB API key, `trial_started_at` ещё не проставлен.
3) При следующем запросе биллинга вызывается `start_trial_if_needed`.
4) Ожидание: статус остаётся lifetime, UI показывает бессрочный доступ.
5) Факт (до фикса): `_upsert_license(..., "trial", ...)` перезаписывал лицензию на trial с истёкшим сроком → UI «Истекла».

## Техническое описание
В `billing_service.start_trial_if_needed` при отсутствии триала и наличии ключа вызывался `_upsert_license` со статусом `trial` без проверки, что лицензия уже `lifetime`.

## Root cause (почему произошло)
- Недоучтён порядок: ленивый старт триала не должен иметь приоритета над явным `lifetime`.
- Недостаточно регрессионного теста на сочетание lifetime + ключ + отсутствие trial.

## Исправление (что сделали)
В начале `start_trial_if_needed` добавлен ранний выход при `_is_lifetime`; вспомогательная `_is_lifetime` вынесена выше по файлу. Дополнительно: в `_upsert_license` запрещено понижение со статуса `lifetime` на любой другой (инвариант на уровне БД-апсерта). На фронте `loadBillingStatus` вызывается при смене маршрута, чтобы не залипать на старом ответе `/billing/status`.

## Профилактика (как не повторить)
Unit-тесты `test_start_trial_if_needed_skips_when_lifetime` и `test_upsert_license_never_downgrades_lifetime` в `backend/tests/test_billing_service.py`.

## Проверка
- Команды: `ruff check .`, `mypy .`, `pytest`
- Сценарии: после grant lifetime + WB key статус API `/billing/status` остаётся `subscription_status: lifetime`.

Затронутые файлы: `backend/app/services/billing_service.py`, `backend/tests/test_billing_service.py`, `frontend/src/Layout.jsx`, `frontend/dist/*`, `BUGLOG.md`
---

---
ID: BUG-13
Дата: 2026-05-04
Статус: fixed
Автоматизация: нет (ручной репорт при локальном тесте)

## Бизнес-описание
При загрузке оферты и запуске индексации RAG-блока пользователь видел ошибку, и оферта не становилась доступной для вопросов.

## Процесс / сценарий
1) Загрузить оферту в блоке “AI по оферте WB”.
2) Дождаться индексации.
3) Ожидание: статус становится `ready`, можно задавать вопросы.
4) Факт: индексация падает с ошибкой Qdrant о невалидном `point id`.

## Техническое описание
В `offer_rag_service.index_offer_file` при upsert в Qdrant использовались строковые id вида `<version>:<chunk_id>`.
Qdrant принимает id только как unsigned integer или UUID, поэтому возвращал `400 Bad Request`.

## Root cause (почему произошло)
- Недоучтён контракт Qdrant на тип идентификатора точки (PointId).
- Не было e2e smoke-теста на реальный upsert в Qdrant.

## Исправление (что сделали)
Перевели id точек в Qdrant на детерминированный UUID (uuid5) по `(offer_version, chunk_id)`.

## Профилактика (как не повторить)
Добавить smoke-check сценарий локального стенда: загрузка оферты → статус `ready` → `ask` возвращает sources.

## Проверка
- Команды: `ruff check .`, `mypy .`, `pytest`
- Сценарии: загрузка оферты больше не падает на upsert; после индексации можно задавать вопросы.

Затронутые файлы: `backend/app/services/offer_rag_service.py`, `BUGLOG.md`
---

---
ID: BUG-12
Дата: 2026-04-30
Статус: fixed
Автоматизация: да (pytest: consumed orchestrator step сохраняет intents, добавленные параллельным kick)

## Бизнес-описание
Новый пользователь `alex054x@gmail.com` после регистрации с WB API key видел первичный экран ошибки, что синхронизация не стартовала/зависла. Фактически часть синхронизации выполнилась: воронка и SKU появились, но финансовые данные `pnl_daily` не появились, поэтому дашборд оставался в первичном loader/error состоянии.

## Процесс / сценарий
1) Пользователь регистрируется с WB API key.
2) Frontend после login вызывает `/dashboard/state`; если `has_data=false`, вызывает `/sync/initial`.
3) Ожидание: оба фоновых намерения сохраняются — rolling `funnel_tail` и initial `finance_range`; оркестратор последовательно обрабатывает финансы и воронку.
4) Факт: `/sync/initial` вернул `200 OK`, но в БД остались `raw_sales=0`, `raw_ads=0`, `pnl_daily=0`; при этом `funnel_daily` и `sku_daily` были заполнены за `2026-04-23..2026-04-29`, а `wb_orchestrator_state.intents` стал `{}`.

## Техническое описание
В `wb_orchestrator_tick` tick работал на snapshot `st.intents`, затем после выполнения шага полностью записывал lane через `_intents_with_lane(intents, ...)`. Если параллельно `wb_orchestrator_kick` добавлял в ту же lane новый intent, например `high.finance_range` от `/sync/initial`, завершающийся tick мог перезаписать более свежий DB state старым snapshot и потерять новый intent.

## Root cause (почему произошло)
- Недоучтён race condition между `/dashboard/state` auto-kick и `/sync/initial`.
- Контракт “tick may consume only the work it observed” не был закреплён инвариантом.
- Недостаток теста на сохранение intents, добавленных во время выполнения background step.

## Исправление (что сделали)
`wb_orchestrator_tick` теперь перед сохранением результата шага перечитывает актуальный `wb_orchestrator_state` и удаляет только те ключи, которые были в snapshot и не изменились. Intents, добавленные параллельным `wb_orchestrator_kick`, сохраняются и приводят к следующему tick.

## Профилактика (как не повторить)
Добавлен unit-test, который моделирует consumed `funnel_tail` из старого snapshot и параллельно добавленный `finance_range`; тест гарантирует, что `finance_range` не теряется.

## Проверка
- Команды: `python3 -m pytest backend/tests/test_wb_orchestrator_intents_merge.py -q`, `ruff check .`, `mypy .`, `pytest`
- Сценарии: primary registration flow `/dashboard/state` + `/sync/initial` не должен терять finance intent при параллельном funnel-tail tick.

Затронутые файлы: `backend/celery_app/tasks.py`, `backend/tests/test_wb_orchestrator_intents_merge.py`, `BUGLOG.md`, `TASKLOG.md`
---

---
ID: BUG-11
Дата: 2026-04-30
Статус: fixed
Автоматизация: да (pytest: `wb_orchestrator_kick` будит оркестратор после истекшего cooldown)

## Бизнес-описание
У пользователя `sherin-ivan@ya.ru` на проде не появлялись продажи за последние дни и заказы в rolling-воронке. Дашборд выглядел так, будто новых данных нет, хотя WB-ключ был задан и фоновые заявки на догрузку создавались.

## Процесс / сценарий
1) Пользователь открывает дашборд после нескольких дней работы магазина.
2) `/dashboard/state` находит missing-tail по финансам и/или хвосту воронки.
3) Ожидание: `wb_orchestrator_kick` ставит intent и запускает `wb_orchestrator_tick`, который догружает продажи/рекламу/воронку.
4) Факт: `wb_orchestrator_state.status='cooldown'` оставался после уже истекшего `cooldown_until`, новые kicks только обновляли intents и не ставили tick.

## Техническое описание
В `wb_orchestrator_kick` запуск tick выполнялся только при `status='idle'`. Если Celery ETA-task после WB 429 была потеряна/не выполнилась, persisted state оставался `cooldown` даже после истечения `cooldown_until`. Последующие kicks возвращали `status='cooldown'`, не будили оркестратор и оставляли pending intents.

## Root cause (почему произошло)
- Недоучтён кейс восстановления после потерянной ETA-task/рестарта воркера во время cooldown.
- Контракт `kick -> pending work must eventually wake tick` проверял только `idle`, но не `expired cooldown`.
- Недостаток регрессионного теста на expired cooldown.

## Исправление (что сделали)
`wb_orchestrator_kick` теперь считает `cooldown` с пустым/истекшим `cooldown_until` пробуждаемым состоянием: переводит orchestrator в `scheduled`, сохраняет intents и ставит `wb_orchestrator_tick.delay(user_id)`.

## Профилактика (как не повторить)
Добавлен pytest, который создаёт state `status='cooldown'` с прошедшим `cooldown_until`, вызывает `wb_orchestrator_kick` и проверяет, что tick поставлен, status стал `scheduled`, а intents не потерялись.

## Проверка
- Команды: `pytest backend/tests/test_wb_orchestrator_intents_merge.py -q`, `ruff check .`, `mypy .`, `pytest`
- Сценарии: на проде вручную разбудили `wb_orchestrator_tick` для `sherin-ivan@ya.ru`; `finance_missing_sync_state` для `2026-04-28..2026-04-29` стал `complete`; в `raw_sales` и `pnl_daily` появились строки за `2026-04-28` и `2026-04-29`, в `funnel_daily` появились заказы за `2026-04-27..2026-04-29`.

Затронутые файлы: `backend/celery_app/tasks.py`, `backend/tests/test_wb_orchestrator_intents_merge.py`, `BUGLOG.md`, `TASKLOG.md`
---

---
ID: BUG-10
Дата: 2026-04-28
Статус: fixed
Автоматизация: да (pytest: смена WB API key для active granted store и запрет без grant)

## Бизнес-описание
Пользователь с доступом к чужому магазину мог открыть вкладку с WB API ключом при активном чужом магазине и ожидать, что меняет ключ этого магазина. Фактически ключ записывался в его собственный аккаунт, а последующая первичная синхронизация могла запускаться уже для активного чужого магазина. Это создавало риск повторного смешения кабинетов и неверной загрузки данных.

## Процесс / сценарий
1) Пользователь Viewer получает доступ к магазину Owner.
2) Viewer выбирает магазин Owner в переключателе магазинов.
3) Viewer вводит новый WB API key и сохраняет его.
Ожидание: ключ сохраняется в `users.wb_api_key` владельца активного магазина Owner, а initial sync работает с тем же owner context.
Факт: ключ сохранялся в `users.wb_api_key` Viewer, потому что `/auth/wb-key` игнорировал `X-Store-Owner-Id`.

## Техническое описание
Роут `/auth/wb-key` использовал `get_current_user`, тогда как `/sync/initial` уже использовал `get_store_context`. Из-за разных источников `user_id` возникал контрактный рассинхрон между сохранением ключа и запуском синхронизации.

## Root cause (почему произошло)
- При добавлении multi-store context не все auth endpoints были переведены на active store contract.
- Не было регрессионного теста на смену WB key при `X-Store-Owner-Id` granted store.

## Исправление (что сделали)
`/auth/me` и `/auth/wb-key` теперь используют `get_store_context` и работают с `store_ctx.store_owner`. Проверка доступа к чужому магазину остаётся централизованной: без active grant backend возвращает 403.

## Профилактика (как не повторить)
Добавлены pytest-кейсы: granted viewer меняет ключ active store owner, viewer key не меняется; stranger без grant получает 403 и ключи не меняются.

## Проверка
- Команды: `DATABASE_URL=postgresql://wb_finance:wb_finance@localhost:5433/wb_finance python3 -m pytest backend/tests/test_integration_store_access.py -q`, `ruff check .`, `mypy .`, `pytest`
- Сценарии: смена ключа в активном магазине Иванова; запрет смены ключа магазина без доступа.

Затронутые файлы: `backend/app/routers/auth.py`, `backend/tests/test_integration_store_access.py`, `backend/tests/test_finance_holes_process.py`, `backend/tests/test_integration_dashboard.py`
---

---
ID: BUG-9
Дата: 2026-04-28
Статус: fixed
Автоматизация: да (pytest: pending+idle/stale funnel_tail wakes orchestrator)

## Бизнес-описание
Старые зависшие `funnel_tail` intents могли оставаться у любых пользователей после предыдущего релиза. Если пользователь открывал дашборд, система видела pending intent и не будила оркестратор, поэтому repair мог не продолжиться/не очиститься без ручного вмешательства.

## Процесс / сценарий
1) У пользователя в `wb_orchestrator_state.intents` уже есть `high.funnel_tail=true`.
2) Оркестратор при этом `idle` или данные воронки уже фактически заполнены.
3) Пользователь открывает дашборд.
Ожидание: `/dashboard/state` будит `wb_orchestrator_tick`, чтобы продолжить repair или очистить stale intent.
Факт: endpoint считал pending intent достаточным и ничего не делал.

## Техническое описание
В `_maybe_start_funnel_tail_repair` при `high.funnel_tail=true` был ранний `return False`. Это предотвращало дубли, но также блокировало wake для idle/stale pending state.

## Root cause (почему произошло)
- Дедуп pending intent не отличал “уже выполняется” от “idle, но не запланировано”.
- Проверка полного rolling-окна выполнялась до wake stale intent, поэтому очищать уже закрытые stale states тоже было нечем.

## Исправление (что сделали)
Если `funnel_tail` уже pending и оркестратор `idle`/`scheduled`, `/dashboard/state` теперь ставит `wb_orchestrator_tick.delay(user_id)`. Если intent running — не дублирует. Если данные уже есть, tick очистит consumed intent новым кодом.

## Профилактика (как не повторить)
- Добавлены pytest-кейсы: pending+idle -> wake tick; pending+running -> no duplicate; pending+complete window -> wake tick for cleanup.

## Проверка
- Команды: `pytest backend/tests/test_dashboard_funnel_tail_autostart.py backend/tests/test_wb_orchestrator_intents_merge.py`, `ruff check .`, `mypy .`, `pytest`
- Сценарии: все пользователи со stale/pending `funnel_tail` получают wake при следующем `/dashboard/state`; running intent не дублируется.

Затронутые файлы: `backend/app/routers/dashboard.py`, `backend/tests/test_dashboard_funnel_tail_autostart.py`
---

---
ID: BUG-8
Дата: 2026-04-28
Статус: fixed
Автоматизация: да (pytest: consumed orchestrator lane removes stale intents)

## Бизнес-описание
После успешной починки воронки пользователь мог продолжать видеть состояние “догружаем”, хотя данные уже появились. Это создавало ложное ощущение незавершённой синхронизации.

## Процесс / сценарий
1) `/dashboard/state` ставит `funnel_tail`.
2) Оркестратор выполняет repair, данные за дни появляются в `funnel_daily`.
3) Ожидание: intent очищается, UI перестаёт считать repair pending.
4) Факт: `funnel_tail=true` оставался в `wb_orchestrator_state.intents`.

## Техническое описание
`wb_orchestrator_tick` делал `high.pop(...)`, но затем записывал state через `_intents_merge(...)`. Merge добавляет/перезаписывает ключи, но не удаляет отсутствующие ключи, поэтому stale `funnel_tail`/`finance_range` мог оставаться навсегда.

## Root cause (почему произошло)
- Использовали merge-функцию для операции удаления.
- Не было unit-теста на “consumed intent удалён из lane”.

## Исправление (что сделали)
Добавлен helper `_intents_with_lane`, который заменяет lane целиком или удаляет её, если она пустая. High/low lane после consumed steps теперь записываются через этот helper.

## Профилактика (как не повторить)
- Добавлен pytest: `_intents_with_lane` заменяет lane и удаляет её после consumed intent.

## Проверка
- Команды: `pytest backend/tests/test_wb_orchestrator_intents_merge.py backend/tests/test_dashboard_funnel_tail_autostart.py backend/tests/test_sync_api.py`, `ruff check .`, `mypy .`, `pytest`
- Сценарии: `funnel_tail` выполнен и complete -> high lane очищается; остальные lanes сохраняются.

Затронутые файлы: `backend/celery_app/tasks.py`, `backend/tests/test_wb_orchestrator_intents_merge.py`
---

---
ID: BUG-7
Дата: 2026-04-28
Статус: fixed
Автоматизация: да (pytest: finance complete + funnel missing -> dashboard state kicks funnel_tail)

## Бизнес-описание
После выката auto-repair пользователь мог открыть дашборд, увидеть актуальные финансы за вчера, но “Заказы ₽” оставались нулями, если финансы уже были complete, а `funnel_daily` за вчера/позавчера всё ещё отсутствовала.

## Процесс / сценарий
1) Пользователь открывает дашборд, ничего не нажимает.
2) Финансовый хвост уже закрыт (`finance_missing_sync=complete`).
3) В rolling-окне последних 7 дней есть дырка в `funnel_daily`.
Ожидание: `/dashboard/state` всё равно ставит `funnel_tail` в orchestrator.
Факт: `funnel_tail_sync.pending=false`, потому что запуск был привязан только к обнаружению finance-missing.

## Техническое описание
В `backend/app/routers/dashboard.py` `funnel_tail` ставился вместе с `finance_range`, но не было отдельной проверки “финансы есть, воронки нет”. Из-за этого `_orch_funnel_tail_step` не запускался для уже закрытого финансового хвоста.

## Root cause (почему произошло)
- Scenario contract был неполным: проверили “finance missing -> finance+funnel”, но не проверили “finance complete -> funnel missing”.
- Не было непропускаемого unit/contract теста на funnel-only gap.

## Исправление (что сделали)
Добавлена `_maybe_start_funnel_tail_repair`: `/dashboard/state` проверяет rolling-окно `funnel_daily` и будит `wb_orchestrator_kick` с `{"high": {"funnel_tail": true}}`, если есть пропуск и intent ещё не pending.

## Профилактика (как не повторить)
- Добавлен pytest `test_dashboard_funnel_tail_autostart.py`: finance complete + funnel missing -> ставится `funnel_tail`; уже pending intent не дублируется.

## Проверка
- Команды: `pytest backend/tests/test_dashboard_funnel_tail_autostart.py backend/tests/test_sync_api.py backend/tests/test_wb_orchestrator_intents_merge.py`, `ruff check .`, `mypy .`, `pytest`
- Сценарии: вход в дашборд без ручных действий; финансы complete, воронка missing -> orchestrator получает `funnel_tail`.

Затронутые файлы: `backend/app/routers/dashboard.py`, `backend/tests/test_dashboard_funnel_tail_autostart.py`, `backend/tests/test_integration_dashboard.py`
---

---
ID: BUG-6
Дата: 2026-04-28
Статус: fixed
Автоматизация: да (pytest: dashboard state queues finance+funnel orchestrator intent; frontend lint/build)

## Бизнес-описание
Пользователь открывал дашборд, финансовые данные за вчера могли догрузиться, но “Заказы ₽” оставались нулями, потому что хвост воронки не запускался и таблица не перечитывалась после фонового repair.

## Процесс / сценарий
1) Пользователь заходит в дашборд.
2) `/dashboard/state` обнаруживает пропущенные финансовые дни и ставит догрузку финансов.
3) Ожидание: после финансового хвоста система последовательно чинит rolling-хвост воронки за последние 7 дней, а UI polling перечитывает данные.
4) Факт: `/dashboard/state` ставил только прямую finance-задачу, воронка не попадала в тот же последовательный flow, а фронт не ждал завершения repair.

## Техническое описание
В `backend/app/routers/dashboard.py` warm-path `/dashboard/state` вызывал `sync_finance_missing_range.delay(...)` напрямую. Этот путь обходил `wb_orchestrator_kick` с `funnel_tail`, поэтому `wb_orchestrator_tick` не выполнял `_orch_funnel_tail_step`. Во фронтенде `Layout.jsx` не polling-ил состояние `finance_missing_sync`/`funnel_tail_sync` до завершения, поэтому таблица P&L могла остаться с устаревшими `funnelRows`.

## Root cause (почему произошло)
- Контракт “вход в дашборд → финансы → хвост воронки → видимый результат” не был зафиксирован тестом.
- Warm-path входа в дашборд остался на прямой legacy-задаче финансов, а не на едином orchestrator intent.
- UI обновлял таблицу раньше, чем фоновые задачи могли дописать `funnel_daily`.

## Исправление (что сделали)
`/dashboard/state` теперь ставит warm-path через `wb_orchestrator_kick` с high-intent `finance_range + funnel_tail`. В ответ state добавлен `funnel_tail_sync`, а фронт polling-ит state и refresh-ит таблицы, пока активны finance/funnel tail repair.
Кнопка ручного `Обновить WB` скрыта из интерфейса, чтобы пользовательский сценарий не зависел от ручного запуска и не создавал отдельный путь синхронизации.

## Профилактика (как не повторить)
- Pytest обновлён: dashboard-entry теперь проверяет постановку orchestrator intent с `finance_range` и `funnel_tail`.
- UI получает явный `funnel_tail_sync`, чтобы не терять фоновые repair-процессы.

## Проверка
- Команды: `pytest backend/tests/test_integration_dashboard.py backend/tests/test_sync_api.py backend/tests/test_funnel_tail_repair_task.py backend/tests/test_wb_orchestrator_intents_merge.py`, `ruff check .`, `mypy .`, `pytest`, `npm run lint`, `npm run build`
- Сценарии: вход в дашборд с missing finance tail → ставится один orchestrator high-intent finance+funnel; UI polling активен до завершения finance/funnel tail; ручная кнопка WB отсутствует; YTD-autostart не возвращался.

Затронутые файлы: `backend/app/routers/dashboard.py`, `backend/tests/test_integration_dashboard.py`, `frontend/src/Layout.jsx`, `frontend/src/components/Topbar.jsx`, `frontend/src/screens/Costs.jsx`, `frontend/dist/index.html`, `frontend/dist/assets/index-CLO5BLTY.js`
---

---
ID: BUG-5
Дата: 2026-04-26
Статус: fixed
Автоматизация: да (pytest: covered pnl_daily chunk skips WB calls)

## Бизнес-описание
Архивная догрузка могла повторно грузить уже заполненные месяцы, создавая лишние обращения к WB и повышая риск 429 без пользы для пользователя.

## Процесс / сценарий
1) Архивный backfill идёт по месячным чанкам.
2) Даже если за месяц уже заполнена витрина P&L, процесс всё равно делал WB-запросы и пересчёты.
Ожидание: если период уже заполнен, архив к нему не возвращается.
Факт: выполнялись повторные запросы “на всякий случай”.

## Техническое описание
В `wb_orchestrator_tick` low-lane `finance_backfill_year` всегда выполнял `sync_sales`/`sync_ads` и пересчёты для чанка.

## Root cause (почему произошло)
- Backfill был реализован как “перезаливка” по диапазону без cheap-check “уже покрыто”.
- Не было регрессионного теста, фиксирующего политику “архив грузим только если данных нет”.

## Исправление (что сделали)
Добавили проверку покрытия по `pnl_daily` на диапазон чанка: если в таблице есть строки на каждый день периода, WB-вызовы пропускаются, курсор backfill продвигается дальше.

## Профилактика (как не повторить)
- Pytest: covered период → оркестратор не вызывает WB синки и пересчёты.

## Проверка
- Команды: `ruff check .`, `mypy .`, `pytest`
- Сценарии: архивный backfill с уже заполненным месяцем → нет WB-вызовов, курсор двигается; незаполненный месяц → выполняется загрузка как раньше.

Затронутые файлы: `backend/celery_app/tasks.py`, `backend/tests/test_orchestrator_finance_backfill_skip_if_covered.py`
---

---
ID: BUG-4
Дата: 2026-04-26
Статус: fixed
Автоматизация: да (pytest: dashboard state no longer autostarts finance backfill)

## Бизнес-описание
Открытие дашборда могло незаметно запускать тяжелую догрузку архива (backfill), что приводило к лишней нагрузке на WB и очередь задач без явного действия пользователя.

## Процесс / сценарий
1) Пользователь (или viewer через grant) открывает дашборд → фронт опрашивает `/dashboard/state`.
2) Сервер по условиям автозапуска ставит в Celery долгоживущую цепочку backfill.
Ожидание: чтение состояния не запускает тяжелые фоновые процессы; архив догружается управляемо и дозировано.
Факт: backfill мог стартовать “сам” при простом открытии/refresh.

## Техническое описание
`backend/app/routers/dashboard.py` вызывал `sync_finance_backfill_step.delay(...)` из обработчика `/dashboard/state`.

## Root cause (почему произошло)
- Backfill был привязан к UI-read endpoint для “удобства автостарта”.
- Legacy self-chain backfill существовал параллельно оркестратору и обходил приоритеты/high-lane.

## Исправление (что сделали)
- Убрали запуск backfill из `/dashboard/state` (endpoint стал read-only по смыслу).
- Добавили дозирующий менеджер `archive_backfill_manager` (celery_beat) для постепенной догрузки архива через intents/оркестратор.

## Профилактика (как не повторить)
- Регрессионные тесты: `/dashboard/state` не запускает backfill; backfill запускается отдельно.

## Проверка
- Команды: `ruff check .`, `mypy .`, `pytest`
- Сценарии: открыть дашборд/refresh → не появляется backfill chain в очереди; архив догружается только через менеджер/кнопку.

Затронутые файлы: `backend/app/routers/dashboard.py`, `backend/celery_app/tasks.py`, `backend/celery_app/celery.py`, `backend/tests/test_integration_dashboard.py`, `backend/tests/test_finance_backfill_job_invariants.py`, `backend/tests/test_integration_store_access.py`
---

---
ID: BUG-3
Дата: 2026-04-26
Статус: fixed
Автоматизация: да (pytest: sync endpoints use orchestrator; intents merge)

## Бизнес-описание
При догрузках (2025/2026) и автосинке могли создаваться пачки фоновых задач на WB, которые “просыпались” одновременно после лимита и снова забивали WB, из‑за чего пользователь видел “попробуем через X минут” и данные за вчера не приходили стабильно.

## Процесс / сценарий
1) Фоновая догрузка 2025/2026 ставит задачи по месяцам.
2) WB отвечает 429 → каждая задача отдельно планирует retry.
3) После снятия лимита задачи просыпаются разом → снова 429 → очередь раздувается.
Ожидание: один последовательный поток запросов с единым cooldown и без fan-out в очереди.
Факт: независимые задачи и независимые ретраи приводили к “шторма” запросов.

## Техническое описание
`backend/app/routers/sync.py` создавал много задач (месячные чанки и chord), а retry на 429 происходил на уровне отдельных задач, что приводило к thundering herd.

## Root cause (почему произошло)
- Оркестрация была распределена по множеству Celery задач; не было единого “single-flight + cooldown per seller”.
- При 429 ретраи планировались каждой задачей отдельно, без глобального gate.

## Исправление (что сделали)
- Добавлен единый оркестратор `wb_orchestrator_tick` и state `wb_orchestrator_state`.
- Роуты `/sync/*` больше не ставят пачки задач; они записывают intents и будят оркестратор.
- При 429 оркестратор ставит общий cooldown и ретраит только себя.

## Профилактика (как не повторить)
- Регрессионные pytest: backfill/initial/recent больше не fan-out; проверка merge intents.

## Проверка
- Команды: `ruff check .`, `mypy .`, `pytest`
- Сценарии: backfill 2026/2025 (без пачек задач), автосинк recent/initial (через intents), 429 → единый cooldown.

Затронутые файлы: `backend/app/routers/sync.py`, `backend/celery_app/tasks.py`, `backend/app/models/wb_orchestrator_state.py`, `backend/alembic/versions/6aec37706b6b_wb_orchestrator_state.py`, `backend/tests/test_sync_api.py`, `backend/tests/test_wb_orchestrator_intents_merge.py`
---

---
ID: BUG-2
Дата: 2026-04-25
Статус: fixed
Автоматизация: да (Playwright e2e: plan-fact derived cogs)

## Бизнес-описание
В “План-факт” поле “Себес” в плане можно было вводить вручную и оно не считалось от “Доля себеса”, из‑за чего плановые метрики расходились и ввод требовал лишних действий.

## Процесс / сценарий
1) Пользователь включает “План-факт” → “Изменить план”.
2) Вводит “Доля себеса”.
Ожидание: “Себес” пересчитывается автоматически как выручка × доля себеса и недоступен для ручного ввода.
Факт: “Себес” был редактируемым и не обязан был соответствовать доле.

## Техническое описание
`frontend/src/screens/Dashboard.jsx`: в блоке plan-fact “Себес” (`cogs`) помечался editable и не вычислялся из `revenue` и `cogs_share`.

## Root cause (почему произошло)
- Не был зафиксирован контракт “derived field” для `cogs` в плане и не было регрессионного e2e на автопересчёт.

## Исправление (что сделали)
- Сделали `cogs` read-only в плане и вычисляемым: `cogs = revenue * (cogs_share / 100)` при вводе выручки/доли, а также при гидратации/refresh планов с сервера.

## Профилактика (как не повторить)
- Добавлен Playwright e2e тест на disabled-инпут `cogs` и корректный автопересчёт.

## Проверка
- Команды: `npm run lint`, `npm run build`, `npx playwright test`
- Сценарии: “План-факт → Изменить план → ввести долю себеса → увидеть рассчитанный себес”

Затронутые файлы: `frontend/src/screens/Dashboard.jsx`, `frontend/e2e/dashboard-table.spec.js`
---

ID: BUG-1
Дата: 2026-04-25
Статус: template
Автоматизация: нет (шаблон)

## Бизнес-описание
<что видел пользователь / какой ущерб / почему важно>

## Процесс / сценарий
<пошагово: входные данные, действия, ожидаемое поведение, фактическое поведение>

## Техническое описание
<где в коде было, какие компоненты/модули, какой контракт нарушен>

## Root cause (почему произошло)
- <недоучтён кейс / противоречивое требование / инфра / тайминги / ретраи / лимиты / отсутствие теста>

## Исправление (что сделали)
<минимальный фикс: что поменяли и почему этого достаточно>

## Профилактика (как не повторить)
<тест/инвариант/алерт/ограничение/изменение контракта>

## Проверка
- Команды: <ruff/mypy/pytest/...>
- Сценарии: <happy/error/retry path>

Затронутые файлы: <список путей>
---

