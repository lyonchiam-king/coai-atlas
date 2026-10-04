@echo off
REM COAI Atlas launcher. Starts the WhatsApp relay in its own window, then the control page.
REM Logic lives in Python; this file only checks, installs and starts things.
cd /d "%~dp0"

where node >nul 2>nul || (echo Node.js is not installed. Install the LTS version from https://nodejs.org then run this again. & pause & exit /b 1)
where python >nul 2>nul || (echo Python is not installed. Install Python 3.11+ from https://python.org and tick "Add to PATH". & pause & exit /b 1)

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

REM One relay per WhatsApp session: two take the connection from each other.
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":3001 " ^| findstr LISTENING') do taskkill /pid %%p /t /f >nul 2>nul

start "Atlas WhatsApp relay - keep open" /min cmd /c "cd /d "%~dp0whatsapp-relay" && node server.js >> "%~dp0relay-log.txt" 2>&1"

python -m atlas serve
pause
