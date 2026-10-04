# COAI Atlas -- AI sales swarm

WhatsApp outreach, follow-up and pipeline for COAI. Owner: Lyon Chiam (Penang), not a programmer.
Python 3.11+, stdlib only, SQLite. Tests: `.venv/bin/pytest -q` must be green before any push.

## Rules
- Every send goes through `guard.may_send`. Never add a second path to the channel.
- Agent order in `Swarm`: Listener, Responder, Writer, Sender. Listening comes before writing and sending.
- STOP/opt-out is keyword-based and the opt-out line is appended by code.
- Normalise phone numbers once, with the country known (`phone.to_e164`). Never guess a country code.
- Test clocks: pick times inside 09:00-21:00 MYT, or the guard will correctly refuse.
- Conventional Commits. No AI attribution trailers.
- The control page (`web.py`) is a raw-string page with JS. `test_the_page_script_runs_and_renders_every_relay_state`
  executes it under node; keep it green. It binds 127.0.0.1 only and every POST needs `X-Atlas: 1`.
- Auto-run starts off. Never make sending start by itself on launch.
- `Atlas.bat` holds no logic beyond checks and starts; it needs no pip install (stdlib only, PYTHONPATH=src).
- Relay errors must reach the page in the relay's own words (`RelayError`), never as "HTTPError".
