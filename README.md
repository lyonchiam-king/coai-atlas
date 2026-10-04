# COAI Atlas

A swarm of small sales agents that run COAI's WhatsApp outreach and follow-ups.
Built for our own use first; kept separable so it can be sold later.

## How the swarm works

One `tick` runs the agents in a fixed order. The order is the safety property.

| Agent | Job |
|---|---|
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

## Run

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
atlas import leads.csv
atlas tick          # needs the WhatsApp relay at 127.0.0.1:3001
```

## Not built yet

The WhatsApp relay (reuse Jarvis's Baileys relay), inbound message intake from the relay,
LLM-written drafts (only a template fallback exists), a dashboard, and any multi-tenant or billing code.
