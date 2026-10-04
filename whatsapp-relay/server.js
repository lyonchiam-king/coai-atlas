/**
 * WhatsApp relay for COAI Atlas (ported from Jarvis).
 *
 * A thin Express wrapper around @whiskeysockets/baileys that speaks WhatsApp's
 * multi-device WebSocket protocol. The Python swarm talks to this via HTTP
 * on localhost — no browser, no Puppeteer, no Cloud API registration.
 *
 * State (auth creds) is persisted to .whatsapp-session/ so a restart does not
 * require re-pairing the phone.
 */

const express = require("express");
const fs = require("fs");
const {
  makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
} = require("@whiskeysockets/baileys");
const QRCode = require("qrcode");
const { extractInbound, Inbox } = require("./inbox");
const pino = require("pino");

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const PORT = parseInt(process.env.RELAY_PORT || "3001", 10);
const AUTH_DIR = process.env.AUTH_DIR || ".whatsapp-session";
const MAX_PER_DAY = parseInt(process.env.MAX_PER_DAY || "30", 10);
const MIN_INTERVAL_MS = parseInt(process.env.MIN_INTERVAL_SECONDS || "15", 10) * 1000;
// Registration lookups are cheaper than sends and carry no message, so they
// pace faster -- but they still pace. Checking is cached upstream, so this
// runs once per lead in its lifetime, not once per run.
const CHECK_INTERVAL_MS =
  parseInt(process.env.CHECK_INTERVAL_SECONDS || "3", 10) * 1000;
const CHECK_MAX_PER_REQUEST = parseInt(process.env.CHECK_MAX_PER_REQUEST || "50", 10);
const TYPING_MAX_MS = 20000;
const ACK_TIMEOUT_MS = parseInt(process.env.ACK_TIMEOUT_SECONDS || "8", 10) * 1000;

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let sock = null; // the Baileys socket
let qrCode = null; // latest QR string (null once paired)
let connected = false;
let dailySent = 0;
let dailyDate = new Date().toDateString();
let lastSendTime = 0;
let lastCheckTime = 0;
// message id -> WhatsApp status code (1 = sent to server, 2 = delivered).
const acks = new Map();

// Replies from leads, held until the swarm has stored them and acked. Persisted so a
// relay restart between a reply and the next poll does not lose the one message
// that matters most -- a STOP.
const inbox = new Inbox(process.env.INBOX_FILE || ".whatsapp-inbox.json");

// message id -> the content we sent under it.
//
// This is what fixes "Waiting for this message. This may take a while."
// WhatsApp is end-to-end encrypted per device, so when any device -- the
// recipient's, or one of the sender's own -- fails to decrypt a message, it
// does not give up: it sends back a *retry receipt* asking for that message
// again. Baileys answers a retry by calling `getMessage(key)` to fetch the
// original content and re-encrypting it against a fresh session.
//
// With no `getMessage` there is nothing to answer with. The retry goes
// unfulfilled, and the message stays "Waiting for this message" for ever --
// on the recipient's phone, and on the sender's own, which is where it was
// first noticed. Nothing about the send looked wrong: the socket accepted it
// and WhatsApp acknowledged it, because encryption is not the problem.
// Keeping the plaintext for a while is what makes the retry answerable.
//
// Bounded: this is a retry buffer, not a message archive. Retries arrive
// within seconds to minutes, so a few hundred recent messages is far more
// than the protocol ever asks for, and the entries are small -- an image
// message holds a media URL and its keys, never the bytes.
const MESSAGE_STORE_MAX = parseInt(process.env.MESSAGE_STORE_MAX || "400", 10);
const sentMessages = new Map();

function rememberMessage(id, content) {
  if (!id || !content) return;
  sentMessages.set(id, content);
  while (sentMessages.size > MESSAGE_STORE_MAX) {
    sentMessages.delete(sentMessages.keys().next().value);
  }
}

