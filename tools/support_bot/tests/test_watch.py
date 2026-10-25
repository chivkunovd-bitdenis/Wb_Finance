from support_fakes import (
    CLIENT_CHAT,
    OTHER_CHAT,
    OWNER_ID,
    STAFF_ID,
    text_update,
    voice_update,
)

from wbf_support_bot.messages import VOICE_PREFIX, parse_message
from wbf_support_bot.poller import Poller
from wbf_support_bot.watch import (
    EXIT_BOT_DOWN,
    EXIT_OK,
    WatchSettings,
    read_watermark,
    run_watch,
    write_watermark,
)

SETTINGS = WatchSettings(
    quiet_sec=45,
    max_wait_sec=600,
    poll_sec=20,
    heartbeat_max_age_sec=180,
    promise_age_sec=3600,
)
SHORT_WAIT = WatchSettings(quiet_sec=45, max_wait_sec=100, poll_sec=20)


def _alive(store, clock):
    """Пульс бота: heartbeat обновляется при каждом «сне» часов, как это делает run."""
    store.set_kv("heartbeat", str(clock.now))
    clock.on_sleep = lambda: store.set_kv("heartbeat", str(clock.now))


def _ingest(config, store, telegram, clock, *updates):
    telegram.update_batches = [list(updates)]
    Poller(config=config, store=store, api=telegram, transcriber=None, clock=clock).poll_once()


def _watch(config, store, clock, settings=SETTINGS):
    lines: list[str] = []
    code = run_watch(
        config=config,
        store=store,
        settings=settings,
        clock=clock,
        sleep=clock.sleep,
        out=lines.append,
    )
    return code, "\n".join(lines)


def test_first_run_treats_existing_messages_as_seen(config, store, telegram, clock):
    _alive(store, clock)
    _ingest(config, store, telegram, clock, text_update(1, 10, text="Старое"))
    code, out = _watch(config, store, clock, SHORT_WAIT)
    assert code == EXIT_OK
    assert "тишина" in out
    assert "Старое" not in out
    assert read_watermark(config.state_dir) == store.max_message_id()


def test_batch_is_printed_after_quiet_period_and_watermark_moves(config, store, telegram, clock):
    _alive(store, clock)
    write_watermark(config.state_dir, 0)
    _ingest(
        config,
        store,
        telegram,
        clock,
        text_update(1, 10, text="Первое сообщение"),
        text_update(2, 11, text="Второе сообщение"),
    )
    code, out = _watch(config, store, clock)
    assert code == EXIT_OK
    assert "НОВЫЕ СООБЩЕНИЯ КЛИЕНТОВ: 2" in out
    assert "Первое сообщение" in out and "Второе сообщение" in out
    assert "Ромашка" in out
    assert "Ответить: --chat" in out
    assert sum(clock.sleeps) >= 45  # подождали тишину перед выводом
    assert read_watermark(config.state_dir) == store.max_message_id()


def test_delivered_messages_are_not_printed_again(config, store, telegram, clock):
    _alive(store, clock)
    write_watermark(config.state_dir, 0)
    _ingest(config, store, telegram, clock, text_update(1, 10, text="Однажды"))
    _watch(config, store, clock)
    code, out = _watch(config, store, clock, SHORT_WAIT)
    assert code == EXIT_OK
    assert "тишина" in out
    assert "Однажды" not in out


def test_owner_and_staff_messages_are_not_client_messages(config, store, telegram, clock):
    _alive(store, clock)
    write_watermark(config.state_dir, 0)
    _ingest(
        config,
        store,
        telegram,
        clock,
        text_update(1, 10, text="Сообщение владельца", author_id=OWNER_ID),
        text_update(2, 11, text="Сообщение сотрудника", author_id=STAFF_ID),
    )
    code, out = _watch(config, store, clock, SHORT_WAIT)
    assert "тишина" in out
    assert "Сообщение владельца" not in out
    assert "Сообщение сотрудника" not in out


def test_message_from_chat_outside_config_is_not_shown(config, store, telegram, clock):
    _alive(store, clock)
    write_watermark(config.state_dir, 0)
    _ingest(
        config, store, telegram, clock, text_update(1, 10, chat_id=OTHER_CHAT, text="Чужой чат")
    )
    code, out = _watch(config, store, clock, SHORT_WAIT)
    assert "тишина" in out
    assert "Чужой чат" not in out


