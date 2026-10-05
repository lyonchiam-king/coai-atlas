@echo off
REM COAI Atlas launcher. Starts the WhatsApp relay in its own window, then the control page.
REM Logic lives in Python; this file only checks, installs and starts things.
cd /d "%~dp0"

where node >nul 2>nul || (echo Node.js is not installed. Double-click Setup.bat first. & pause & exit /b 1)
python --version >nul 2>nul || (echo Python is not installed. Double-click Setup.bat first. & pause & exit /b 1)

REM Atlas itself is plain Python: pointing Python at src\ is the whole setup.
set "PYTHONPATH=%~dp0src"

REM The one optional library: Claude for AI-written messages. Without it, or
REM without ANTHROPIC_API_KEY in .env, Atlas still runs and uses templates.
python -c "import anthropic" >nul 2>nul || (
  echo Installing the AI writer library...
  python -m pip install --user -q anthropic || echo Could not install it. Messages will use templates.
)

if not exist whatsapp-relay\node_modules (
  echo First run: installing the WhatsApp relay...
  pushd whatsapp-relay
  call npm install --no-audit --no-fund
  popd
)

REM One relay per phone, each with its own WhatsApp session folder and port.
REM Two relays on one session take the connection from each other, so any relay
REM left over from a window closed with the X is stopped first.
for %%P in (3001 3002 3003) do for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%%P " ^| findstr LISTENING') do taskkill /pid %%p /t /f >nul 2>nul

REM Phone 1 keeps the original folder names, so a phone linked before stays linked.
start "Atlas relay - Phone 1 - keep open" /min cmd /c "cd /d "%~dp0whatsapp-relay" && set "RELAY_PORT=3001" && node server.js >> "%~dp0relay-log-1.txt" 2>&1"
start "Atlas relay - Phone 2 - keep open" /min cmd /c "cd /d "%~dp0whatsapp-relay" && set "RELAY_PORT=3002" && set "AUTH_DIR=.whatsapp-session-2" && set "INBOX_FILE=.whatsapp-inbox-2.json" && set "CONTACTS_FILE=.whatsapp-contacts-2.json" && node server.js >> "%~dp0relay-log-2.txt" 2>&1"
start "Atlas relay - Phone 3 - keep open" /min cmd /c "cd /d "%~dp0whatsapp-relay" && set "RELAY_PORT=3003" && set "AUTH_DIR=.whatsapp-session-3" && set "INBOX_FILE=.whatsapp-inbox-3.json" && set "CONTACTS_FILE=.whatsapp-contacts-3.json" && node server.js >> "%~dp0relay-log-3.txt" 2>&1"

python -m atlas serve
pause
