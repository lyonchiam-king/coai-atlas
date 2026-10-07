# COAI Atlas -- AI sales swarm

WhatsApp outreach, follow-up and pipeline for COAI. Owner: Lyon Chiam (Penang), not a programmer.
Python 3.11+, stdlib only except the optional `anthropic` SDK (imported lazily), SQLite. Tests: `.venv/bin/pytest -q` must be green before any push.

## Rules
- Every send goes through `guard.may_send`. Never add a second path to the channel.
- Agent order in `Swarm`: Listeners, Responder, Checkers, Writer, Senders. Listening comes before writing and sending.
- STOP/opt-out is keyword-based and the opt-out line is appended by code.
- Normalise phone numbers once, with the country known (`phone.to_e164`). Never guess a country code.
- Test clocks: pick times inside 09:00-21:00 MYT, or the guard will correctly refuse.
  Tests of the classic one-message flow set `two_step=False`.
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
- Three phones: every lead has an `account`; only that phone's Sender writes to it, and the
  cap, pacing plan and check budget are per phone. A STOP reaching any phone still stops.
- Two-step reconnect: openers never sell and never carry the opt-out line (`SELLING` in writer.py).
  An unclear answer to "is this still you?" goes to a person (REPLIED), never to a pitch.
- On a phone whose relay can `check`, nothing is written until WhatsApp says the number exists.
- Writers: `make_writer` picks Claude / Ollama / templates from the page setting. Never hard-code
  an Ollama model name; offer what `/api/tags` lists. Each Claude model gets only the params it accepts.
- Page times use `cfg.tz_name`, never the browser's zone.
- The Claude key is write-only: `settings.save_claude_key` writes `.env`; the page only ever learns
  `claude_key: true/false`. Profile fields live in kv `profile`; a blank opt-out line means default.
- Owner templates (`templates.py`): spin, fill, then optional AI light-edit gated by `close_enough`
  (links, numbers, length, word overlap). A failed gate sends the owner's words, never the AI's.
- Media: the relay takes bare file names only and resolves them inside MEDIA_DIR (`media.js`);
  a missing file sends the text alone with a warning. Never pass paths over HTTP.
- Mac launchers (`Setup.command`, `Atlas.command`): LF, executable, bash 3.2. Never run
  /usr/bin/python3 (opens the developer-tools dialog). Atlas.command uses `.venv/bin/python`.
