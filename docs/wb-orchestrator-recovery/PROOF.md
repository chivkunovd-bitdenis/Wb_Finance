# Проверки восстановления WB Finance — 18.10.2026

## До изменения production

Установленный код: `28a633ea074b784a3614ed24d5c6e23e00f1e700` (image label и
bind-mounted source совпадают). Redis: Celery 5.6.3 / Kombu 5.6.2; приложение,
worker и beat остановлены. В ready: 257838 `wb_orchestrator_tick`, одно
`billing_send_reminders`, три `archive_backfill_manager`; в unacked: три тика.
Все тики относятся к одному владельцу с существующими непустыми intents.
Другой Redis DB с image pipeline не входит в очистку.

Приватные холодные копии томов сделаны при остановленных Redis/PostgreSQL:
- Redis archive: 50538678 bytes, SHA256 `3b0ef5227996c5fab2fee5df5c52b0253057cb14e35788c63839313b28a86f46`.
- PostgreSQL archive: 115181431 bytes, SHA256 `854518f5c7e04263b66427f07984e5dd17b013731d8628623a8df40e4cfa57ee`.
- Дополнительный pg_dump: SHA256 `2bae405f772957f5abd8357a381aa70bdd5c494cfe2da1a8ea24c80db7e16c17`.

Архивы и исходные сообщения остаются в приватном каталоге сервера, не в Git.

## Локальная проверка и независимое ревью

Реальная отдельная PostgreSQL: конкурентные dispatch/kick, исключение одновременных
шагов, 100 устаревших/legacy сообщений, восстановление lease/publish failure,
новая доступная операция при 429, сохранение pacing deadline. Целевой набор:
53 passed до добавления дополнительной проверки pacing; отдельный итоговый
scheduler набор после неё: 6 passed (независимое повторение ревьюером).

Реальные изолированные Redis-процессы: `tests/test_recover_wb_orchestrator_queue.py`
— 15 passed. Проверены mixed ready/unacked/priority, байты/порядок/индекс, неизвестный
формат, отсутствующее состояние, изменение снимка, режим dry-run и private backup.
Ruff двух новых recovery файлов: All checks passed.

Миграции выполнены в отдельной scratch БД: baseline до d8e9f0a1b2c3, upgrade до
e3b8f4a12c90, downgrade/upgrade. Хеши исходных intents сохранились; pending row получает
срок, metadata-only row не запускается. Диспетчер при будущем cooldown отправил 0 задач.

Независимое ревью: APPROVE WITH WARNINGS. При длинном шаге и замене lease продолжение
может задержаться до пяти минут; параллельное выполнение блокируется отдельной PG lock.

## Live-проверка

На момент этого коммита очистка и запуск ещё не выполнялись. Их результат, проверенный
SHA и фактическое окно наблюдения будут добавлены после CI и применения.
