#!/bin/bash
# COAI Atlas one-time setup for Mac. Double-click it once.
# The very first time, macOS may refuse an app "from an unidentified developer":
# right-click this file -> Open -> Open.
#
# Installs Python and Node.js if missing, the WhatsApp relay and the AI library,
# asks for the Claude key, offers Ollama, and puts "COAI Atlas" on the Desktop.
# Safe to run again. Written for the bash 3.2 that macOS ships: no bash 4 features.

cd "$(dirname "$0")" || exit 1
HERE="$PWD"

say()  { printf '\n\033[36m== %s\033[0m\n' "$1"; }
ok()   { printf '   \033[32mOK\033[0m  %s\n' "$1"; }
warn() { printf '   \033[33m!!\033[0m  %s\n' "$1"; }
fail() {
  printf '\n   \033[31mSETUP STOPPED: %s\033[0m\n' "$1"
  printf '   Take a screenshot of this window and send it to Claude.\n'
  read -r -p "   Press Enter to close " _
  exit 1
}
ask() {  # ask "question" y|n  -> success for yes
  local hint="[y/N]" a
  [ "$2" = y ] && hint="[Y/n]"
  read -r -p "$1 $hint " a
  a=$(printf '%s' "$a" | tr '[:upper:]' '[:lower:]')
  [ -z "$a" ] && a="$2"
  case "$a" in y*) return 0 ;; *) return 1 ;; esac
}

# Downloaded files carry Apple's quarantine flag, which makes every later
# double-click stop at a warning. These are ours; clear it once.
xattr -dr com.apple.quarantine "$HERE" 2>/dev/null
chmod +x "$HERE/Atlas.command" "$HERE/Setup.command" 2>/dev/null

for d in /opt/homebrew/bin /usr/local/bin; do [ -d "$d" ] && PATH="$d:$PATH"; done
export PATH

echo
echo "  COAI Atlas setup"
echo "  This installs everything Atlas needs. It takes 5-15 minutes."
echo "  Your Mac password may be asked for once, to install Python or Node.js."

# --- 1. Python 3.11+ -------------------------------------------------------------
say "Step 1 of 6: Python"
find_python() {
  # /usr/bin/python3 is deliberately not tried: on a Mac without developer tools
  # it opens an "install command line tools" dialog, and it is 3.9 anyway.
  local c
  for c in /Library/Frameworks/Python.framework/Versions/3.1[1-9]/bin/python3 \
           /opt/homebrew/bin/python3.1[1-9] /usr/local/bin/python3.1[1-9] \
           /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if [ -x "$c" ] && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  return 1
}
PY=$(find_python)
if [ -z "$PY" ]; then
  if command -v brew >/dev/null 2>&1; then
    brew install python@3.12 || warn "Homebrew could not install Python."
  else
    PKG=/tmp/atlas-python.pkg
    echo "   Downloading Python from python.org..."
    if curl -fL --progress-bar -o "$PKG" "https://www.python.org/ftp/python/3.12.7/python-3.12.7-macos11.pkg"; then
      echo "   Installing Python. Type your Mac password if asked (nothing shows while you type)."
      sudo installer -pkg "$PKG" -target / || warn "The Python installer did not finish."
    else
      warn "Could not download Python automatically."
    fi
  fi
  PY=$(find_python)
fi
if [ -z "$PY" ]; then
  open "https://www.python.org/downloads/macos/"
  fail "Python is missing. Your browser opened python.org: install the latest macOS version, then double-click Setup.command again."
fi
ok "Python $("$PY" -c 'import platform; print(platform.python_version())')"

# --- 2. Node.js --------------------------------------------------------------------
say "Step 2 of 6: Node.js"
if ! command -v node >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    brew install node || warn "Homebrew could not install Node.js."
  else
    # Ask nodejs.org for its current LTS rather than writing a version down here.
    VER=$(curl -fsSL https://nodejs.org/dist/index.json | tr '}' '\n' | grep '"lts":"' | head -1 \
          | grep -o '"version":"v[0-9.]*"' | cut -d'"' -f4)
    PKG=/tmp/atlas-node.pkg
    if [ -n "$VER" ] && curl -fL --progress-bar -o "$PKG" "https://nodejs.org/dist/$VER/node-$VER.pkg"; then
      echo "   Installing Node.js $VER. Type your Mac password if asked."
      sudo installer -pkg "$PKG" -target / || warn "The Node.js installer did not finish."
    fi
  fi
  PATH="/usr/local/bin:$PATH"
