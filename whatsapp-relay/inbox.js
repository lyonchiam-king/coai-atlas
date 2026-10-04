/**
 * Inbound replies, kept apart from server.js so they can be tested without
 * Baileys or a network.
 */
const fs = require("fs");

const MAX_HELD = 500;

// Pull the plain text out of the shapes WhatsApp wraps it in.
function textOf(message) {
  if (!message) return "";
  return (
    message.conversation ||
    message.extendedTextMessage?.text ||
    message.imageMessage?.caption ||
    message.videoMessage?.caption ||
    message.ephemeralMessage?.message?.conversation ||
    message.ephemeralMessage?.message?.extendedTextMessage?.text ||
    ""
  );
}

// One WhatsApp message -> { id, phone, text, at } or null.
//
// Null for anything that is not a person writing to us: our own sends,
// groups, status updates, and messages with no readable text. Newer WhatsApp
// addresses some contacts by an opaque @lid id rather than a number; the number
// is only usable when WhatsApp also supplies it. Never guess one -- a reply
// matched to the wrong lead could turn a STOP into a message to a stranger.
function extractInbound(m) {
  const key = m?.key;
  if (!key || key.fromMe || !key.id) return null;
  let jid = key.remoteJid || "";
  if (jid.endsWith("@lid")) jid = key.senderPn || key.remoteJidAlt || "";
  if (!jid.endsWith("@s.whatsapp.net")) return null;
  const digits = jid.split("@")[0].split(":")[0].replace(/[^0-9]/g, "");
  if (digits.length < 8 || digits.length > 15) return null;
  const text = String(textOf(m.message)).trim();
  if (!text) return null;
  const ts = Number(m.messageTimestamp);
  return { id: key.id, phone: "+" + digits, text, at: ts > 0 ? ts : Math.floor(Date.now() / 1000) };
}

class Inbox {
  constructor(file) {
    this.file = file;
    this.items = [];
    try {
      this.items = JSON.parse(fs.readFileSync(file, "utf8"));
    } catch {
      this.items = [];
    }
  }

  _save() {
    try {
      fs.writeFileSync(this.file, JSON.stringify(this.items));
    } catch (err) {
      console.error("[whatsapp-relay] Could not save inbox:", err.message || err);
    }
  }

  add(item) {
    if (this.items.some((x) => x.id === item.id)) return;
    this.items.push(item);
    // Bounded, dropping the oldest. Unacked this long means the swarm is down.
    while (this.items.length > MAX_HELD) this.items.shift();
    this._save();
  }

  list() {
    return this.items.slice();
  }

  ack(ids) {
    const before = this.items.length;
    const gone = new Set(ids);
    this.items = this.items.filter((x) => !gone.has(x.id));
    if (this.items.length !== before) this._save();
    return before - this.items.length;
  }
}

module.exports = { extractInbound, Inbox, textOf };
