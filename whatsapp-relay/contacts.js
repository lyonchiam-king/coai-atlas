/**
 * The phone's own WhatsApp contacts and chats, gathered as WhatsApp hands them
 * over after linking (history sync) and as they change. Kept apart from
 * server.js so it can be tested without Baileys.
 *
 * This is how a list that exists only inside WhatsApp -- people the owner has
 * chatted with but never saved anywhere else -- becomes importable.
 */
const fs = require("fs");

// A contact or chat id -> "+digits", or "" when no phone number is known.
// Groups, broadcasts and bare @lid ids (WhatsApp's anonymous form) are skipped:
// a list built from them would message nobody.
function phoneOf(...ids) {
  for (const id of ids) {
    const s = String(id || "");
    if (s.endsWith("@s.whatsapp.net")) {
      const digits = s.split("@")[0].split(":")[0].replace(/[^0-9]/g, "");
      if (digits.length >= 8 && digits.length <= 15) return "+" + digits;
    }
  }
  return "";
}

// Long values from protobuf arrive as objects; seconds either way.
function seconds(ts) {
  if (ts == null) return 0;
  const n = Number(typeof ts === "object" && ts.toString ? ts.toString() : ts);
  return Number.isFinite(n) && n > 0 ? Math.floor(n > 1e12 ? n / 1000 : n) : 0;
}

class ContactBook {
  constructor(file) {
    this.file = file;
    this.byPhone = {};
    try {
      this.byPhone = JSON.parse(fs.readFileSync(file, "utf8")) || {};
    } catch {
      this.byPhone = {};
    }
    this._dirty = false;
  }

  _merge(phone, fields) {
    if (!phone) return;
    const cur = this.byPhone[phone] || { phone, name: "", notify: "", last_chat: 0 };
    if (fields.name) cur.name = fields.name;           // what the owner saved them as
    if (fields.notify && !cur.notify) cur.notify = fields.notify;   // what they call themselves
    if (fields.last_chat && fields.last_chat > cur.last_chat) cur.last_chat = fields.last_chat;
    this.byPhone[phone] = cur;
    this._dirty = true;
  }

  addContacts(list) {
    for (const c of list || []) {
      this._merge(phoneOf(c.jid, c.id, c.phoneNumber), { name: c.name, notify: c.notify || c.verifiedName });
    }
  }

  addChats(list) {
    for (const c of list || []) {
      this._merge(phoneOf(c.id, c.pnJid, c.jid), {
        name: c.name,
        last_chat: seconds(c.conversationTimestamp || c.lastMessageRecvTimestamp),
      });
    }
  }

  save() {
    if (!this._dirty) return;
    try {
      fs.writeFileSync(this.file, JSON.stringify(this.byPhone));
      this._dirty = false;
    } catch (err) {
      console.error("[whatsapp-relay] Could not save contacts:", err.message || err);
    }
  }

  list() {
    return Object.values(this.byPhone);
  }
}

module.exports = { ContactBook, phoneOf, seconds };