// Wait briefly for the server to acknowledge a message. Resolves to the
// status code, or null if nothing came back in time -- which is reported as
// "pending", never as "sent".
async function waitForAck(id, timeoutMs = ACK_TIMEOUT_MS) {
  if (!id) return null;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (acks.has(id)) return acks.get(id);
    await new Promise((r) => setTimeout(r, 200));
  }
  return acks.has(id) ? acks.get(id) : null;
}
let sendQueue = Promise.resolve(); // serialise sends so we never firehose
// True while /logout is tearing the session down -- a lock on the route, so a
// second press cannot start a teardown on top of one already running.
let resetting = false;
// The last error worth telling the user about, surfaced on /status. A relay
// that is merely "not connected" gives the page nothing to say.
let lastError = "";

// Which socket owns the relay.
//
// Every socket captures the generation it was built under, and every handler
// it registers does nothing once that number has moved on. Sockets do not
// close on command: `logout()` returns before the WebSocket has finished
// closing, so the old socket's `connection.update` lands *after* the
// replacement is already connecting. Without this it landed on the live
// handler, read the disconnect as "logged out", and deleted the credential
// folder the new socket had just written -- which crashed the next
// `saveCreds` with ENOENT and took the whole relay down. That is the "logged
// out, then no QR, then relay not working" sequence.
let generation = 0;

// Reconnection, which must never be allowed to stop.
//
// connect() awaits fetchLatestBaileysVersion(), which is a network call, so
// it can reject on nothing worse than a patchy minute. Every call site used
// to drop that rejection on the floor -- `setTimeout(connect, 3000)` keeps no
// reference to the promise -- and before the process-level handlers existed
// that killed the relay, which at least made the failure obvious and got it
// restarted. Swallowing the rejection instead turned it into something worse:
// a relay that stays up, never reconnects, and answers every send with "not
// connected" for as long as it is left running.
//
// So a failed attempt schedules the next one, backing off to a cap, and the
// delay resets the moment a connection opens.
const RECONNECT_MIN_MS = 3000;
const RECONNECT_MAX_MS = 60000;
let reconnectTimer = null;
let reconnectDelay = RECONNECT_MIN_MS;
let reconnectAt = 0;
// True while an attempt is actually in flight. Without it the window between
// the retry timer firing and the socket opening reported connected:false,
// reconnecting:false and no error -- which the page could only render as
// "Relay not running", so a normal reconnect looked like a dead relay.
let connecting = false;
// When the current connection opened, so a drop can report how long it held.
let openedAt = 0;

// Every connection transition, with a clock against it.
//
// A relay that connects and drops repeatedly is only diagnosable from the
// pattern -- how long it held, what code closed it, how many times. The
// console said "Disconnected" with no time attached, and the window it was
// printed in closes when the process stops, so relay-log.txt is where this
// has to end up.
function stamp(message) {
  console.log(`[whatsapp-relay] ${new Date().toISOString()} ${message}`);
}

function scheduleReconnect(reason, delay = reconnectDelay) {
  // One pending attempt at a time: a close event and a failed connect can
  // both ask, and two timers means two sockets.
  if (reconnectTimer || resetting) return;
  reconnectAt = Date.now() + delay;
  console.log(
    `[whatsapp-relay] Reconnecting in ${Math.round(delay / 1000)}s (${reason})`
  );
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
    connectSafely();
  }, delay);
}

// The only way connect() should ever be called from a timer or at startup.
function connectSafely() {
  return connect().catch((err) => {
    lastError = `Could not reach WhatsApp: ${err && err.message ? err.message : err}`;
    logger.error({ err }, "Connect failed");
    scheduleReconnect("last attempt failed");
  });
}

// ---------------------------------------------------------------------------
// Logger — pino but quiet (Baileys is chatty at info level)
// ---------------------------------------------------------------------------

const logger = pino({ level: "warn" });

// ---------------------------------------------------------------------------
// Daily counter
// ---------------------------------------------------------------------------

