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
