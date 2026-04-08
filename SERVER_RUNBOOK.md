## Server runbook (wb-finance)

Цель: чтобы деплой/отладка не начинались с «где папка проекта».

### Источники
- **Текущая точка правды по VPS**: `SERVER_ZURICH.md`
- **Общий гайд деплоя**: `DEPLOY.md` (в нём местами фигурирует `/opt/wb-finance`, но на реальном VPS сейчас используется другой путь)
- **Транскрипт, где использовали серверные команды/пути**: [DB access via Docker env](e88a7f21-5f35-459c-ae48-0ef41652cd05)

---

### 1) VPS / SSH
- **Host/IP**: `194.87.96.144` (см. `SERVER_ZURICH.md`)
- **SSH user**: обычно `root`

Пример:

```bash
ssh root@194.87.96.144
```

---

### 2) Канонический путь проекта на сервере
**Проект на VPS лежит здесь:**
- **`/root/wb-finance`**

Быстрая проверка на сервере:

```bash
cd /root/wb-finance
ls -la
```

Должно быть:
- **`.git/`** (иначе `git pull` будет падать)
- `docker-compose.yml` или `compose.yml` (или `docker compose config` должен работать)

---

### 3) Одна команда для обновления (pull + build + миграции + статус)
Выполнять **на сервере** из `/root/wb-finance`:

```bash
cd /root/wb-finance \
  && git pull \
  && docker compose up -d --build api celery_worker celery_beat \
  && docker compose exec -T api alembic upgrade head \
  && docker compose ps
```

Если нужно обновить всё сразу:

```bash
cd /root/wb-finance && git pull && docker compose up -d --build && docker compose ps
```

---

### 4) Где env и конфигурация
По `DEPLOY.md` окружение обычно хранится в `backend/.env` (внутри папки проекта):

```bash
cd /root/wb-finance
ls -la backend/.env backend/.env.example 2>/dev/null || true
```

Если приложение поднято в Docker, финальные значения проще всего смотреть в окружении контейнеров:

```bash
docker compose exec -T api env | sort
docker compose exec -T postgres env | sort
```

Важно: **не копипастить секреты в чат**.

---

### 5) Логи и диагностика (быстро)

```bash
cd /root/wb-finance
docker compose ps
docker compose logs -f --tail 100 api
docker compose logs -f --tail 100 caddy
docker compose logs -f --tail 100 celery_worker
```

---

### 6) Postgres “без поиска” (внутри docker compose)

Проверить, что база жива:

```bash
cd /root/wb-finance
docker compose exec -T postgres psql -U wb_finance -d wb_finance -c "SELECT 1;"
```

Показать текущие ревизии alembic (миграции):

```bash
cd /root/wb-finance
docker compose exec -T api alembic current
docker compose exec -T api alembic heads
```

---

### 7) Оптимизация “навсегда” (чтобы `cd` не вспоминать)
Сделать канонический alias на сервере (один раз):

```bash
cat >>~/.bashrc <<'EOF'
alias wb='cd /root/wb-finance'
alias wbd='cd /root/wb-finance && git pull && docker compose up -d --build && docker compose ps'
EOF
source ~/.bashrc
```

После этого деплой одной командой:

```bash
wbd
```

Альтернатива: сделать symlink `/srv/wb-finance -> /root/wb-finance`, чтобы путь был “красивый”:

```bash
ln -sfn /root/wb-finance /srv/wb-finance
```

