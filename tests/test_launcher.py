"""Atlas.bat cannot run here; these hold the lines that make three phones possible."""
from pathlib import Path

BAT = (Path(__file__).parent.parent / "Atlas.bat").read_bytes().decode()
CODE = "\n".join(l for l in BAT.splitlines() if not l.strip().upper().startswith("REM"))


def test_crlf_line_endings():
    assert "\r\n" in BAT and BAT.count("\n") == BAT.count("\r\n")


def test_three_relays_on_separate_ports_and_sessions():
    for n, port in (("1", "3001"), ("2", "3002"), ("3", "3003")):
        line = next(l for l in CODE.splitlines() if f"Phone {n} - keep open" in l)
        assert f'set "RELAY_PORT={port}"' in line and f"relay-log-{n}.txt" in line
    assert CODE.count(".whatsapp-session-2") == 1 and CODE.count(".whatsapp-session-3") == 1
    assert "AUTH_DIR" not in next(l for l in CODE.splitlines() if "Phone 1 - keep open" in l)   # keeps old pairing


def test_old_relays_are_stopped_before_new_ones_start():
    assert CODE.index("taskkill") < CODE.index("Phone 1 - keep open")
    assert "3001 3002 3003" in CODE


def test_ports_match_the_config():
    from atlas.config import Config
    assert [u.rsplit(":", 1)[1] for _, _, u in Config().accounts] == ["3001", "3002", "3003"]


SETUP_BAT = (Path(__file__).parent.parent / "Setup.bat").read_bytes().decode()
SETUP_PS = (Path(__file__).parent.parent / "setup.ps1").read_text()


def test_setup_bat_only_hands_over_to_powershell():
    assert SETUP_BAT.count("\n") == SETUP_BAT.count("\r\n")
    assert '-ExecutionPolicy Bypass -File "%~dp0setup.ps1"' in SETUP_BAT


def test_setup_installs_everything_atlas_bat_needs():
    for needle in ("OpenJS.NodeJS.LTS", "Python.Python.3.12", "npm.cmd install", "pip install --user",
                   "Atlas.bat", "Unblock-File"):
        assert needle in SETUP_PS, needle


def test_setup_never_trusts_the_store_python_stub():
    assert "Get-Command python -All" in SETUP_PS and "sys.executable" in SETUP_PS
    # Atlas.bat itself must test that python runs, not merely that something is named python
    assert "python --version >nul" in CODE and "where python" not in CODE


def test_setup_writes_the_key_without_a_bom_and_never_prints_it():
    assert "UTF8Encoding $false" in SETUP_PS
    assert "Write-Host $key" not in SETUP_PS and "Ok $key" not in SETUP_PS


def test_setup_is_powershell_5_compatible():
    # Windows ships 5.1; these are PowerShell 7 only.
    code = "\n".join(l for l in SETUP_PS.splitlines() if not l.strip().startswith("#"))
    assert " ?? " not in code and "?." not in code
    assert not any(" && " in l or " || " in l for l in code.splitlines())


ROOT = Path(__file__).parent.parent
MAC_SETUP = (ROOT / "Setup.command").read_bytes().decode()
MAC_ATLAS = (ROOT / "Atlas.command").read_bytes().decode()


def test_mac_launchers_are_executable_lf_bash_that_parses():
    import os
    import subprocess
    for name, text in (("Setup.command", MAC_SETUP), ("Atlas.command", MAC_ATLAS)):
        assert "\r\n" not in text, f"{name} has Windows line endings; bash on a Mac would fail"
        assert text.startswith("#!/bin/bash\n")
        assert os.access(ROOT / name, os.X_OK), f"{name} is not executable"
        assert subprocess.run(["bash", "-n", str(ROOT / name)]).returncode == 0


def test_mac_launchers_avoid_bash4_and_the_store_python():
    # macOS ships bash 3.2.
    for text in (MAC_SETUP, MAC_ATLAS):
        assert "declare -A" not in text and "mapfile" not in text and ",,}" not in text
    # /usr/bin/python3 opens an "install developer tools" dialog on a fresh Mac.
    code = "\n".join(l for l in MAC_SETUP.splitlines() if not l.strip().startswith("#"))
    assert "/usr/bin/python3" not in code


def test_mac_relays_match_windows_and_config():
    for n, port in (("1", "3001"), ("2", "3002"), ("3", "3003")):
        line = next(l for l in MAC_ATLAS.splitlines() if l.startswith(f"relay {n} "))
        assert f"RELAY_PORT={port}" in line
    assert "AUTH_DIR" not in next(l for l in MAC_ATLAS.splitlines() if l.startswith("relay 1 "))
    assert "caffeinate -ims" in MAC_ATLAS and "trap 'kill $(jobs -p)" in MAC_ATLAS


def test_zip_keeps_the_executable_bit():
    import io
    import subprocess
    import zipfile
    data = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=zip", "HEAD"], capture_output=True).stdout
    z = zipfile.ZipFile(io.BytesIO(data))
    for name in ("Setup.command", "Atlas.command"):
        if name in z.namelist():
            assert (z.getinfo(name).external_attr >> 16) & 0o111, name
