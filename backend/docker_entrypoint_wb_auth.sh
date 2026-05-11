#!/usr/bin/env sh
set -eu

# Virtual display for headed chromium (inside container).
export DISPLAY="${DISPLAY:-:99}"

# Allow configuring display size (noVNC scaling will handle client size).
XVFB_W="${WB_AUTH_SCREEN_W:-1440}"
XVFB_H="${WB_AUTH_SCREEN_H:-900}"
XVFB_D="${WB_AUTH_SCREEN_D:-24}"

echo "wb_auth: starting Xvfb on ${DISPLAY} (${XVFB_W}x${XVFB_H}x${XVFB_D})"
Xvfb "${DISPLAY}" -screen 0 "${XVFB_W}x${XVFB_H}x${XVFB_D}" -ac +extension RANDR &

echo "wb_auth: starting fluxbox"
fluxbox >/tmp/fluxbox.log 2>&1 &

echo "wb_auth: starting x11vnc (port 5900)"
x11vnc -display "${DISPLAY}" -forever -shared -rfbport 5900 -nopw -quiet &

echo "wb_auth: starting noVNC (port 6080)"
# Debian novnc package ships as /usr/share/novnc with websockify.
websockify --web=/usr/share/novnc 0.0.0.0:6080 localhost:5900 >/tmp/novnc.log 2>&1 &

echo "wb_auth: starting manager API (port 8081)"
exec uvicorn app.wb_auth_manager:app --host 0.0.0.0 --port 8081