function resetDailyIfNeeded() {
  const today = new Date().toDateString();
  if (today !== dailyDate) {
    dailyDate = today;
    dailySent = 0;
  }
}

// ---------------------------------------------------------------------------
// Session teardown
// ---------------------------------------------------------------------------

// Delete the stored credentials.
//
// `sock.logout()` ends the session at WhatsApp's end but leaves the folder on
// disk untouched -- that part is the application's job, and skipping it is
// the classic Baileys reset bug: `useMultiFileAuthState` reads the same dead
// credentials back on the next connect, WhatsApp answers 401, and the relay
// reconnect-loops for ever without ever emitting a QR. Whoever pressed the
// button just sees a relay that never comes back.
async function clearSession() {
  // maxRetries is for Windows, which is where this runs. A file another
  // handle still holds gives EBUSY or EPERM rather than deleting, and the
  // handle is usually released a moment later -- Node retries internally
  // rather than failing a reset over a race that resolves itself.
  await fs.promises.rm(AUTH_DIR, {
    recursive: true,
    force: true,
    maxRetries: 5,
    retryDelay: 200,
  });
  acks.clear();
  sentMessages.clear();
}

// ---------------------------------------------------------------------------
// Connect to WhatsApp
// ---------------------------------------------------------------------------

async function connect() {
  const gen = ++generation;
  connecting = true;
  stamp("connecting to WhatsApp");
  try {
    var { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
    var { version } = await fetchLatestBaileysVersion();
  } catch (err) {
    if (gen === generation) connecting = false;
    throw err;
  }

  // Both of those awaited. If another connect started meanwhile it owns the
  // relay now, and building a second socket here would leave two of them
  // fighting over one account.
  if (gen !== generation) return;

  sock = makeWASocket({
    version,
    auth: state,
    printQRInTerminal: false,
    logger,
    browser: ["COAI Atlas", "Desktop", "1.0.0"],
    // Answers retry receipts. Without it every message a device cannot
    // decrypt stays "Waiting for this message" permanently -- see the note
    // on `sentMessages` above. Returning undefined is safe and simply means
    // that retry cannot be served; it is not an error.
    getMessage: async (key) => sentMessages.get(key?.id) || undefined,
  });

  sock.ev.on("creds.update", async () => {
    // Inert once this socket has been superseded: its credentials belong to
    // a session being torn down, and writing them would either resurrect the
    // old account or land in a folder that no longer exists.
    if (gen !== generation) return;
    try {
      await saveCreds();
    } catch (err) {
      // Never fatal. This throws ENOENT if the folder went away underneath
      // it, and an unhandled throw inside an event handler ends the process
      // -- the relay would simply vanish, which is what "relay not working"
      // with no further explanation actually was.
      lastError = `Could not save WhatsApp credentials: ${err.message || err}`;
      logger.error({ err }, "saveCreds failed");
    }
  });

  // WhatsApp acknowledges a message separately from sendMessage() resolving.
  // Without this the relay reported "sent" the moment the call returned,
  // which is true of a message the socket merely queued and never
  // transmitted -- so a send could look successful and arrive nowhere, with
  // nothing in the sender's own chat list either.
  sock.ev.on("messages.update", (updates) => {
    if (gen !== generation) return;
    for (const u of updates || []) {
      const id = u?.key?.id;
      const status = u?.update?.status;
      if (id && typeof status === "number") {
        acks.set(id, status);
        // Bounded: this is a short-lived correlation table, not a log.
        if (acks.size > 500) acks.delete(acks.keys().next().value);
      }
    }
  });

  sock.ev.on("messages.upsert", ({ messages, type }) => {
    if (gen !== generation || type !== "notify") return;
    for (const m of messages || []) {
      try {
        const item = extractInbound(m);
        if (item) inbox.add(item);
      } catch (err) {
        // A message we cannot read must never take the relay down.
        logger.warn({ err }, "Could not read an inbound message");
      }
    }
  });

  sock.ev.on("connection.update", (update) => {
    // A socket we have already replaced. Its close event is not news, and
    // acting on it is what deleted a live session's credentials.
    if (gen !== generation) return;

    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      // Convert the raw QR string to a base64 PNG for the dashboard.
      QRCode.toDataURL(qr, { width: 300, margin: 2 })
        .then((dataUrl) => {
          qrCode = dataUrl;
        })
        .catch((err) => {
          logger.error({ err }, "QR generation failed");
        });
    }

    if (connection === "open") {
      connected = true;
      connecting = false;
      qrCode = null;
      lastError = "";
      reconnectDelay = RECONNECT_MIN_MS;
      reconnectAt = 0;
      openedAt = Date.now();
      stamp("connected");
    }

    if (connection === "close") {
      connected = false;
      connecting = false;
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const loggedOut = statusCode === DisconnectReason.loggedOut;

      // How long it held is the tell. Seconds means something is taking the
      // connection away -- most often a second relay on the same session,
      // which is why the port is now a hard single-instance lock.
      const held = openedAt ? Math.round((Date.now() - openedAt) / 1000) : null;
      openedAt = 0;
      stamp(
        `disconnected after ${held === null ? "no successful connection" : held + "s"}`
        + ` (code ${statusCode}, logged out: ${loggedOut})`
      );
      if (held !== null && held < 30) {
        stamp(
          "that connection lasted under 30s. If this repeats, another relay "
          + "or another WhatsApp Web session is using the same linked device."
        );
      }

      // A reset is driving the teardown and reconnects itself. The
      // generation check above already makes a superseded socket inert; this
      // covers the window before the replacement has been built.
      if (resetting) return;

      if (!loggedOut) {
        scheduleReconnect(`socket closed (code ${statusCode})`);
        return;
      }

      // Logged out somewhere else -- almost always the phone, via
      // Linked devices > Log out. The session on disk is dead: WhatsApp
      // answers those credentials with 401 before the QR flow can begin, so
      // while they stay on disk every reconnect replays them and is rejected
      // again. The relay sits there logging that it is waiting for a scan it
      // can never offer, which reads as a hung relay and leaves re-pairing
      // impossible without deleting the folder by hand. Wipe them first so
      // the reconnect starts genuinely fresh and a QR actually appears.
      qrCode = null;
      console.log("[whatsapp-relay] Logged out — clearing session, new QR to follow");
      clearSession()
        .catch((err) => logger.error({ err }, "Could not clear session folder"))
        .finally(() => scheduleReconnect("re-pairing after logout"));
    }
  });
}

