#!/usr/bin/env bash
# Установка бота поддержки WB Finance на этот Mac.
#
# Скрипт трогает только своё:
#   ~/.wbf-support-bot                                 (папка бота, права 700)
#   ~/.wbf-support-bot/venv, ~/.wbf-support-bot/app    (окружение и копия пакета)
#   ~/.wbf-support-bot/config.json                     (права 600, создаётся из шаблона)
#   ~/Library/LaunchAgents/pro.wbfinance.support-bot.plist
#   ~/Library/Logs/wbf-support-bot.log
#   launchd-метка pro.wbfinance.support-bot
# Другие launchd-метки и другие папки он не читает и не меняет.
# Повторный запуск безопасен: готовый config.json не перезаписывается, файлы пакета
# копируются без удаления лишнего.
#
# Использование: launchd/install.sh   (можно задать PYTHON_BIN=/путь/к/python3.12)
# Код выхода: 0 - сервис запущен; 2 - конфиг ещё не заполнен, автозапуск не включён.

set -euo pipefail

LABEL="pro.wbfinance.support-bot"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOT_HOME="${HOME}/.wbf-support-bot"
APP_DIR="${BOT_HOME}/app"
VENV_DIR="${BOT_HOME}/venv"
CONFIG="${BOT_HOME}/config.json"
EXAMPLE="${SRC_DIR}/config.example.json"
TEMPLATE="${SRC_DIR}/launchd/${LABEL}.plist.template"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
LOG="${HOME}/Library/Logs/wbf-support-bot.log"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "1/6 Папка бота: ${BOT_HOME}"
mkdir -p "${BOT_HOME}"
chmod 700 "${BOT_HOME}"

echo "2/6 Python 3.12+ и виртуальное окружение"
if ! "${PYTHON_BIN}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
  echo "Нужен Python 3.12 или новее. Укажите его через PYTHON_BIN."
  exit 1
fi
if [ ! -x "${VENV_DIR}/bin/python" ]; then
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi
"${VENV_DIR}/bin/python" -m pip install --quiet --upgrade "httpx>=0.27,<1"

echo "3/6 Копирование пакета в ${APP_DIR} (без --delete)"
mkdir -p "${APP_DIR}"
rsync -a --exclude '__pycache__' --exclude '.pytest_cache' \
  "${SRC_DIR}/wbf_support_bot" "${APP_DIR}/"

echo "4/6 Конфиг"
if [ ! -f "${CONFIG}" ]; then
  cp "${EXAMPLE}" "${CONFIG}"
  echo "Создан ${CONFIG} из шаблона."
fi
chmod 600 "${CONFIG}"

echo "5/6 Проверка конфига (без сети)"
if ! (cd "${APP_DIR}" && "${VENV_DIR}/bin/python" -m wbf_support_bot check-config --config "${CONFIG}"); then
  cat <<EOF

Конфиг ещё не готов. Откройте ${CONFIG}, заполните поля, которые перечислены выше,
и запустите install.sh снова. Пока конфиг неполный, автозапуск не включается:
так сервис не уходит в бесконечные перезапуски.
EOF
  exit 2
fi

echo "6/6 Автозапуск: только метка ${LABEL}"
mkdir -p "${HOME}/Library/LaunchAgents" "${HOME}/Library/Logs"
sed \
  -e "s|__PYTHON__|${VENV_DIR}/bin/python|g" \
  -e "s|__APP_DIR__|${APP_DIR}|g" \
  -e "s|__CONFIG__|${CONFIG}|g" \
  -e "s|__LOG__|${LOG}|g" \
  "${TEMPLATE}" > "${PLIST}.tmp"
mv "${PLIST}.tmp" "${PLIST}"
plutil -lint "${PLIST}" > /dev/null

USER_DOMAIN="gui/$(id -u)"
if launchctl print "${USER_DOMAIN}/${LABEL}" > /dev/null 2>&1; then
  launchctl bootout "${USER_DOMAIN}/${LABEL}"
fi
launchctl bootstrap "${USER_DOMAIN}" "${PLIST}"
# Сначала забираем вывод целиком: с pipefail ранний выход head ломает скрипт через SIGPIPE.
STATUS_TEXT="$(launchctl print "${USER_DOMAIN}/${LABEL}")"
printf '%s\n' "${STATUS_TEXT}" | head -n 12

echo "Готово. Логи сервиса: ${LOG}"
echo "Проверка связи с Telegram: cd ${APP_DIR} && ${VENV_DIR}/bin/python -m wbf_support_bot check --config ${CONFIG}"
