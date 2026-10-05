# COAI Atlas one-time setup. Started by Setup.bat; safe to run again.
#
# Installs what Atlas needs (Node.js, Python), the WhatsApp relay, the optional
# AI library, asks for the Claude key, and puts "COAI Atlas" on the desktop.
# Written for Windows PowerShell 5.1, which every Windows 10/11 PC has: no
# PowerShell 7 syntax (no ?? , no ternary, no && between commands).

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
# Files from a downloaded ZIP carry Windows' "came from the internet" mark, which
# makes every later double-click stop at a warning. They are ours; clear it once.
Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue

function Say($text)  { Write-Host ''; Write-Host "== $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "   OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "   !!  $text" -ForegroundColor Yellow }
function Fail($text) {
    Write-Host ''
    Write-Host "   SETUP STOPPED: $text" -ForegroundColor Red
    Write-Host '   Take a photo of this window and send it to Claude.' -ForegroundColor Red
    Read-Host '   Press Enter to close'
    exit 1
}

function Refresh-Path {
    # A program installed a moment ago is not on this window's PATH until we reread it.
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Ask-YesNo($question, $default) {
    $hint = 'y/N'
    if ($default) { $hint = 'Y/n' }
    $answer = Read-Host "$question [$hint]"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
    return $answer.Trim().ToLower().StartsWith('y')
}

function Find-Python {
    # The Microsoft Store puts a fake "python" on PATH that opens the Store
    # instead of running anything, so a version check is the only real test.
    # The answer is always the real python.exe (sys.executable), never "py.exe":
    # Atlas.bat runs "python", so that is the folder that must go on PATH.
    # Every "python" on PATH is tried, not just the first: the fake one usually comes first.
    $candidates = @(Get-Command python -All -CommandType Application -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
    $candidates += @(Get-Command py -All -CommandType Application -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
    foreach ($candidate in $candidates) {
        try {
            $out = & $candidate --version 2>&1
            if ($LASTEXITCODE -eq 0 -and "$out" -match 'Python 3\.(1[1-9]|[2-9][0-9])') {
                $exe = & $candidate -c "import sys; print(sys.executable)" 2>$null
                if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path "$exe".Trim())) { return "$exe".Trim() }
            }
        } catch { }
    }
    $installed = Get-ChildItem -Path "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
                                     "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue |
                 Sort-Object FullName -Descending | Select-Object -First 1
    if ($installed) { return $installed.FullName }
    return $null
}

function Add-ToUserPath($dir) {
    # Prepended, not appended: the Store's fake python lives in the user PATH too
    # (...\WindowsApps), and whichever comes first is the one "python" runs.
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not $userPath) { $userPath = '' }
    $parts = @($userPath -split ';' | Where-Object { $_ -and ($_ -ne $dir) })
    [Environment]::SetEnvironmentVariable('Path', ((@($dir) + $parts) -join ';'), 'User')
    Refresh-Path
}

function Winget-Install($id, $label) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail "Windows cannot install $label by itself on this PC (winget is missing). Install 'App Installer' from the Microsoft Store, then run Setup.bat again."
    }
    Write-Host "   Installing $label. A Windows prompt may ask for permission: click Yes."
    & winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements | Out-Host
    Refresh-Path
}

Write-Host ''
Write-Host '  COAI Atlas setup' -ForegroundColor White
Write-Host '  This installs everything Atlas needs. It takes 5-15 minutes.'
Write-Host '  Leave this window open until it says DONE.'

# --- 1. Node.js: runs the WhatsApp relays --------------------------------------
Say 'Step 1 of 6: Node.js'
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Winget-Install 'OpenJS.NodeJS.LTS' 'Node.js' }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    $nodeDir = "$env:ProgramFiles\nodejs"
    if (Test-Path "$nodeDir\node.exe") { Add-ToUserPath $nodeDir }
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Fail 'Node.js did not install.' }
Ok ("Node.js " + (& node -v))

# --- 2. Python: runs Atlas itself ----------------------------------------------
Say 'Step 2 of 6: Python'
$python = Find-Python
if (-not $python) {
    Winget-Install 'Python.Python.3.12' 'Python'
    $python = Find-Python
}
if (-not $python) { Fail 'Python did not install.' }
# Atlas.bat runs "python", so Python's own folder must be on PATH -- ahead of the Store's fake one.
$pyDir = Split-Path -Parent $python
Add-ToUserPath "$pyDir\Scripts"
Add-ToUserPath $pyDir
Ok ("Python " + ((& $python --version 2>&1) -replace 'Python ', ''))