// ---------------------------------------------------------------------------
// HTTP API
// ---------------------------------------------------------------------------

const app = express();
// The default body limit is 100kb, which a text message never approached
// and a screenshot always exceeds: a 900x1200 PNG is a few hundred KB and
// base64 adds a third on top. Express rejected those requests with 413
// before the send route ran at all, so sending worked perfectly until the
// day messages started carrying a picture.
app.use(express.json({ limit: process.env.MAX_BODY || "25mb" }));

// A body that is still too large must not come back as Express's HTML
// error page, which the caller cannot parse and reports as a transport
// failure -- saying nothing about the size being the problem.
app.use((err, _req, res, next) => {
  if (err && (err.type === "entity.too.large" || err.status === 413)) {
    return res.status(413).json({
      status: "failed",
      error:
        "The message and its picture are larger than the relay accepts. "
        + "Send without the screenshot, or raise MAX_BODY.",
    });
  }
  return next(err);
});

// GET /status — connection state + daily counters
app.get("/status", (_req, res) => {
  resetDailyIfNeeded();
  res.json({
    connected,
    paired: connected, // Baileys: open socket = paired
    daily_sent: dailySent,
    daily_cap: MAX_PER_DAY,
    port: PORT,
    resetting,
    // A relay working its way back is not a relay that has given up, and the
    // page should not describe them the same way.
    connecting,
    reconnecting: reconnectTimer !== null,
    ...(reconnectAt ? { retry_in_seconds: Math.max(
      0, Math.round((reconnectAt - Date.now()) / 1000)) } : {}),
    // Only when there is one, so the page shows a reason rather than an
    // empty field.
    ...(lastError ? { last_error: lastError } : {}),
  });
});