def test_batch_waits_for_voice_transcription(config, store, clock):
    _alive(store, clock)
    write_watermark(config.state_dir, 0)
    record = parse_message(voice_update(1, 10)["message"], transcribe_voice=True)
    store.save_message(record, edited=False)
    row_id = store.chat_messages(CLIENT_CHAT, 10)[0].id

    def finish_transcription():
        store.set_kv("heartbeat", str(clock.now))
        if len(clock.sleeps) == 1:
            store.set_transcription(
                row_id, text=VOICE_PREFIX + "Когда будет отчёт", status="ok", attempts=1
            )

    clock.on_sleep = finish_transcription
    code, out = _watch(config, store, clock)
    assert code == EXIT_OK
    assert "Когда будет отчёт" in out
    assert "расшифровывается" not in out


def test_missing_heartbeat_reports_bot_not_running(config, store, clock):
    code, out = _watch(config, store, clock, SHORT_WAIT)
    assert code == EXIT_BOT_DOWN
    assert "БОТ НЕ ЗАПУЩЕН" in out


def test_stale_heartbeat_reports_bot_not_running(config, store, clock):
    store.set_kv("heartbeat", str(clock.now - 200))
    code, out = _watch(config, store, clock, SHORT_WAIT)
    assert code == EXIT_BOT_DOWN
    assert "БОТ НЕ ЗАПУЩЕН" in out


def _insert_sent(store, *, key, chat_id, text, sent_at):
    store.insert_outbox_pending(
        key=key, chat_id=chat_id, text=text, reply_to_msg_id=None, file_path=None, created_at=1
    )
    store.claim_outbox(key)
    store.finish_outbox(key, status="sent", tg_message_ids=[77], sent_at=sent_at)


def test_overdue_promise_is_alerted_once(config, store, clock):
    _alive(store, clock)
    write_watermark(config.state_dir, 0)
    _insert_sent(
        store,
        key="promise-1",
        chat_id=CLIENT_CHAT,
        text="Разбираемся с вопросом, вернусь с ответом",
        sent_at=int(clock.now) - 3700,
    )
    code, out = _watch(config, store, clock)
    assert code == EXIT_OK
    assert "ОБЕЩАНИЕ БЕЗ ОТВЕТА" in out
    assert "Разбираемся с вопросом" in out

    code, out = _watch(config, store, clock, SHORT_WAIT)
    assert "ОБЕЩАНИЕ" not in out
    assert "тишина" in out


def test_promise_in_owner_chat_is_ignored(config, store, clock):
    _alive(store, clock)
    write_watermark(config.state_dir, 0)
    _insert_sent(
        store,
        key="to-owner",
        chat_id=OWNER_ID,
        text="Проверим и напишу позже",
        sent_at=int(clock.now) - 7200,
    )
    code, out = _watch(config, store, clock, SHORT_WAIT)
    assert "ОБЕЩАНИЕ" not in out


def test_recent_promise_is_not_alerted(config, store, clock):
    _alive(store, clock)
    write_watermark(config.state_dir, 0)
    _insert_sent(
        store,
        key="fresh",
        chat_id=CLIENT_CHAT,
        text="Уточню у коллег",
        sent_at=int(clock.now) - 600,
    )
    code, out = _watch(config, store, clock, SHORT_WAIT)
    assert "ОБЕЩАНИЕ" not in out


def test_promise_is_not_alerted_when_a_later_message_was_sent(config, store, clock):
    _alive(store, clock)
    write_watermark(config.state_dir, 0)
    _insert_sent(
        store,
        key="old-promise",
        chat_id=CLIENT_CHAT,
        text="Проверим и вернусь",
        sent_at=int(clock.now) - 7200,
    )
    _insert_sent(
        store,
        key="answer",
        chat_id=CLIENT_CHAT,
        text="Отчёт готов, смотрите вложение",
        sent_at=int(clock.now) - 60,
    )
    code, out = _watch(config, store, clock, SHORT_WAIT)
    assert "ОБЕЩАНИЕ" not in out
