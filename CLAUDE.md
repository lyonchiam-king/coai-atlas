# COAI Atlas -- AI sales swarm

WhatsApp outreach, follow-up and pipeline for COAI. Owner: Lyon Chiam (Penang), not a programmer.
Python 3.11+, stdlib only, SQLite. Tests: `.venv/bin/pytest -q` must be green before any push.

## Rules
- Every send goes through `guard.may_send`. Never add a second path to the channel.
- Agent order in `Swarm`: Responder, Writer, Sender. Listening comes before writing and sending.
- STOP/opt-out is keyword-based and the opt-out line is appended by code.
- Normalise phone numbers once, with the country known (`phone.to_e164`). Never guess a country code.
- Test clocks: pick times inside 09:00-21:00 MYT, or the guard will correctly refuse.
- Conventional Commits. No AI attribution trailers.