// GET /qr — base64 PNG of the pairing QR, or null if already paired
app.get("/qr", (_req, res) => {
  res.json({ qr: qrCode });
});

// POST /send — { phone, message }
app.post("/send", async (req, res) => {
  // `image` is optional base64 PNG/JPEG bytes. With one, the message goes
  // as a picture with the text as its caption -- a screenshot of the site
  // says more in a phone notification than a link nobody taps.
  const { phone, message, image, typing_ms } = req.body || {};

  if (!phone || !message) {
    return res.status(400).json({
      status: "failed",
      error: "phone and message are required",
    });
  }

  if (!connected || !sock) {
    return res.status(503).json({
      status: "failed",
      error: "WhatsApp is not connected",
    });
  }

  resetDailyIfNeeded();

  if (dailySent >= MAX_PER_DAY) {
    return res.status(429).json({
      status: "failed",
      error: `Daily cap of ${MAX_PER_DAY} messages reached`,
    });
  }

  // Serialise sends so we never fire two at once, and enforce the gap.
  sendQueue = sendQueue.then(async () => {
    const now = Date.now();
    const wait = Math.max(0, MIN_INTERVAL_MS - (now - lastSendTime));
    if (wait > 0) {
      await new Promise((r) => setTimeout(r, wait));
    }

    try {
      // Baileys expects the JID in international format:
      // <country><number>@s.whatsapp.net. The caller normalises to E.164 --
      // outreach/phone.py knows each lead's country and this process does
      // not -- so the number arrives ready and is used as given.
      //
      // This used to force a "60" onto anything that lacked one, which was
      // right in Penang and dangerous everywhere else: a US number
      // 15045550123 became 6015045550123, a real Malaysian line belonging
      // to a stranger. Never re-guess a country code here.
      const jid = toJid(phone);
      if (!jid) {
        lastSendTime = Date.now();
        return res.status(400).json({
          status: "failed",
          error: "phone must be in international format, digits only",
        });
      }
      // Confirm the recipient exists before sending. WhatsApp accepts a
      // message for any well-formed JID and reports nothing wrong, so
      // sending blind to a number that is not registered looks exactly like
      // success and reaches nobody.
      const digits = jid.split("@")[0];
      let registered = null;
      try {
        const found = await sock.onWhatsApp(digits);
        registered = (found || []).some((e) => {
          const back = String(e?.jid || "").replace(/[^0-9]/g, "");
          return (back === digits || back.endsWith(digits)) && e?.exists !== false;
        });
      } catch (err) {
        // A failed check is not proof of absence, so the send still goes.
        logger.warn({ err, phone }, "Pre-send check failed; sending anyway");
      }
      // Deliberately does not block. The queue only offers leads whose
      // cached verdict is already "yes", so a live "no" here contradicts an
      // earlier answer from the same API and is far more likely a hiccup
      // than the truth -- and refusing on it stops the send outright. It is
      // reported alongside the result instead.
      const unregistered = registered === false;

      // "typing..." for a few seconds first, as a person would. Atlas picks the
      // length; capped here so a bad value cannot hold the send queue hostage.
      // Cosmetic only: a failure here must never cost the message.
      const typingMs = Math.max(0, Math.min(TYPING_MAX_MS, parseInt(typing_ms, 10) || 0));
      if (typingMs > 0) {
        try {
          await sock.presenceSubscribe(jid);
          await sock.sendPresenceUpdate("composing", jid);
          await new Promise((r) => setTimeout(r, typingMs));
          await sock.sendPresenceUpdate("paused", jid);
        } catch (err) {
          logger.warn({ err, phone }, "Typing indicator failed; sending anyway");
        }
      }

      let payload = { text: message };
      if (image) {
        try {
          const buffer = Buffer.from(image, "base64");
          if (!buffer.length) throw new Error("empty image");
          payload = { image: buffer, caption: message };
        } catch (err) {
          // A bad attachment must not cost the message. Sending the text
          // alone is worth far more than failing the whole send.
          logger.warn({ err, phone }, "Attachment unusable; sending text only");
        }
      }
      const result = await sock.sendMessage(jid, payload);

      lastSendTime = Date.now();
      dailySent += 1;

      const key = result?.key;
      // Kept so a retry receipt for this message can be answered. A device
      // that fails to decrypt asks for the message again; with nothing held
      // here that request cannot be served and the message stays "Waiting
      // for this message" on the recipient's phone and on our own.
      rememberMessage(key?.id, result?.message);
      // "sent" now means WhatsApp acknowledged it, not merely that the call
      // returned. Without an ack the honest answer is "pending": the message
      // may be sitting in a socket that never transmitted it.
      const ack = await waitForAck(key?.id);
      // An ack is evidence, not a gate. WhatsApp does not always emit
      // messages.update promptly -- a first message to a new contact often
      // confirms late -- and treating silence as failure turned working
      // sends into errors and stopped the queue dead. Report what is known:
      // "sent" when confirmed, "unconfirmed" when the message left but
      // nothing came back yet. Neither is a failure.
      res.json({
        status: ack === null ? "unconfirmed" : "sent",
        message_id: key?.id || "",
        dialled: digits,
        ack: ack,
        warning: unregistered
          ? `WhatsApp did not recognise ${digits} just now, though it was `
            + "confirmed earlier. The message was still sent."
          : undefined,
        note:
          ack === null
            ? "WhatsApp has not confirmed delivery yet. The message was "
              + "accepted; check the chat on your phone if it matters."
            : undefined,
        timestamp: new Date().toISOString(),
      });
    } catch (err) {
      logger.error({ err, phone }, "Send failed");
      res.json({
        status: "failed",
        error: err.message || "Unknown error",
        timestamp: new Date().toISOString(),
      });
    }
  });
});

