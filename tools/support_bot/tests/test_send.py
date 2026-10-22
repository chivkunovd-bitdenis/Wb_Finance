import httpx
import pytest
from support_fakes import CLIENT_CHAT, OTHER_CHAT, OWNER_ID, FakeTelegram

from wbf_support_bot.send import SendError, send_message
from wbf_support_bot.telegram import TelegramError


def _send(config, store, telegram, **kwargs):
    params = {"chat_id": CLIENT_CHAT, "key": "reply-1", "text": "Добрый день, отчёт готов."}
    params.update(kwargs)
    return send_message(store=store, api=telegram, config=config, now=1_800_000_100, **params)


def test_sent_message_is_recorded_and_status_was_sending_during_call(config, store, monkeypatch):
    class Inspecting(FakeTelegram):
        def send_message(self, **kwargs):
            self.status_during_call = store.get_outbox("reply-1").status
            return super().send_message(**kwargs)

    telegram = Inspecting()
    outcome = _send(config, store, telegram)
    assert telegram.status_during_call == "sending"
    assert outcome.status == "sent"
    assert outcome.exit_code == 0
    row = store.get_outbox("reply-1")
    assert row.status == "sent"
    assert row.tg_ids() == [501]
    assert row.sent_at == 1_800_000_100


def test_same_key_is_never_resent_after_success(config, store, telegram):
    _send(config, store, telegram)
    outcome = _send(config, store, telegram)
    assert outcome.already_sent is True
    assert outcome.status == "sent"
    assert len(telegram.sent) == 1


def test_already_sent_reports_when_text_differs(config, store, telegram):
    _send(config, store, telegram)
    outcome = _send(config, store, telegram, text="Совсем другой текст")
    assert outcome.already_sent is True
    assert outcome.text_matches is False
    assert len(telegram.sent) == 1


def test_lost_response_marks_unknown_and_is_not_resent(config, store, telegram):
    telegram.lose_response = True
    first = _send(config, store, telegram)
    assert first.status == "unknown"
    assert first.exit_code == 3
    assert len(telegram.sent) == 1  # сообщение реально ушло

    telegram.lose_response = False
    second = _send(config, store, telegram)
    assert second.status == "unknown"
    assert second.warning is not None
    assert len(telegram.sent) == 1


def test_leftover_sending_row_after_crash_is_not_resent(config, store, telegram):
    store.insert_outbox_pending(
        key="reply-1",
        chat_id=CLIENT_CHAT,
        text="Добрый день, отчёт готов.",
        reply_to_msg_id=None,
        file_path=None,
        created_at=1,
    )
    store.claim_outbox("reply-1")  # процесс упал именно здесь
    outcome = _send(config, store, telegram)
    assert outcome.status == "sending"
    assert outcome.warning is not None
    assert telegram.sent == []


def test_telegram_rejection_is_failed_and_retry_with_same_key_is_allowed(config, store, telegram):
    telegram.send_failures = [TelegramError(400, "Bad Request: chat not found")]
    failed = _send(config, store, telegram)
    assert failed.status == "failed"
    assert failed.exit_code == 1
    assert telegram.sent == []

    retried = _send(config, store, telegram)
    assert retried.status == "sent"
    assert len(telegram.sent) == 1


def test_network_error_is_unknown_not_failed(config, store, telegram):
    telegram.send_failures = [httpx.ConnectError("нет связи")]
    outcome = _send(config, store, telegram)
    assert outcome.status == "unknown"


def test_chat_outside_config_is_rejected_before_any_call(config, store, telegram):
    with pytest.raises(SendError):
        _send(config, store, telegram, chat_id=OTHER_CHAT)
    assert store.get_outbox("reply-1") is None
    assert telegram.sent == []


def test_owner_private_chat_is_allowed(config, store, telegram):
    outcome = _send(config, store, telegram, chat_id=OWNER_ID)
    assert outcome.status == "sent"


def test_empty_key_and_empty_text_are_rejected(config, store, telegram):
    with pytest.raises(SendError):
        _send(config, store, telegram, key="  ")
    with pytest.raises(SendError):
        _send(config, store, telegram, text="   ")


def test_long_text_goes_as_several_messages_with_reply_on_first(config, store, telegram):
    paragraph = "п" * 3000
    text = "\n\n".join([paragraph, paragraph, paragraph])
    outcome = _send(config, store, telegram, text=text, reply_to=77)
    assert outcome.status == "sent"
    assert len(telegram.sent) == 3
    assert telegram.sent[0]["reply_to"] == 77
    assert telegram.sent[1]["reply_to"] is None
    assert outcome.tg_message_ids == [501, 502, 503]
    assert store.get_outbox("reply-1").tg_ids() == [501, 502, 503]


def test_file_with_short_text_is_one_document_with_caption(config, store, telegram, tmp_path):
    path = tmp_path / "act.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    outcome = _send(config, store, telegram, text="Акт во вложении", file_path=path)
    assert outcome.status == "sent"
    assert telegram.sent == [
        {
            "kind": "document",
            "chat_id": CLIENT_CHAT,
            "file": "act.pdf",
            "caption": "Акт во вложении",
            "reply_to": None,
            "message_id": 501,
        }
    ]


def test_file_with_long_text_sends_text_then_document_without_caption(
    config, store, telegram, tmp_path
):
    path = tmp_path / "act.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    text = "т" * 1500
    _send(config, store, telegram, text=text, file_path=path)
    assert [item["kind"] for item in telegram.sent] == ["message", "document"]
    assert telegram.sent[1]["caption"] is None


def test_missing_file_is_rejected(config, store, telegram, tmp_path):
    with pytest.raises(SendError):
        _send(config, store, telegram, file_path=tmp_path / "нет.pdf")
