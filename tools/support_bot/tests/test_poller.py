import logging
import threading

import httpx
from support_fakes import (
    CLIENT_CHAT,
    OTHER_CHAT,
    FakeTelegram,
    FakeTranscriber,
    document_update,
    edited_update,
    photo_update,
    sticker_update,
    text_update,
    voice_update,
)

from wbf_support_bot.config import Config
from wbf_support_bot.messages import VOICE_FAILED, VOICE_NO_KEY, VOICE_PREFIX
from wbf_support_bot.poller import Poller, run_loop
from wbf_support_bot.store import Store
from wbf_support_bot.telegram import TelegramError


def make_poller(config: Config, store: Store, telegram: FakeTelegram, transcriber=None):
    return Poller(
        config=config,
        store=store,
        api=telegram,
        transcriber=transcriber,
        clock=lambda: 1_800_000_000.0,
    )


def test_offset_is_saved_only_after_messages_are_committed(config, store, telegram, monkeypatch):
    telegram.update_batches = [[text_update(10, 1)]]
    visible_to_other_connection: list[int] = []
    real_set_kv = store.set_kv

    def spy(key, value):
        if key == "offset":
            # Отдельное соединение видит данные только после COMMIT.
            other = Store(config.db_path)
            try:
                visible_to_other_connection.append(len(other.chat_messages(CLIENT_CHAT, 10)))
            finally:
                other.close()
        real_set_kv(key, value)

    monkeypatch.setattr(store, "set_kv", spy)
    make_poller(config, store, telegram).poll_once()
    assert visible_to_other_connection == [1]
    assert store.get_kv("offset") == "11"


def test_failed_batch_rolls_back_and_keeps_offset(config, store, telegram, monkeypatch):
    telegram.update_batches = [[text_update(10, 1), text_update(11, 2)]]
    calls = {"count": 0}
    real_save = store.save_message

    def flaky(record, *, edited):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("диск заполнен")
        real_save(record, edited=edited)

    monkeypatch.setattr(store, "save_message", flaky)
    poller = make_poller(config, store, telegram)
    try:
        poller.poll_once()
    except RuntimeError:
        pass
    assert store.max_message_id() == 0
    assert store.get_kv("offset") is None


def test_crash_after_commit_is_redelivered_without_duplicates(config, store, telegram, monkeypatch):
    update = text_update(10, 1)
    telegram.update_batches = [[update], [update]]
    real_set_kv = store.set_kv

    def crash_on_offset(key, value):
        if key == "offset":
            raise RuntimeError("процесс упал")
        real_set_kv(key, value)

    poller = make_poller(config, store, telegram)
    monkeypatch.setattr(store, "set_kv", crash_on_offset)
    try:
        poller.poll_once()
    except RuntimeError:
        pass
    assert len(store.chat_messages(CLIENT_CHAT, 10)) == 1

    monkeypatch.setattr(store, "set_kv", real_set_kv)
    poller.poll_once()
    assert telegram.get_updates_calls[1]["offset"] == 0  # Telegram прислал обновление снова
    assert len(store.chat_messages(CLIENT_CHAT, 10)) == 1
    assert store.get_kv("offset") == "11"


def test_unknown_chat_is_stored_and_flagged_by_config(config, store, telegram):
    telegram.update_batches = [[text_update(10, 1, chat_id=OTHER_CHAT)]]
    make_poller(config, store, telegram).poll_once()
    assert len(store.chat_messages(OTHER_CHAT, 10)) == 1
    assert store.client_messages_after(0, sorted(config.chats), []) == []


def test_group_chat_is_recorded_in_seen_chats(config, store, telegram):
    telegram.update_batches = [
        [text_update(10, 1), text_update(11, 2, chat_type="private", chat_id=5555)]
    ]
    make_poller(config, store, telegram).poll_once()
    chat_ids = [chat.chat_id for chat in store.list_chats()]
    assert chat_ids == [CLIENT_CHAT]


def test_edited_message_updates_existing_row(config, store, telegram):
    telegram.update_batches = [
        [text_update(10, 1, text="первая версия")],
        [edited_update(11, 1, "исправленный текст")],
    ]
    poller = make_poller(config, store, telegram)
    poller.poll_once()
    poller.poll_once()
    rows = store.chat_messages(CLIENT_CHAT, 10)
    assert len(rows) == 1
    assert rows[0].text == "исправленный текст"


def test_voice_without_key_gets_placeholder(config, store, telegram):
    telegram.update_batches = [[voice_update(10, 1)]]
    make_poller(config, store, telegram, transcriber=None).poll_once()
    row = store.chat_messages(CLIENT_CHAT, 10)[0]
    assert row.kind == "voice"
    assert row.status == "ok"
    assert row.text == VOICE_NO_KEY