// Digits of an E.164 number -> a WhatsApp JID. No country is inferred: a
// number that does not already carry its country code is refused rather than
// sent somewhere plausible-looking.
function toJid(phone) {
  const digits = String(phone || "").replace(/[^0-9]/g, "");
  // Shortest real E.164 subscriber numbers are 8 digits including the code.
  if (digits.length < 8 || digits.length > 15) return "";
  if (digits.startsWith("0")) return "";
  return digits + "@s.whatsapp.net";
}

// POST /check — is this number registered on WhatsApp?
//
// The only reliable answer for a country whose numbering plan does not
// separate mobile from landline -- and for landlines everywhere, since
// WhatsApp Business verifies fixed lines by voice call. Paced like /send and
// capped per request: a burst of lookups for numbers never messaged is
// exactly the pattern WhatsApp rate-limits.
//
// onWhatsApp() takes a PHONE NUMBER, not a JID. It builds the query by
// prefixing "+" to whatever it is handed, so passing 4479...@s.whatsapp.net
// asks about "+4479...@s.whatsapp.net", which matches nothing -- every
// number came back "not on WhatsApp", in every country. Pass bare digits.
app.post("/check", async (req, res) => {
  const { phones, debug } = req.body || {};

  if (!Array.isArray(phones) || phones.length === 0) {
    return res.status(400).json({ error: "phones must be a non-empty array" });
  }
  if (phones.length > CHECK_MAX_PER_REQUEST) {
    return res.status(400).json({
      error: `at most ${CHECK_MAX_PER_REQUEST} numbers per request`,
    });
  }
  if (!connected || !sock) {
    return res.status(503).json({ error: "WhatsApp is not connected" });
  }

  // Share the send queue so a check never overlaps a send. Everything inside
  // is wrapped: an escaping throw would both hang this request and poison
  // sendQueue for the life of the process, silently killing every later send.
  sendQueue = sendQueue.then(async () => {
    const results = {};
    try {
      for (const phone of phones) {
        const digits = String(phone || "").replace(/[^0-9]/g, "");
        if (digits.length < 8 || digits.length > 15 || digits.startsWith("0")) {
          results[phone] = { registered: false, error: "not international format" };
          continue;
        }

        const wait = Math.max(0, CHECK_INTERVAL_MS - (Date.now() - lastCheckTime));
        if (wait > 0) await new Promise((r) => setTimeout(r, wait));

        try {
          const found = await sock.onWhatsApp(digits);
          // Match on the digits that come back rather than on position:
          // Baileys omits numbers that do not exist, so index 0 of a short
          // reply can belong to a different number than the one asked about.
          const hit = (found || []).find((entry) => {
            const back = String(entry?.jid || "").replace(/[^0-9]/g, "");
            return back === digits || back.endsWith(digits) || digits.endsWith(back);
          });
          results[phone] = { registered: Boolean(hit && hit.exists !== false) };
          if (debug) {
            results[phone].raw = found;
            results[phone].asked = digits;
          }
        } catch (err) {
          logger.error({ err, phone }, "Check failed");
          // Unknown, not "no" -- writing "no" would permanently retire a lead
          // because the network hiccuped once.
          results[phone] = { registered: null, error: err.message || "lookup failed" };
        }
        lastCheckTime = Date.now();
      }
      res.json({ results, checked_at: new Date().toISOString() });
    } catch (err) {
      logger.error({ err }, "Check batch failed");
      if (!res.headersSent) {
        res.status(500).json({ error: err.message || "check failed" });
      }
    }
  });
});

