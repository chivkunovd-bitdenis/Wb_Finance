import json
import logging
import os
import shutil
from pathlib import Path

from support_fakes import CLIENT_CHAT, OPENAI_KEY, OTHER_CHAT, TOKEN, make_config_dict, text_update

from wbf_support_bot import commands
from wbf_support_bot.cli import build_parser, main, setup_logging
from wbf_support_bot.messages import parse_message
from wbf_support_bot.redaction import register_secret
from wbf_support_bot.store import Store

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.json"


def _write_config(directory: Path, *, openai_key: str = "", mode: int = 0o600) -> Path:
    path = directory / "config.json"
    path.write_text(
        json.dumps(make_config_dict(directory / "state", openai_key=openai_key)),
        encoding="utf-8",
    )
    os.chmod(path, mode)
    return path


def test_config_option_works_before_and_after_command():
    before = build_parser().parse_args(["--config", "a.json", "watch"])
    after = build_parser().parse_args(["watch", "--config", "b.json"])
    assert before.config == "a.json"
    assert after.config == "b.json"
    without = build_parser().parse_args(["check-config"])
    assert not hasattr(without, "config")


def test_send_options_parse_negative_chat_id_and_stdin_marker():
    args = build_parser().parse_args(
        ["send", "--chat", "-1001111111111", "--key", "k", "--text", "-", "--reply-to", "5"]
    )
    assert args.chat == -1001111111111
    assert args.text == "-"
    assert args.reply_to == 5


def test_check_config_with_example_fails_cleanly(tmp_path, capsys):
    path = tmp_path / "config.json"
    shutil.copyfile(EXAMPLE, path)
    code = main(["check-config", "--config", str(path)])
    out = capsys.readouterr().out
    assert code == 2
    assert "bot_token" in out
    assert "owner_user_id" in out


def test_check_config_masks_secrets_and_warns_about_permissions(tmp_path, capsys):
    path = _write_config(tmp_path, openai_key=OPENAI_KEY, mode=0o644)
    code = main(["check-config", "--config", str(path)])
    out = capsys.readouterr().out
    assert code == 0
    assert TOKEN not in out
    assert OPENAI_KEY not in out
    assert "***" in out
    assert "chmod 600" in out


def test_send_to_chat_outside_config_is_rejected_with_exit_1(tmp_path, capsys):
    path = _write_config(tmp_path)
    code = main(
        ["send", "--config", str(path), "--chat", str(OTHER_CHAT), "--key", "k1", "--text", "hi"]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "rejected" in out
    assert "запрещена" in out


def test_top_level_errors_are_redacted(tmp_path, capsys, monkeypatch):
    path = _write_config(tmp_path)
    register_secret(TOKEN)

    def boom(config_path):
        raise RuntimeError(f"сбой при запросе с токеном {TOKEN}")

    monkeypatch.setattr(commands, "cmd_check", boom)
    code = main(["check", "--config", str(path)])
    err = capsys.readouterr().err
    assert code == 1
    assert TOKEN not in err
    assert "RuntimeError" in err


def test_httpx_logger_is_kept_at_warning_level():
    setup_logging(logging.INFO)
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_show_flags_message_from_chat_outside_config(tmp_path, capsys):
    path = _write_config(tmp_path)
    store = Store(tmp_path / "state" / "state.db")
    record = parse_message(
        text_update(1, 10, chat_id=OTHER_CHAT)["message"], transcribe_voice=False
    )
    store.save_message(record, edited=False)
    row_id = store.chat_messages(OTHER_CHAT, 10)[0].id
    store.close()
    code = main(["show", "--config", str(path), "--id", str(row_id)])
    out = capsys.readouterr().out
    assert code == 0
    assert "чат не в конфиге" in out


def test_history_lists_client_and_bot_messages_in_order(tmp_path, capsys):
    path = _write_config(tmp_path)
    store = Store(tmp_path / "state" / "state.db")
    store.save_message(
        parse_message(text_update(1, 10, text="Где отчёт?")["message"], transcribe_voice=False),
        edited=False,
    )
    store.insert_outbox_pending(
        key="r1",
        chat_id=CLIENT_CHAT,
        text="Отчёт готов",
        reply_to_msg_id=10,
        file_path=None,
        created_at=1_800_000_500,
    )
    store.claim_outbox("r1")
    store.finish_outbox("r1", status="sent", tg_message_ids=[600], sent_at=1_800_000_500)
    store.close()
    code = main(["history", "--config", str(path), "--chat", str(CLIENT_CHAT), "--limit", "5"])
    out = capsys.readouterr().out
    assert code == 0
    assert out.index("Где отчёт?") < out.index("Отчёт готов")
    assert "мы (бот)" in out


def test_chats_lists_seen_and_configured_chats(tmp_path, capsys):
    path = _write_config(tmp_path)
    store = Store(tmp_path / "state" / "state.db")
    store.touch_chat(OTHER_CHAT, "Чужая группа", 1_800_000_000)
    store.close()
    code = main(["chats", "--config", str(path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "НЕ в конфиге" in out
    assert "Ромашка" in out
