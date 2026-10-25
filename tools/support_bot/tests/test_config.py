import json
from pathlib import Path

import pytest
from support_fakes import OPENAI_KEY, OTHER_CHAT, OWNER_ID, TOKEN, make_config_dict

from wbf_support_bot.config import (
    ConfigError,
    default_config_path,
    describe_config,
    load_config,
    mask_secret,
    parse_config,
    resolve_config_path,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.json"


def test_valid_config_is_parsed(tmp_path):
    config = parse_config(make_config_dict(tmp_path / "s", openai_key=OPENAI_KEY))
    assert config.owner_user_id == OWNER_ID
    assert config.bot_token == TOKEN
    assert config.openai_api_key == OPENAI_KEY
    assert config.db_path == tmp_path / "s" / "state.db"
    assert config.staff_ids == {OWNER_ID, 1000002}


def test_repr_never_shows_secrets(tmp_path):
    config = parse_config(make_config_dict(tmp_path, openai_key=OPENAI_KEY))
    text = repr(config)
    assert TOKEN not in text
    assert OPENAI_KEY not in text


def test_missing_token_is_reported_by_field_name(tmp_path):
    raw = make_config_dict(tmp_path)
    raw["bot_token"] = ""
    with pytest.raises(ConfigError) as caught:
        parse_config(raw)
    assert any(problem.startswith("bot_token") for problem in caught.value.problems)


def test_invalid_token_is_reported_without_echoing_value(tmp_path):
    raw = make_config_dict(tmp_path)
    raw["bot_token"] = "SECRET-LOOKING-VALUE-xyz"
    with pytest.raises(ConfigError) as caught:
        parse_config(raw)
    message = str(caught.value)
    assert "bot_token" in message
    assert "SECRET-LOOKING-VALUE" not in message


@pytest.mark.parametrize("bad_owner", [None, "1000001", 0, -5, True, 12.5])
def test_owner_must_be_positive_integer(tmp_path, bad_owner):
    raw = make_config_dict(tmp_path)
    raw["owner_user_id"] = bad_owner
    with pytest.raises(ConfigError) as caught:
        parse_config(raw)
    assert any(problem.startswith("owner_user_id") for problem in caught.value.problems)


def test_unknown_key_is_reported(tmp_path):
    raw = make_config_dict(tmp_path)
    raw["bot_tokn"] = "123"
    with pytest.raises(ConfigError) as caught:
        parse_config(raw)
    assert any("bot_tokn" in problem for problem in caught.value.problems)


def test_chat_keys_and_client_names_are_validated(tmp_path):
    raw = make_config_dict(tmp_path)
    raw["chats"] = {"не-число": {"client": "A"}, "-100123": {"client": ""}}
    with pytest.raises(ConfigError) as caught:
        parse_config(raw)
    problems = caught.value.problems
    assert any("не-число" in problem for problem in problems)
    assert any("-100123" in problem and "client" in problem for problem in problems)


def test_example_file_only_reports_empty_fields(tmp_path):
    raw = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    with pytest.raises(ConfigError) as caught:
        parse_config(raw)
    problems = caught.value.problems
    assert len(problems) == 2
    assert problems[0].startswith("bot_token")
    assert problems[1].startswith("owner_user_id")


def test_mask_secret_keeps_only_tail():
    assert mask_secret("") == "(не задан)"
    assert mask_secret("short") == "***"
    masked = mask_secret(OPENAI_KEY)
    assert masked.endswith(OPENAI_KEY[-4:])
    assert OPENAI_KEY not in masked


def test_describe_config_masks_token_and_key(tmp_path):
    config = parse_config(make_config_dict(tmp_path, openai_key=OPENAI_KEY))
    text = "\n".join(describe_config(config))
    assert TOKEN not in text
    assert OPENAI_KEY not in text
    assert "Ромашка" in text


def test_config_path_priority_cli_then_env_then_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_config_path(
        "/x/cli.json", environ={"WBF_SUPPORT_BOT_CONFIG": "/y/env.json"}
    ) == Path("/x/cli.json")
    assert resolve_config_path(None, environ={"WBF_SUPPORT_BOT_CONFIG": "/y/env.json"}) == Path(
        "/y/env.json"
    )
    assert resolve_config_path(None, environ={}) == tmp_path / ".wbf-support-bot" / "config.json"
    assert default_config_path() == tmp_path / ".wbf-support-bot" / "config.json"


def test_missing_file_gives_clear_message(tmp_path):
    with pytest.raises(ConfigError) as caught:
        load_config(tmp_path / "nope.json")
    assert "не найден" in caught.value.problems[0]


def test_broken_json_does_not_leak_content(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"bot_token": "' + TOKEN + '" "oops"', encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert TOKEN not in str(caught.value)
    assert "строка" in caught.value.problems[0]


def test_can_send_only_to_client_chats_and_owner(tmp_path):
    config = parse_config(make_config_dict(tmp_path))
    assert config.can_send_to(config.owner_user_id)
    assert not config.can_send_to(OTHER_CHAT)
    client_chat = next(iter(config.chats))
    assert config.can_send_to(client_chat)