// GET /inbox -- replies not yet acknowledged by the swarm.
app.get("/inbox", (_req, res) => {
  res.json({ messages: inbox.list() });
});

// POST /inbox/ack -- { ids }. Only called after the swarm has stored them, so a
// crash between poll and store re-delivers instead of losing a reply.
app.post("/inbox/ack", (req, res) => {
  const { ids } = req.body || {};
  if (!Array.isArray(ids)) {
    return res.status(400).json({ error: "ids must be an array" });
  }
  res.json({ removed: inbox.ack(ids) });
});

// POST /logout — unlink this account and come back ready for a new QR.
//
// Used by the dashboard's "Log out / switch account" button. Three things
// have to happen in order, and skipping any one of them leaves the relay in a
// state that looks broken rather than logged out:
//
//   1. Tell WhatsApp, so the entry disappears from Linked devices on the
//      phone rather than lingering as a device nobody can see the use of.
//   2. Delete the credentials on disk. Without this the next connect reads
//      the dead session straight back and loops on 401 with no QR.
//   3. Reconnect, so a fresh QR exists by the time the page next asks.
//
// `resetting` holds the close handler off while this runs: it would otherwise
// reconnect underneath us and rebuild the folder being deleted.
app.post("/logout", async (_req, res) => {
  if (resetting) {
    return res.status(409).json({
      ok: false,
      error: "A reset is already in progress. Give it a few seconds.",
    });
  }

  resetting = true;
  connected = false;
  qrCode = null;
  lastError = "";

  // Orphan the old socket before touching anything. Bumping the generation
  // makes every handler it registered inert, so the close event that arrives
  // seconds from now -- after logout() has already returned -- cannot reach
  // the live handler and delete the replacement's credentials.
  const dying = sock;
  sock = null;
  generation += 1;

  try {
    if (dying) {
      try {
        // Best effort, so the device disappears from Linked devices on the
        // phone. The credentials go either way: this machine forgets the
        // account regardless of what WhatsApp heard.
        await dying.logout();
      } catch (err) {
        logger.warn({ err }, "logout() failed; clearing local session anyway");
      }
      try {
        dying.end(undefined);
      } catch {
        // Already closed.
      }
    }

    try {
      await clearSession();
    } catch (err) {
      // Reported, never reported as success. With the credentials still on
      // disk the relay reconnects to the same account, and an ok here would
      // leave the user pressing a button that silently changes nothing.
      logger.error({ err }, "Could not delete the session folder");
      lastError = `Could not delete ${AUTH_DIR}: ${err.message || err}`;
      resetting = false;
      scheduleReconnect("session could not be cleared");
      return res.status(500).json({
        ok: false,
        error:
          "Could not delete the saved WhatsApp session at "
          + AUTH_DIR
          + ": " + (err.message || String(err))
          + ". Close the relay window and delete that folder by hand.",
      });
    }

    // Awaited: the reply should mean a socket exists and a QR is coming, not
    // merely that the old one is gone. Answering earlier is how a reset that
    // then failed to reconnect looked like a success followed by a dead
    // relay.
    await connect();
  } catch (err) {
    logger.error({ err }, "Reset failed");
    lastError = `Reset failed: ${err.message || err}`;
    resetting = false;  // released before scheduling, or the schedule is refused
    scheduleReconnect("reset could not reconnect");
    return res.status(500).json({
      ok: false,
      error:
        "Logged out, but the relay could not start a new session: "
        + (err.message || String(err))
        + ". Close the relay window and run Dashboard.bat again.",
    });
  } finally {
    resetting = false;
  }

  res.json({ ok: true, note: "Logged out. A new QR code will appear shortly." });
});

