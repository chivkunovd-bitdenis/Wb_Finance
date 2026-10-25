"""Точка входа: python -m wbf_support_bot <команда> --config PATH."""

import sys

from wbf_support_bot.cli import main

if __name__ == "__main__":
    sys.exit(main())
