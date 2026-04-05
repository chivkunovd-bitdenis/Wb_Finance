# Деплой WB Finance на сервер

## Что есть в проекте

| Компонент | Путь | Куда деплоится |
|---|---|---|
| Лендинг (HTML) | `landing/` | `sellerfocus.pro` |
| React-приложение | `frontend/` | `app.sellerfocus.pro` |
| FastAPI backend | `backend/` | `api:8000` (внутри docker) |
| Celery worker | `backend/celery_app/` | внутри docker |
| Caddy (reverse proxy) | `Caddyfile` | порты 80/443 |

## Стек деплоя

- Docker + Docker Compose
- Caddy 2 — автоматически получает TLS-сертификат (Let's Encrypt)
- Postgres + Redis — внутри docker
- Лендинг — статический HTML, картинки в `landing/`

---

## Шаг 1. Подготовка сервера

На сервере должны быть установлены:
- `docker` >= 24
- `docker compose` >= 2

```bash
# Проверить
docker --version
docker compose version
```

---

## Шаг 2. Загрузить код на сервер

### Вариант A — через git (рекомендуется)

```bash
git clone <repo-url> /opt/wb-finance
cd /opt/wb-finance
```

### Вариант B — через rsync (если нет git)

```bash
rsync -avz --exclude='.git' --exclude='node_modules' --exclude='__pycache__' \
  /Users/deniscivkunov/wb-finance/ user@server:/opt/wb-finance/
```

---

## Шаг 3. Настроить переменные окружения

```bash
cd /opt/wb-finance
cp backend/.env.example backend/.env
nano backend/.env
```

Обязательно заполнить:
- `SECRET_KEY` — случайная строка (можно `openssl rand -hex 32`)
- `WB_API_KEY` — ключ от Wildberries
- `DATABASE_URL` — уже прописан для docker: `postgresql://wb_finance:wb_finance@postgres:5432/wb_finance`
- `REDIS_URL` — уже прописан: `redis://redis:6379/0`

**Для оплаты подписки (ЮKassa):**
- `YOOKASSA_SHOP_ID` — идентификатор магазина из личного кабинета ЮKassa
- `YOOKASSA_SECRET_KEY` — секретный ключ API
- `YOOKASSA_RETURN_URL` — опционально, запасной URL возврата после оплаты (если фронт не передаёт `return_url`)
- `YOOKASSA_WEBHOOK_SECRET` — опционально; если задан, заголовок `X-Webhook-Secret` входящего запроса на `POST /billing/webhook/yookassa` должен совпадать с этим значением  
  В личном кабинете ЮKassa укажите URL уведомлений: `https://app.<ваш-домен>/billing/webhook/yookassa` (или тот путь, который отдаёт Caddy на API).

**Для AI-сводки (daily brief):**
- `AI_API_KEY` — ключ OpenAI (или совместимого провайдера: Deepseek, Azure и т.п.)
- `AI_API_BASE_URL` — по умолчанию `https://api.openai.com/v1`, для Deepseek: `https://api.deepseek.com/v1`
- `AI_MODEL` — по умолчанию `gpt-4o-mini`
- `AI_TIMEOUT_SEC` — таймаут в секундах (по умолчанию 120)
- `AI_MAX_TOKENS` — максимум токенов в ответе (по умолчанию 900)

---

## Шаг 4. Собрать фронтенд

Фронтенд нужно собрать **один раз перед деплоем** (или при каждом обновлении):

```bash
cd /opt/wb-finance/frontend
npm ci
npm run build
# Результат: frontend/dist/ — эта папка монтируется в Caddy
```

Если на сервере нет Node.js, соберите локально и залейте `frontend/dist/` на сервер:

```bash
# Локально:
cd frontend && npm ci && npm run build

# Затем скопировать dist на сервер:
rsync -avz frontend/dist/ user@server:/opt/wb-finance/frontend/dist/
```

---

## Шаг 5. Проверить DNS

Убедитесь, что домены смотрят на IP сервера:

```
sellerfocus.pro      A  <IP сервера>
www.sellerfocus.pro  A  <IP сервера>
app.sellerfocus.pro  A  <IP сервера>
```

Caddy сам получит TLS-сертификаты при первом запуске (нужен открытый порт 80 и 443).

---

## Шаг 6. Запустить

```bash
cd /opt/wb-finance

# Первый запуск — собрать образы и поднять всё
docker compose up -d --build

# Проверить, что всё запустилось
docker compose ps
```

Ожидаемый результат — все сервисы в статусе `running`:
- `postgres`
- `redis`
- `api`
- `celery_worker`
- `celery_beat` — **обязателен** для ежедневной AI-сводки (запускает генерацию в 07:00 и 09:00 МСК)
- `caddy`

---

## Шаг 7. Миграции базы данных

Контейнер **`api`** при каждом запуске сначала выполняет **`alembic upgrade head`**, затем стартует uvicorn (см. `command` в `docker-compose.yml`). Отдельный шаг после первого `docker compose up` обычно не нужен.

Если контейнер `api` не запущен или нужно прогнать миграции вручную:

```bash
docker compose exec api alembic upgrade head
```

### Ошибка: `overlaps with other requested revisions` (в т.ч. `c3d4e5f6a7b8` и `f1e2d3c4b5a6`)

Это почти всегда значит: в **`alembic_version` несколько строк**, а ревизии лежат на **одной цепочке** (например, уже записали и родителя `f1e2d3c4b5a6`, и потомка `c3d4e5f6a7b8`). Тогда и `upgrade head`, и `upgrade heads` падают.

**1.** Убедитесь, что на сервере актуальный `docker-compose.yml` с **`alembic upgrade head`** (не `heads`): `git pull`.

**2.** Посмотреть версии и колонку `tax_rate` (миграция `c3d4e5f6a7b8` добавляет её в `users`):

```bash
docker compose exec postgres psql -U wb_finance -d wb_finance -c "SELECT * FROM alembic_version;"
docker compose exec postgres psql -U wb_finance -d wb_finance -c "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='users' AND column_name='tax_rate';"
```

**3.** Привести таблицу к **одной** строке (перед правкой — бэкап БД, если есть ценные данные):

- Если колонка **`tax_rate` уже есть** — схема соответствует ревизии **`c3d4e5f6a7b8`**. Тогда:

```bash
docker compose exec postgres psql -U wb_finance -d wb_finance -c "DELETE FROM alembic_version; INSERT INTO alembic_version (version_num) VALUES ('c3d4e5f6a7b8');"
```

- Если **`tax_rate` нет** — оставьте в `alembic_version` только **`f1e2d3c4b5a6`** (удалите лишние строки), затем поднимите API — выполнится миграция на `c3d4e5f6a7b8`:

```bash
docker compose exec postgres psql -U wb_finance -d wb_finance -c "DELETE FROM alembic_version; INSERT INTO alembic_version (version_num) VALUES ('f1e2d3c4b5a6');"
```

**4.** Перезапуск:

```bash
docker compose up -d api
docker compose logs api --tail 30
```

Ожидается одна строка Alembic без `FAILED`, затем `Application startup complete` / Uvicorn.

---

## Шаг 8. Проверить

- `https://sellerfocus.pro` — лендинг
- `https://app.sellerfocus.pro` — React-приложение
- `https://app.sellerfocus.pro/docs` — Swagger API (если включён)

---

## Обновление после изменений

### Обновить лендинг (только HTML/картинки)

```bash
# Загрузить новые файлы в landing/ на сервер
# Caddy сразу отдаёт новые файлы — перезапуск не нужен
rsync -avz landing/ user@server:/opt/wb-finance/landing/
```

### Обновить фронтенд

```bash
# Пересобрать локально, залить dist
cd frontend && npm run build
rsync -avz frontend/dist/ user@server:/opt/wb-finance/frontend/dist/
```

### Обновить backend

```bash
cd /opt/wb-finance
git pull
docker compose up -d --build api celery_worker celery_beat
docker compose exec api alembic upgrade head
```

---

## Структура Caddyfile (уже настроена)

```
sellerfocus.pro, www.sellerfocus.pro {
    root * /var/www/landing
    file_server
}

app.sellerfocus.pro {
    @api path /auth/* /sync/* /dashboard/* /health ...
    handle @api { reverse_proxy api:8000 }
    handle {
        root * /var/www/app
        try_files {path} /index.html
        file_server
    }
}
```

---

## Если нужно поменять домен

1. Изменить `Caddyfile` — заменить `sellerfocus.pro` на новый домен
2. Изменить DNS записи
3. Перезапустить Caddy: `docker compose restart caddy`

---

## Логи и отладка

```bash
# Логи всех сервисов
docker compose logs -f

# Только api
docker compose logs -f api

# Только caddy
docker compose logs -f caddy

# Перезапустить один сервис
docker compose restart api
```
