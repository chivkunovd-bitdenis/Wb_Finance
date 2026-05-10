# AI-MVP1 — Scenario contract: Task/Hypothesis + базовые API

## User action
- Пользователь открывает раздел “ИИ модуль → Задачи/Гипотезы”.
- Пользователь:
  - просматривает список задач/гипотез,
  - открывает детальную карточку,
  - меняет статус задачи (например, “в работе”, “выполнено”),
  - запускает гипотезу (start) и завершает (finish) по итогу тест-периода.

## Expected visible result
- В списке отображаются задачи и гипотезы пользователя с актуальными статусами.
- После смены статуса в UI/клиенте сразу видно новый статус и временные метки (started/completed/ended).
- “Запуск гипотезы” недоступен, если гипотеза уже запущена/завершена.

## System steps (backend)
- Авторизация пользователя.
- CRUD-read по сущностям `Task` и `Hypothesis` с фильтром `user_id=current_user.id`.
- Валидация допустимых переходов статусов.
- Запись изменений статуса и временных меток в БД.

## Frontend call(s)
Пока UI не реализован, контракт фиксируем как HTTP API:
- GET `/ai/tasks`
- GET `/ai/tasks/{task_id}`
- PATCH `/ai/tasks/{task_id}` (смена статуса)
- GET `/ai/hypotheses`
- GET `/ai/hypotheses/{hypothesis_id}`
- POST `/ai/hypotheses/{hypothesis_id}/start`
- POST `/ai/hypotheses/{hypothesis_id}/finish`

## Backend endpoint(s)
См. выше; все требуют auth и работают только в рамках `current_user`.

## Background process(es)
- В MVP-1 фоновых процессов нет: сущности могут создаваться вручную (в тестах) или позже сервисом ежедневной аналитики (AI-MVP3).

## DB/state changes
- Таблица `ai_tasks`:
  - создание записи (позже — аналитикой),
  - смена `status`, установка `started_at`/`completed_at`.
- Таблица `ai_hypotheses`:
  - смена `status`, установка `started_at`/`ended_at`,
  - хранение `daily_log` и `result_summary` (может быть пустым на MVP-1).

## What must NOT happen
- Пользователь не должен видеть/менять задачи и гипотезы другого пользователя.
- Нельзя поставить “completed” без корректного перехода (например, из `new` → `completed` может быть запрещено, если мы фиксируем промежуточный `in_progress`).
- Нельзя запускать гипотезу второй раз, если она уже `running`/`finished`.

## Happy-path verification
- Создать в БД задачу и гипотезу для пользователя.
- GET список → вернуть записи.
- PATCH задачу `new` → `in_progress` → `completed`, проверить метки времени.
- POST start гипотезы → статус `running`, `started_at` заполнен.
- POST finish гипотезы → статус `finished`, `ended_at` заполнен.

## Error/retry verification
- 401 без токена.
- 404 если `task_id`/`hypothesis_id` не существует или принадлежит другому пользователю.
- 400/409 на недопустимые переходы статусов (например, start уже запущенной гипотезы).

