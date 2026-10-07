#!/bin/bash
# COAI Atlas for Mac: starts one WhatsApp relay per phone, then the control page.
# Keep this window open; closing it stops Atlas. Logic lives in Python.

cd "$(dirname "$0")" || exit 1
for d in /opt/homebrew/bin /usr/local/bin; do [ -d "$d" ] && PATH="$d:$PATH"; done
export PATH

stop() { printf '\n%s\n' "$1"; read -r -p "Press Enter to close " _; exit 1; }
command -v node >/dev/null 2>&1 || stop "Node.js is not installed. Double-click Setup.command first."
[ -x .venv/bin/python ] || stop "Atlas is not set up yet. Double-click Setup.command first."
if [ ! -d whatsapp-relay/node_modules ]; then
  (cd whatsapp-relay && npm install --no-audit --no-fund) || stop "Installing the WhatsApp relay failed."
fi

# One relay per WhatsApp session: two on one session take the connection from
# each other. Anything left on these ports from an earlier run is stopped first.
for port in 3001 3002 3003 8790; do
  pids=$(lsof -ti "tcp:$port" -sTCP:LISTEN 2>/dev/null)
  [ -n "$pids" ] && kill $pids 2>/dev/null
done
sleep 1

relay() {  # relay <phone number> VAR=value ...
  local n="$1"; shift
  (cd whatsapp-relay && exec env "$@" node server.js >> "../relay-log-$n.txt" 2>&1) &
}
# Phone 1 keeps the original folder names, as on Windows.
relay 1 RELAY_PORT=3001
relay 2 RELAY_PORT=3002 AUTH_DIR=.whatsapp-session-2 INBOX_FILE=.whatsapp-inbox-2.json CONTACTS_FILE=.whatsapp-contacts-2.json
relay 3 RELAY_PORT=3003 AUTH_DIR=.whatsapp-session-3 INBOX_FILE=.whatsapp-inbox-3.json CONTACTS_FILE=.whatsapp-contacts-3.json
# Closing the window stops the relays with it, so none is left holding a session.
trap 'kill $(jobs -p) 2>/dev/null' EXIT

export PYTHONPATH="$PWD/src"
echo "COAI Atlas is running. Keep this window open; closing it stops Atlas."
# caffeinate keeps the Mac awake only while Atlas runs: a sleeping Mac sends nothing.
if command -v caffeinate >/dev/null 2>&1; then
  caffeinate -ims .venv/bin/python -m atlas serve
else
  .venv/bin/python -m atlas serve
fi
