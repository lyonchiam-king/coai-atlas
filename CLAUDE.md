# COAI Atlas -- AI sales swarm

WhatsApp outreach, follow-up and pipeline for COAI. Owner: Lyon Chiam (Penang), not a programmer.
Python 3.11+, stdlib only except the optional `anthropic` SDK (imported lazily), SQLite. Tests: `.venv/bin/pytest -q` must be green before any push.

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
- `Atlas.bat` holds no logic beyond checks and starts; PYTHONPATH=src, and the only pip install is the optional SDK.
- Relay errors must reach the page in the relay's own words (`RelayError`), never as "HTTPError".
- Human pacing (`pacing.py`): at most one send per run, inside a per-day plan stored in the db.
  It sits inside the guard, never replaces it. Tests about other rules set `human_pacing=False`.
- AI messages pass `check_message` or fall back to a template; the opt-out line is appended after.
  A refusal is an `LLMError`, never message text. Facts come from extra CSV columns (`Lead.facts`).
- Schema changes: add to `MIGRATIONS` in store.py so an existing atlas.db upgrades in place.
