import logging
import sys

from support_fakes import OPENAI_KEY, TOKEN

from wbf_support_bot.redaction import MASK, RedactingFormatter, redact, register_secret


def test_redact_with_explicit_secrets():
    text = f"GET https://api.telegram.org/bot{TOKEN}/getMe key={OPENAI_KEY}"
    result = redact(text, [TOKEN, OPENAI_KEY])
    assert TOKEN not in result
    assert OPENAI_KEY not in result
    assert result.count(MASK) == 2


def test_registered_secret_is_used_by_default():
    register_secret(TOKEN)
    assert TOKEN not in redact(f"ошибка при запросе {TOKEN}")


def test_formatter_masks_message_and_traceback():
    formatter = RedactingFormatter("%(levelname)s %(message)s")
    register_secret(TOKEN)
    try:
        raise RuntimeError(f"сбой с токеном {TOKEN}")
    except RuntimeError:
        record = logging.LogRecord(
            name="wbf_support_bot",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg=f"ответ {TOKEN}",
            args=(),
            exc_info=sys.exc_info(),
        )
    rendered = formatter.format(record)
    assert TOKEN not in rendered
    assert "RuntimeError" in rendered