// ---------------------------------------------------------------------------
// Last resort
// ---------------------------------------------------------------------------

// A relay that degrades beats a relay that disappears. Baileys emits from
// timers and socket callbacks, where an unhandled throw ends the process with
// nothing on screen -- the window is started with `cmd /c`, so it closes on
// exit and takes the stack trace with it. All the dashboard could then say
// was "relay not running", which describes the symptom and hides the cause.
// Staying up keeps /status answering, which is what puts the reason on the
// page.
process.on("uncaughtException", (err) => {
  lastError = `Relay error: ${err && err.message ? err.message : err}`;
  console.error("[whatsapp-relay] Uncaught exception:", err);
});

process.on("unhandledRejection", (err) => {
  lastError = `Relay error: ${err && err.message ? err.message : err}`;
  console.error("[whatsapp-relay] Unhandled rejection:", err);
});

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

// Listen first, connect second, and never the other way round. Waiting for
// WhatsApp before opening the port meant one failed first attempt left no
// HTTP server at all -- so the dashboard could not even ask what was wrong
// and reported the relay as not running, which is a different problem with a
// different fix. The port answering is what lets /status explain itself.
//
// It also makes the port the single-instance lock, which matters more than
// the HTTP server does: WhatsApp allows one connection per linked device, so
// a second relay on the same .whatsapp-session/ does not sit harmlessly
// beside the first -- the two take the slot from each other, and the symptom
// is a relay that connects, works for a moment, and drops, over and over.
const server = app.listen(PORT, "127.0.0.1", () => {
  console.log(`[whatsapp-relay] Listening on http://127.0.0.1:${PORT}`);
  connectSafely();
});

server.on("error", (err) => {
  if (err && err.code === "EADDRINUSE") {
    // The one place where disappearing is right. "Never fatal" is about not
    // losing a working relay to a stray exception; a second relay is not a
    // working relay, it is the thing that breaks the first one. Staying
    // alive here is what the uncaughtException handler would otherwise do,
    // and it is exactly wrong.
    console.error(
      `[whatsapp-relay] Port ${PORT} is already in use, so another relay is `
      + "already running. Stopping, because two relays sharing one WhatsApp "
      + "session take the connection from each other -- which looks like a "
      + "relay that keeps dropping."
    );
    process.exit(1);
  }
  console.error("[whatsapp-relay] Server error:", err);
  process.exit(1);
});