# --- 3. The WhatsApp relay ------------------------------------------------------
Say 'Step 3 of 6: WhatsApp connection'
Push-Location 'whatsapp-relay'
try {
    & npm.cmd install --no-audit --no-fund | Out-Host
    if ($LASTEXITCODE -ne 0) { Fail 'Installing the WhatsApp relay failed. Check the internet connection.' }
} finally { Pop-Location }
Ok 'WhatsApp relay installed'

# --- 4. The AI writer -----------------------------------------------------------
Say 'Step 4 of 6: AI writer'
& $python -m pip install --user --quiet --disable-pip-version-check anthropic 2>&1 | Out-Host
if ($LASTEXITCODE -eq 0) { Ok 'Claude library installed' } else { Warn 'Claude library did not install; Atlas will use templates or Ollama.' }

$envFile = Join-Path $PSScriptRoot '.env'
$hasKey = (Test-Path $envFile) -and ((Get-Content $envFile -Raw) -match 'ANTHROPIC_API_KEY=\S+')
if ($hasKey) {
    Ok 'A Claude key is already saved'
} else {
    Write-Host '   For the best-written messages, paste your Claude API key now.'
    Write-Host '   (Get one at console.anthropic.com -> API Keys. Press Enter to skip; you can add it later.)'
    $key = Read-Host '   Claude API key'
    if ($key -and $key.Trim().StartsWith('sk-')) {
        # Written without a BOM: Python must read "ANTHROPIC_API_KEY", not "﻿ANTHROPIC_API_KEY".
        [IO.File]::WriteAllText($envFile, "ANTHROPIC_API_KEY=$($key.Trim())`r`n", (New-Object Text.UTF8Encoding $false))
        Ok 'Key saved in .env (it stays on this PC only)'
    } elseif ($key) {
        Warn 'That does not look like a Claude key (they start with sk-). Skipped.'
    } else {
        Warn 'Skipped. Atlas will use Ollama or templates until a key is added.'
    }
}

if (Ask-YesNo '   Also install Ollama, the free AI that runs on this PC? (about 5GB, needs 16GB RAM)' $false) {
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { Winget-Install 'Ollama.Ollama' 'Ollama' }
    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollama -and (Test-Path "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe")) {
        Add-ToUserPath "$env:LOCALAPPDATA\Programs\Ollama"
        $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    }
    if ($ollama) {
        Write-Host '   Downloading the llama3.1 model. This can take a while.'
        & ollama pull llama3.1 | Out-Host
        if ($LASTEXITCODE -eq 0) { Ok 'Ollama ready with llama3.1' } else { Warn 'Model download failed; try again later with: ollama pull llama3.1' }
    } else {
        Warn 'Ollama did not install; you can get it later from ollama.com'
    }
}

# --- 5. Keep the PC awake, start with Windows ------------------------------------
Say 'Step 5 of 6: Keeping Atlas running'
if (Ask-YesNo '   Stop this PC from sleeping while plugged in? (Atlas cannot send while asleep)' $true) {
    & powercfg /change standby-timeout-ac 0 | Out-Null
    Ok 'Sleep turned off while plugged in'
}

$shell = New-Object -ComObject WScript.Shell
function Make-Shortcut($path) {
    $s = $shell.CreateShortcut($path)
    $s.TargetPath = Join-Path $PSScriptRoot 'Atlas.bat'
    $s.WorkingDirectory = $PSScriptRoot
    $s.Description = 'COAI Atlas'
    $s.Save()
}
Make-Shortcut (Join-Path ([Environment]::GetFolderPath('Desktop')) 'COAI Atlas.lnk')
Ok 'Shortcut "COAI Atlas" added to the desktop'
if (Ask-YesNo '   Start Atlas automatically when Windows starts? (sending still waits for you to tick Auto-run)' $true) {
    Make-Shortcut (Join-Path ([Environment]::GetFolderPath('Startup')) 'COAI Atlas.lnk')
    Ok 'Atlas will start with Windows'
}

# --- 6. Start ---------------------------------------------------------------------
Say 'Step 6 of 6: Starting Atlas'
Write-Host ''
Write-Host '  DONE. Atlas is starting and your browser will open.' -ForegroundColor Green
Write-Host '  Next: scan each phone''s QR code on the page'
Write-Host '  (WhatsApp -> Settings -> Linked devices -> Link a device).'
Write-Host ''
Start-Process -FilePath (Join-Path $PSScriptRoot 'Atlas.bat') -WorkingDirectory $PSScriptRoot
Start-Sleep -Seconds 3
