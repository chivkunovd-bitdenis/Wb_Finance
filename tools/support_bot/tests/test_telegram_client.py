import json

import httpx
import pytest
from support_fakes import TOKEN

from wbf_support_bot.telegram import TelegramClient, TelegramError


def _client(handler):
    return TelegramClient(TOKEN, http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_get_updates_sends_offset_and_parses_result():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": [{"update_id": 7}]})

    updates = _client(handler).get_updates(offset=7, timeout=25, allowed_updates=["message"])
    assert updates == [{"update_id": 7}]
    assert seen["url"] == f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    assert seen["body"] == {"offset": 7, "timeout": 25, "allowed_updates": ["message"]}


def test_conflict_409_is_a_telegram_error_with_code():
    def handler(request):
        return httpx.Response(
            409,
            json={
                "ok": False,
                "error_code": 409,
                "description": "Conflict: terminated by other getUpdates request",
            },
        )

    with pytest.raises(TelegramError) as caught:
        _client(handler).get_updates(offset=0, timeout=1, allowed_updates=[])
    assert caught.value.code == 409


def test_error_text_never_contains_token():
    def handler(request):
        return httpx.Response(
            401, json={"ok": False, "error_code": 401, "description": "Unauthorized"}
        )

    with pytest.raises(TelegramError) as caught:
        _client(handler).get_me()
    assert TOKEN not in str(caught.value)
    assert caught.value.code == 401


def test_non_json_answer_becomes_error_with_http_code():
    def handler(request):
        return httpx.Response(502, text="<html>Bad gateway</html>")

    with pytest.raises(TelegramError) as caught:
        _client(handler).get_me()
    assert caught.value.code == 502


def test_send_message_passes_reply_and_disables_link_preview():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

    result = _client(handler).send_message(chat_id=-100, text="привет", reply_to=5)
    assert result == {"message_id": 9}
    assert seen["body"]["reply_to_message_id"] == 5
    assert seen["body"]["link_preview_options"] == {"is_disabled": True}


def test_send_document_is_multipart_with_caption(tmp_path):
    path = tmp_path / "act.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    seen = {}

    def handler(request):
        seen["content_type"] = request.headers["content-type"]
        seen["body"] = request.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 11}})

    result = _client(handler).send_document(
        chat_id=-100, path=path, caption="Подпись", reply_to=None
    )
    assert result == {"message_id": 11}
    assert seen["content_type"].startswith("multipart/form-data")
    assert b'filename="act.pdf"' in seen["body"]
    assert "Подпись".encode() in seen["body"]


def test_download_uses_file_endpoint():
    def handler(request):
        assert str(request.url) == f"https://api.telegram.org/file/bot{TOKEN}/voice/x.oga"
        return httpx.Response(200, content=b"ogg-bytes")

    assert _client(handler).download_file("voice/x.oga") == b"ogg-bytes"
