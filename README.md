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
| Writer | One message per lead, written by Claude from what the CSV says about them (industry, area, notes, language). Falls back to varied templates. Follow-ups land 2-3 and 5-6 days later, staggered per lead |
| Sender | One message at a time on a human schedule (below), always inside the guard: stage, 09:00-21:00 MYT, daily cap |

## Human schedule

- Mon-Sat. Sunday off.
- Each day starts at a random time between 09:15 and 10:30 and stops between 17:30 and 18:45.
- Lunch 12:30-14:00; Friday 12:15-14:45 for Jumaat.
- 3-25 minutes between messages, usually about 7, with an occasional 25-50 minute break.
- Each day sends 60-100% of the daily cap, not the same number every day.
- "typing..." shows for 4-18 seconds before each message.

## AI-written messages

Copy `.env.example` to `.env` and paste an Anthropic API key. Without one, Atlas uses templates.
Add any columns you know to the CSV -- `industry`, `area`, `notes`, `hook`, `language` (en / ms / zh) -- and
the writer uses them. `notes` are private context for the AI and are never pasted into a template;
`hook` is a line written for the message itself. Templates are English only; AI messages follow `language`. Every message is checked (no links, no placeholders, no invented opt-out, sensible
length) before it can be queued; one that fails is replaced by a template. Read them in "Next to send".

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
