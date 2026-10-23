"""Конфигурация бота: поиск файла, чтение JSON и проверка полей.

Файл содержит токен бота и ключ OpenAI, поэтому:
- путь берётся из параметра --config, затем из переменной WBF_SUPPORT_BOT_CONFIG
  и только в последнюю очередь из ~/.wbf-support-bot/config.json;
- значения секретов не попадают ни в тексты ошибок, ни в repr объекта.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ENV_CONFIG_PATH = "WBF_SUPPORT_BOT_CONFIG"
DEFAULT_STATE_DIR = "~/.wbf-support-bot"
DEFAULT_TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"

_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
_CHAT_ID_RE = re.compile(r"^-?\d+$")
_KNOWN_KEYS = frozenset(
    {
        "state_dir",
        "bot_token",
        "owner_user_id",
        "internal_user_ids",
        "chats",
        "openai_api_key",
        "transcribe_model",
    }
)


class ConfigError(Exception):
    """Конфигурация не прошла проверку. В problems лежат понятные строки без секретов."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True)
class Config:
    state_dir: Path
    bot_token: str = field(repr=False)
    owner_user_id: int
    internal_user_ids: frozenset[int]
    chats: Mapping[int, str]
    openai_api_key: str = field(repr=False)
    transcribe_model: str

    @property
    def db_path(self) -> Path:
        return self.state_dir / "state.db"

    @property
    def staff_ids(self) -> frozenset[int]:
        """Сотрудники: владелец и внутренние сотрудники. Их сообщения не считаются клиентскими."""
        return self.internal_user_ids | {self.owner_user_id}

    def can_send_to(self, chat_id: int) -> bool:
        """Писать можно только в клиентские чаты из конфига и в личный чат владельца."""
        return chat_id in self.chats or chat_id == self.owner_user_id

    def secrets(self) -> list[str]:
        return [value for value in (self.bot_token, self.openai_api_key) if value]


def default_config_path() -> Path:
    return Path(DEFAULT_STATE_DIR).expanduser() / "config.json"


def resolve_config_path(cli_value: str | None, environ: Mapping[str, str] | None = None) -> Path:
    """Порядок поиска: параметр --config, переменная окружения, ~/.wbf-support-bot/config.json."""
    if cli_value:
        return Path(cli_value).expanduser()
    env = os.environ if environ is None else environ
    env_value = env.get(ENV_CONFIG_PATH, "").strip()
    if env_value:
        return Path(env_value).expanduser()
    return default_config_path()


def mask_secret(value: str) -> str:
    """Показывает только признак наличия и последние четыре символа длинного значения."""
    if not value:
        return "(не задан)"
    if len(value) < 12:
        return "***"
    return "***" + value[-4:]


def load_config(path: Path) -> Config:
    if not path.is_file():
        raise ConfigError(
            [
                f"файл конфигурации не найден: {path}. "
                "Создайте его, скопировав config.example.json, или запустите launchd/install.sh"
            ]
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            [f"{path}: файл не разобран как JSON ({exc.msg}, строка {exc.lineno})"]
        ) from None
    except OSError as exc:
        raise ConfigError([f"{path}: не удалось прочитать файл ({exc.strerror})"]) from None
    return parse_config(raw)


def parse_config(raw: Any) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError(["корень файла должен быть объектом в фигурных скобках {...}"])

    problems: list[str] = []
    for key in sorted(set(raw) - _KNOWN_KEYS):
        problems.append(f"неизвестное поле «{key}» (опечатка в названии?)")

    state_dir_raw = raw.get("state_dir", DEFAULT_STATE_DIR)
    if isinstance(state_dir_raw, str) and state_dir_raw.strip():
        state_dir = Path(state_dir_raw.strip()).expanduser()
    else:
        problems.append("state_dir: укажите папку строкой, например ~/.wbf-support-bot")
        state_dir = Path(DEFAULT_STATE_DIR).expanduser()

    token = _token_field(raw.get("bot_token"), problems)

    owner = raw.get("owner_user_id")
    if not _is_positive_int(owner):
        problems.append("owner_user_id: укажите ваш числовой Telegram id (целое число больше нуля)")
        owner = 0

    internal_raw = raw.get("internal_user_ids", [])
    if not isinstance(internal_raw, list) or not all(
        _is_positive_int(item) for item in internal_raw
    ):
        problems.append("internal_user_ids: список целых чисел (Telegram id сотрудников), можно []")
        internal_raw = []

    chats = _chats_field(raw.get("chats", {}), problems)

    openai_key = raw.get("openai_api_key", "")
    if not isinstance(openai_key, str):
        problems.append("openai_api_key: строка; оставьте пустой, если расшифровка не нужна")
        openai_key = ""

    model = raw.get("transcribe_model", DEFAULT_TRANSCRIBE_MODEL)
    if not isinstance(model, str) or not model.strip():
        problems.append("transcribe_model: название модели строкой")
        model = DEFAULT_TRANSCRIBE_MODEL

    if problems:
        raise ConfigError(problems)

    return Config(
        state_dir=state_dir,
        bot_token=token,
        owner_user_id=int(owner),
        internal_user_ids=frozenset(int(item) for item in internal_raw),
        chats=chats,
        openai_api_key=openai_key.strip(),
        transcribe_model=model.strip(),
    )


def describe_config(config: Config) -> list[str]:
    """Сводка для check-config. Секреты выводятся только в замаскированном виде."""
    lines = [
        f"Папка состояния: {config.state_dir}",
        f"Токен бота: {mask_secret(config.bot_token)}",
        f"Владелец (owner_user_id): {config.owner_user_id}",
        f"Сотрудники (internal_user_ids): {len(config.internal_user_ids)}",
        f"Клиентских чатов: {len(config.chats)}",
    ]
    for chat_id, client in sorted(config.chats.items()):
        lines.append(f"  - чат {chat_id}: {client}")
    if config.openai_api_key:
        lines.append(
            f"Расшифровка голосовых: включена, ключ {mask_secret(config.openai_api_key)}, "
            f"модель {config.transcribe_model}"
        )
    else:
        lines.append("Расшифровка голосовых: выключена (голосовые сохраняются без текста)")
    return lines


def _token_field(value: Any, problems: list[str]) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        problems.append("bot_token: не заполнен (токен бота из @BotFather)")
        return ""
    if not isinstance(value, str) or not _TOKEN_RE.match(value.strip()):
        # Значение намеренно не выводим: в ошибке не должно быть даже его фрагмента.
        problems.append(
            "bot_token: неверный формат (ожидается вид 123456789:AAH...), значение не выводится"
        )
        return ""
    return value.strip()


def _chats_field(value: Any, problems: list[str]) -> dict[int, str]:
    chats: dict[int, str] = {}
    if not isinstance(value, dict):
        problems.append('chats: нужен объект вида {"-1001234567890": {"client": "Название"}}')
        return chats
    for chat_key, entry in value.items():
        if not _CHAT_ID_RE.match(str(chat_key)):
            problems.append(
                f"chats: ключ «{chat_key}» должен быть id чата "
                "(целое число; у групп оно отрицательное)"
            )
            continue
        client = entry.get("client") if isinstance(entry, dict) else None
        if not isinstance(client, str) or not client.strip():
            problems.append(f"chats: у чата {chat_key} не заполнено поле client (название клиента)")
            continue
        chats[int(chat_key)] = client.strip()
    return chats


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