fi
if ! command -v node >/dev/null 2>&1; then
  open "https://nodejs.org/"
  fail "Node.js is missing. Your browser opened nodejs.org: install the LTS version, then run Setup.command again."
fi
ok "Node.js $(node -v)"

# --- 3. Atlas's own Python environment and the WhatsApp relay ------------------------
say "Step 3 of 6: Atlas and the WhatsApp connection"
# A private environment, so nothing here can upset Python used by anything else on the Mac.
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv || fail "Could not create Atlas's Python environment."
fi
ok "Python environment ready"
(cd whatsapp-relay && npm install --no-audit --no-fund) || fail "Installing the WhatsApp relay failed. Check the internet connection."
ok "WhatsApp relay installed"

# --- 4. The AI writer --------------------------------------------------------------
say "Step 4 of 6: AI writer"
if .venv/bin/python -m pip install -q --disable-pip-version-check anthropic; then
  ok "Claude library installed"
else
  warn "Claude library did not install; Atlas will use Ollama or templates."
fi
if grep -q '^ANTHROPIC_API_KEY=.' .env 2>/dev/null; then
  ok "A Claude key is already saved"
else
  echo "   For the best-written messages, paste your Claude API key now (it will not show)."
  echo "   Press Enter to skip; you can also add it later on the Atlas page, under Settings."
  read -r -s -p "   Claude API key: " KEY; echo
  KEY=$(printf '%s' "$KEY" | tr -d '[:space:]')
  case "$KEY" in
    sk-*) printf 'ANTHROPIC_API_KEY=%s\n' "$KEY" > .env; chmod 600 .env; ok "Key saved in .env (it stays on this Mac only)" ;;
    "")   warn "Skipped." ;;
    *)    warn "That does not look like a Claude key (they start with sk-). Skipped." ;;
  esac
  KEY=""
fi

if ask "   Also install Ollama, the free AI that runs on this Mac? (about 5GB, needs 16GB RAM)" n; then
  OLLAMA=""
  for c in /Applications/Ollama.app/Contents/Resources/ollama "$(command -v ollama 2>/dev/null)"; do
    [ -n "$c" ] && [ -x "$c" ] && OLLAMA="$c" && break
  done
  if [ -z "$OLLAMA" ]; then
    if curl -fL --progress-bar -o /tmp/atlas-ollama.zip "https://ollama.com/download/Ollama-darwin.zip" \
       && ditto -x -k /tmp/atlas-ollama.zip /Applications; then
      OLLAMA=/Applications/Ollama.app/Contents/Resources/ollama
    fi
  fi
  if [ -x "$OLLAMA" ]; then
    open -a Ollama 2>/dev/null
    sleep 5
    echo "   Downloading the llama3.1 model. This can take a while."
    "$OLLAMA" pull llama3.1 && ok "Ollama ready with llama3.1" || warn "Model download failed; open Ollama and try again later."
  else
    open "https://ollama.com/download"
    warn "Could not install Ollama automatically; your browser opened the download page."
  fi
fi

# --- 5. Shortcut and start-at-login ---------------------------------------------------
say "Step 5 of 6: Shortcut"
ln -sf "$HERE/Atlas.command" "$HOME/Desktop/COAI Atlas.command" && ok "\"COAI Atlas\" added to the Desktop"
if ask "   Start Atlas automatically when you log in? (sending still waits for you to tick Auto-run)" y; then
  osascript -e "tell application \"System Events\" to make login item at end with properties {path:\"$HERE/Atlas.command\", hidden:false}" >/dev/null 2>&1 \
    && ok "Atlas will start when you log in" \
    || warn "macOS did not allow it. You can add Atlas.command in System Settings -> General -> Login Items."
fi
echo "   While Atlas is open it keeps the Mac awake by itself; it cannot send while the Mac sleeps."

# --- 6. Start ------------------------------------------------------------------------
say "Step 6 of 6: Starting Atlas"
echo
printf '  \033[32mDONE.\033[0m Atlas opens in a new window and your browser shows the page.\n'
echo "  Next: scan each phone's QR code (WhatsApp -> Settings -> Linked devices -> Link a device)."
open "$HERE/Atlas.command"
