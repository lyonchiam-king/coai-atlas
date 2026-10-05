# COAI Atlas

An AI sales assistant that reconnects with your own contacts over WhatsApp, from up to
three phones, and introduces COAI. Built for our own use first; kept separable so it can be
sold later.

## How it works

Each phone has its own list, its own human schedule and its own daily limit. Nobody hears
from two of your phones: a number already on one list is skipped when imported to another.

1. **Check** -- every number is looked up on WhatsApp first. Dead numbers are retired before
   anything is written. For live ones Atlas reads what the person shows publicly (About line;
   for WhatsApp Business: category, description, website). That is the free research.
2. **"Is this still you?"** -- a short, personal opener with no selling. One light nudge after
   about 3 days if there is no answer, then silence.
3. **They answer** --
   - as themselves -> Atlas replies to what they said and introduces COAI (the pitch);
   - "wrong number" -> never written to again;
   - "who is this?" or anything unclear -> **Your turn** on the page; Atlas stops.
4. **Follow-ups** -- two, if the pitch goes unanswered. Any reply hands the chat to you.
5. **STOP** at any point, to any of your phones, is final.

| Agent | Job |
|---|---|
| Listener (per phone) | Pulls replies from that phone. Acks only after storing, so a STOP is never lost |
| Responder | Reads each reply: confirm / wrong number / unsure / stop. Unsure always goes to a person |
| Checker (per phone) | WhatsApp lookups and free profile research, budgeted per day |
| Writer | One message per person, from what the list and profile say. Claude, Ollama or templates |
| Sender (per phone) | One message at a time on that phone's human schedule, inside the guard |

## Human schedule (per phone)

- Mon-Sat. Sunday off. Start between 09:15-10:30, stop between 17:30-18:45.
- Lunch 12:30-14:00; Friday 12:15-14:45 for Jumaat.
- 3-25 minutes between messages, usually about 7, with an occasional 25-50 minute break.
- Each day 60-100% of the cap (25 per phone). A reply to someone who just answered skips the
  daily quota, never the hours or the gap.
- "typing..." for 4-18 seconds before each message.

At 20-25 a day per phone, a list of 1,000 takes about 6-8 weeks.

## Start it (Windows)

1. Unzip Atlas to a folder such as `C:\Atlas`.
2. Double-click **`Setup.bat`** once. It installs Node.js and Python (through Windows' own
   installer), the WhatsApp relay and the AI library, asks for your Claude key (or skip),
   offers Ollama, keeps the PC awake while plugged in, adds a **COAI Atlas** desktop shortcut,
   and starts Atlas. Safe to run again.
3. On the page that opens, scan each phone's QR: WhatsApp -> Settings -> Linked devices ->
   Link a device. Rename each phone.
4. Add contacts per phone: a Google Contacts CSV, a `.vcf` exported from the phone, your own
   spreadsheet, or **Import this phone's WhatsApp chats** (available a few minutes after linking).
5. Read **Next to send**, then **Run now** or tick **Auto-run**.

After that, start it from the **COAI Atlas** desktop shortcut (or it starts with Windows, if
you chose that). Keep the three minimised "Atlas relay" windows open. Logs: `relay-log-1.txt`
... `-3.txt`. Auto-run is off every time Atlas starts, on purpose.

**Updating:** copy a new version over the folder. Never delete `atlas.db`, `.env` or
`whatsapp-relay\.whatsapp-session*` -- your contacts, key and phone links.

## Who writes the messages

Set on the page:

- **Claude** -- best writing, especially in BM and Chinese. Copy `.env.example` to `.env` and
  paste an Anthropic API key. Opus 5.5 (best), Sonnet 5.5 (cheaper) or Haiku 4.5 (cheapest).
- **Ollama** -- free, runs on this PC (16GB+ RAM recommended). Install from ollama.com, then e.g.
  `ollama pull llama3.1`. The page lists only models actually installed.
- **Templates** -- always available; openers in English, BM and Chinese, the rest English.

Every AI message is checked before it can queue (no links, no placeholders, no own opt-out line,
no selling in the opener, sensible length); one that fails is replaced by a template.

Useful columns in your lists: `how_we_know`, `notes` (private, for the AI only), `hook`
(a line to open with), `language` (en / ms / zh), `company`, `industry`, `area`.

## Developer

```bash
python -m venv .venv && .venv/bin/pip install pytest anthropic
.venv/bin/pytest -q                          # also runs the relay's node tests
cd whatsapp-relay && npm install && npm test
PYTHONPATH=src python -m atlas serve        # or: import <file> --phone 2 --list Rotary | tick
```

## Not verified yet

- No relay has connected to WhatsApp: this build container cannot reach WhatsApp's servers.
  Linking, sending, typing, number checks, profile reading and the contact sync from history
  are first tested on your PC. The Baileys calls were read from the installed library (6.7.24),
  not exercised.
- No real Claude or Ollama call has been made from here; both are tested against stand-ins.
- `Setup.bat`, `setup.ps1` and `Atlas.bat` have never been run on Windows. `setup.ps1` was
  parsed and its Python detection exercised under PowerShell 7 on Linux, not 5.1 on Windows.
- Replies from contacts WhatsApp identifies only by an `@lid` id (no number) are skipped.
