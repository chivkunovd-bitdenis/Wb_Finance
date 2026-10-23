"""Командная строка: python -m wbf_support_bot <команда> [--config PATH].

Параметр --config можно указать и до имени команды, и после него.
Если его нет, файл ищется в переменной WBF_SUPPORT_BOT_CONFIG, затем в
~/.wbf-support-bot/config.json. Другие пути по умолчанию не используются.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from wbf_support_bot import commands
from wbf_support_bot.config import ConfigError, resolve_config_path
from wbf_support_bot.redaction import RedactingFormatter, redact

EXIT_ERROR = 1
EXIT_CONFIG = 2
CONFIG_HELP = (
    "путь к config.json (по умолчанию переменная WBF_SUPPORT_BOT_CONFIG "
    "или ~/.wbf-support-bot/config.json)"
)


def setup_logging(level: int) -> None:
    """Логи пишутся в stderr с маскировкой секретов. Библиотеку httpx переводим в WARNING:
    на уровне INFO она печатает полный адрес запроса, а в нём есть токен бота."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        RedactingFormatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )
    bot_logger = logging.getLogger("wbf_support_bot")
    bot_logger.handlers[:] = [handler]
    bot_logger.propagate = False
    bot_logger.setLevel(level)
    for noisy in ("httpx", "httpcore"):
        library_logger = logging.getLogger(noisy)
        library_logger.handlers[:] = [handler]
        library_logger.propagate = False
        library_logger.setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    # SUPPRESS: значение из общего родителя не перетирает значение, указанное до команды.
    common.add_argument("--config", metavar="PATH", default=argparse.SUPPRESS, help=CONFIG_HELP)

    parser = argparse.ArgumentParser(
        prog="python -m wbf_support_bot",
        description="Бот поддержки клиентов WB Finance: копит сообщения для живой AI-сессии.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", metavar="команда", required=True)

    sub.add_parser(
        "run",
        parents=[common],
        help="опрашивать Telegram и записывать сообщения (работает постоянно)",
    ).set_defaults(handler=lambda args: commands.cmd_run(_config_path(args)))

    watch = sub.add_parser(
        "watch",
        parents=[common],
        help="ждать новых сообщений клиентов и напечатать их; выход = сигнал",
    )
    watch.add_argument(
        "--quiet-sec", type=float, default=45, help="сколько ждать тишины перед выводом"
    )
    watch.add_argument(
        "--max-wait-sec", type=float, default=7200, help="через сколько секунд без сообщений выйти"
    )
    watch.set_defaults(
        handler=lambda args: commands.cmd_watch(
            _config_path(args), quiet_sec=args.quiet_sec, max_wait_sec=args.max_wait_sec
        )
    )

    send = sub.add_parser("send", parents=[common], help="отправить сообщение клиенту")
    send.add_argument("--chat", type=int, required=True, help="id чата из конфига")
    send.add_argument(
        "--key", required=True, help="уникальный ключ ответа (повтор не отправляется)"
    )
    send.add_argument(
        "--text", required=True, help="текст сообщения; значение «-» читает текст из stdin"
    )
    send.add_argument("--reply-to", type=int, default=None, help="id сообщения клиента в Telegram")
    send.add_argument("--file", type=Path, default=None, help="прикрепить файл")
    send.set_defaults(
        handler=lambda args: commands.cmd_send(
            _config_path(args),
            chat_id=args.chat,
            key=args.key,
            text=args.text,
            reply_to=args.reply_to,
            file_path=args.file,
        )
    )

    history = sub.add_parser(
        "history", parents=[common], help="последние сообщения чата и наши ответы"
    )
    history.add_argument("--chat", type=int, required=True, help="id чата")
    history.add_argument("--limit", type=int, default=20, help="сколько записей взять")
    history.set_defaults(
        handler=lambda args: commands.cmd_history(
            _config_path(args), chat_id=args.chat, limit=args.limit
        )
    )

    show = sub.add_parser("show", parents=[common], help="полный текст одного сообщения")
    show.add_argument("--id", dest="row_id", type=int, required=True, help="номер в базе")
    show.set_defaults(
        handler=lambda args: commands.cmd_show(_config_path(args), row_id=args.row_id)
    )

    download = sub.add_parser("download", parents=[common], help="скачать вложение сообщения")
    download.add_argument("--id", dest="row_id", type=int, required=True, help="номер в базе")
    download.add_argument("--out", type=Path, required=True, help="куда сохранить файл")
    download.set_defaults(
        handler=lambda args: commands.cmd_download(
            _config_path(args), row_id=args.row_id, out=args.out
        )
    )

    sub.add_parser("chats", parents=[common], help="группы, которые видел бот").set_defaults(
        handler=lambda args: commands.cmd_chats(_config_path(args))
    )
    sub.add_parser("check", parents=[common], help="проверить токен (getMe)").set_defaults(
        handler=lambda args: commands.cmd_check(_config_path(args))
    )
    sub.add_parser(
        "check-config", parents=[common], help="проверить config.json без сети"
    ).set_defaults(handler=lambda args: commands.cmd_check_config(_config_path(args)))
    return parser


def _config_path(args: argparse.Namespace) -> Path:
    return resolve_config_path(getattr(args, "config", None))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(logging.INFO if args.command == "run" else logging.WARNING)
    try:
        return int(args.handler(args))
    except ConfigError as exc:
        print("Ошибка конфигурации:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_CONFIG
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        # Последний рубеж: текст ошибки проходит маскировку, токен на экран не попадёт.
        print(f"Ошибка: {type(exc).__name__}: {redact(str(exc))}", file=sys.stderr)
        return EXIT_ERROR
