import pytest
from support_fakes import CLIENT_CHAT, OTHER_CHAT, OWNER_ID, STAFF_ID, edited_update, text_update

from wbf_support_bot.messages import parse_message
from wbf_support_bot.store import Store, StoreError


def _record(update, **kwargs):
    return parse_message(update["message"], transcribe_voice=False, **kwargs)


def test_schema_is_created_once_and_reopen_is_safe(tmp_path):
    path = tmp_path / "state.db"
    first = Store(path)
    assert first.get_kv("schema_version") == "1"
    first.close()
    second = Store(path)
    assert second.get_kv("schema_version") == "1"
    second.close()


def test_newer_schema_is_refused(tmp_path):
    path = tmp_path / "state.db"
    first = Store(path)
    first.set_kv("schema_version", "99")
    first.close()
    with pytest.raises(StoreError):
        Store(path)


def test_database_uses_wal_mode(store):
    row = store._conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0].lower() == "wal"


def test_transaction_rolls_back_on_error(store):
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.set_kv("probe", "1")
            raise RuntimeError("boom")
    assert store.get_kv("probe") is None


def test_redelivered_message_is_not_duplicated(store):
    record = _record(text_update(1, 10))
    store.save_message(record, edited=False)
    store.save_message(record, edited=False)
    assert len(store.chat_messages(CLIENT_CHAT, 10)) == 1


def test_edit_updates_text_but_keeps_transcript(store):
    store.save_message(_record(text_update(1, 10, text="старый текст")), edited=False)
    edit = edited_update(2, 10, "новый текст")
    store.save_message(
        parse_message(edit["edited_message"], transcribe_voice=False, edited=True), edited=True
    )
    rows = store.chat_messages(CLIENT_CHAT, 10)
    assert len(rows) == 1
    assert rows[0].text == "новый текст"
    assert rows[0].edited_ts is not None


def test_client_messages_exclude_staff_and_unknown_chats(store):
    store.save_message(_record(text_update(1, 10)), edited=False)
    store.save_message(_record(text_update(2, 11, author_id=OWNER_ID)), edited=False)
    store.save_message(_record(text_update(3, 12, author_id=STAFF_ID)), edited=False)
    store.save_message(_record(text_update(4, 13, chat_id=OTHER_CHAT)), edited=False)
    rows = store.client_messages_after(0, [CLIENT_CHAT], [OWNER_ID, STAFF_ID])
    assert [row.msg_id for row in rows] == [10]


def test_outbox_claim_only_from_pending_or_failed(store):
    store.insert_outbox_pending(
        key="k1",
        chat_id=CLIENT_CHAT,
        text="hi",
        reply_to_msg_id=None,
        file_path=None,
        created_at=1,
    )
    assert store.claim_outbox("k1") is True
    assert store.claim_outbox("k1") is False  # уже sending, второй раз не забираем
    store.finish_outbox("k1", status="failed", error="x")
    assert store.claim_outbox("k1") is True


def test_last_sent_per_chat_returns_latest_sent_row(store):
    for key, sent_at in (("a", 100), ("b", 200)):
        store.insert_outbox_pending(
            key=key,
            chat_id=CLIENT_CHAT,
            text=key,
            reply_to_msg_id=None,
            file_path=None,
            created_at=1,
        )
        store.claim_outbox(key)
        store.finish_outbox(key, status="sent", tg_message_ids=[1], sent_at=sent_at)
    rows = store.last_sent_per_chat()
    assert [row.key for row in rows] == ["b"]