def test_voice_is_transcribed_with_prefix(config, store, telegram):
    telegram.update_batches = [[voice_update(10, 1)]]
    transcriber = FakeTranscriber(["Где мой отчёт за октябрь"])
    make_poller(config, store, telegram, transcriber=transcriber).poll_once()
    row = store.chat_messages(CLIENT_CHAT, 10)[0]
    assert row.status == "ok"
    assert row.text == VOICE_PREFIX + "Где мой отчёт за октябрь"
    assert telegram.file_requests == ["VOICE_FILE_1"]
    assert transcriber.calls[0][0] == b"OggS-fake-voice"
    assert transcriber.calls[0][1] == "voice.ogg"


def test_voice_transcription_retries_three_times_then_fails(config, store, telegram):
    telegram.update_batches = [[voice_update(10, 1)]]
    failure = httpx.ConnectError("нет связи")
    transcriber = FakeTranscriber([failure, failure, failure])
    poller = make_poller(config, store, telegram, transcriber=transcriber)

    poller.poll_once()
    row = store.chat_messages(CLIENT_CHAT, 10)[0]
    assert (row.status, row.attempts) == ("transcribing", 1)

    poller.poll_once()
    row = store.chat_messages(CLIENT_CHAT, 10)[0]
    assert (row.status, row.attempts) == ("transcribing", 2)

    poller.poll_once()
    row = store.chat_messages(CLIENT_CHAT, 10)[0]
    assert (row.status, row.attempts) == ("transcribe_failed", 3)
    assert row.text == VOICE_FAILED
    assert len(transcriber.calls) == 3

    poller.poll_once()  # после финального провала новых попыток нет
    assert len(transcriber.calls) == 3


def test_voice_transcription_succeeds_after_one_failure(config, store, telegram):
    telegram.update_batches = [[voice_update(10, 1)]]
    transcriber = FakeTranscriber([httpx.ConnectError("сбой"), "Всё понятно"])
    poller = make_poller(config, store, telegram, transcriber=transcriber)
    poller.poll_once()
    poller.poll_once()
    row = store.chat_messages(CLIENT_CHAT, 10)[0]
    assert row.status == "ok"
    assert row.text == VOICE_PREFIX + "Всё понятно"


def test_photo_document_and_sticker_are_described(config, store, telegram):
    telegram.update_batches = [
        [
            photo_update(10, 1, caption="Смотрите чек"),
            document_update(11, 2, "акт.pdf"),
            sticker_update(12, 3),
        ]
    ]
    make_poller(config, store, telegram).poll_once()
    photo, document, sticker = store.chat_messages(CLIENT_CHAT, 10)
    assert (photo.kind, photo.file_id, photo.caption) == ("photo", "PHOTO_BIG", "Смотрите чек")
    assert (document.kind, document.file_name) == ("document", "акт.pdf")
    assert sticker.kind == "other"


def test_reply_snippet_is_stored(config, store, telegram):
    telegram.update_batches = [[text_update(10, 1, reply_to=77)]]
    make_poller(config, store, telegram).poll_once()
    row = store.chat_messages(CLIENT_CHAT, 10)[0]
    assert row.reply_to_msg_id == 77
    assert row.reply_to_text == "Наш предыдущий ответ"


def test_malformed_update_is_skipped_and_queue_moves_on(config, store, telegram):
    broken = {"update_id": 5, "message": {"chat": {"id": CLIENT_CHAT, "type": "group"}}}
    telegram.update_batches = [[broken, text_update(6, 42)]]
    make_poller(config, store, telegram).poll_once()
    assert store.get_kv("offset") == "7"
    assert [row.msg_id for row in store.chat_messages(CLIENT_CHAT, 10)] == [42]


class ScriptedPoller:
    """Имитирует poll_once: по очереди отдаёт числа или бросает ошибки, потом останавливает."""

    def __init__(self, script, stop: threading.Event) -> None:
        self.script = list(script)
        self.stop = stop
        self.calls = 0

    def poll_once(self) -> int:
        self.calls += 1
        if not self.script:
            self.stop.set()
            return 0
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def test_run_loop_survives_conflict_and_network_errors(store, monkeypatch, caplog):
    monkeypatch.setattr(logging.getLogger("wbf_support_bot"), "propagate", True)
    stop = threading.Event()
    poller = ScriptedPoller(
        [
            TelegramError(409, "Conflict: terminated by other getUpdates request"),
            0,
            httpx.ConnectError("нет сети"),
        ],
        stop,
    )
    with caplog.at_level(logging.WARNING, logger="wbf_support_bot"):
        run_loop(store=store, poller=poller, stop=stop, initial_backoff=0.0)
    assert poller.calls == 4
    assert store.get_kv("heartbeat") is not None
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "конфликт" in messages
    assert "сеть недоступна" in messages
