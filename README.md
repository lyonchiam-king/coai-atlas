# COAI Atlas

A swarm of small sales agents that run COAI's WhatsApp outreach and follow-ups.
Built for our own use first; kept separable so it can be sold later.

## How the swarm works

One `tick` runs the agents in a fixed order. The order is the safety property.

| Agent | Job |
|---|---|
| Listener | Pulls replies from the WhatsApp relay. Acks only after storing, so a STOP is never lost |
| Intake | CSV of name, phone, company -> leads. Normalises to E.164, drops bad and duplicate numbers |
| Responder | Classifies inbound messages. STOP -> do-not-contact; any reply -> hand over to a human. Cancels queued drafts |
| Writer | Drafts the first message, follow-up 1 (day 2) and follow-up 2 (day 5+). Drafts in parallel |
| Sender | Sends through the guard: stage, quiet hours (09:00-21:00 MYT), daily cap. One bad send never stops the queue |

Pipeline: new -> contacted -> followup_1 -> followup_2 -> (replied | won | lost | do_not_contact).
After follow-up 2 the swarm stops. Replies are for a person.

## Rules

- The opt-out line is appended by code, never asked of the model.
- STOP detection is a keyword rule, never a model call.
- The guard (`guard.py`) is the only gate to a send. No agent bypasses it.
- Daily cap starts at 20. A new WhatsApp number gets banned fast.
- PDPA: every lead needs a lawful basis to contact. Keep the do-not-contact list.

## Start it (Windows)

1. Install **Node.js LTS** (nodejs.org) and **Python 3.11+** (python.org, tick "Add to PATH").
2. Double-click **`Atlas.bat`**. First run installs the relay (about a minute).
3. The control page opens at http://127.0.0.1:8790. Scan the QR:
   WhatsApp -> Settings -> Linked devices -> Link a device.
4. Paste leads as CSV (`name,phone,company`), press **Run now**, or tick **Auto-run**.

Keep the minimised "Atlas WhatsApp relay" window open. Its log is `relay-log.txt`.
Auto-run is off every time Atlas starts, on purpose.

## Developer

```bash
python -m venv .venv && .venv/bin/pip install pytest
.venv/bin/pytest -q                          # also runs the relay's node tests
cd whatsapp-relay && npm install && npm start
PYTHONPATH=src python -m atlas serve        # or: import <csv> | tick
```

`whatsapp-relay/` is ported from Jarvis (Baileys) and adds inbound replies:
`GET /inbox`, `POST /inbox/ack`.

## Not built yet

LLM-written drafts (only a template fallback exists), a dashboard, and any multi-tenant or billing code.

## Not verified yet

- The relay has never connected to WhatsApp: the build container cannot reach WhatsApp's servers.
  The relay was started for real and the control page drove it (status, "not connected" handling),
  but pairing, sending and receiving are first tested on your PC.
- `Atlas.bat` has never been run on Windows.
- Replies from contacts WhatsApp identifies only by an `@lid` id (no number) are skipped.
