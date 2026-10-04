const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { extractInbound, Inbox } = require("./inbox");

const msg = (over = {}) => ({
  key: { id: "A1", remoteJid: "60123456789@s.whatsapp.net", fromMe: false },
  message: { conversation: "how much?" },
  messageTimestamp: 1790000000,
  ...over,
});

test("a plain reply becomes an inbound item with an E.164 phone", () => {
  assert.deepStrictEqual(extractInbound(msg()), {
    id: "A1", phone: "+60123456789", text: "how much?", at: 1790000000,
  });
});

test("our own sends, groups and status are ignored", () => {
  assert.strictEqual(extractInbound(msg({ key: { id: "x", remoteJid: "60123456789@s.whatsapp.net", fromMe: true } })), null);
  assert.strictEqual(extractInbound(msg({ key: { id: "x", remoteJid: "123-456@g.us", fromMe: false } })), null);
  assert.strictEqual(extractInbound(msg({ key: { id: "x", remoteJid: "status@broadcast", fromMe: false } })), null);
});

test("a device suffix on the jid is stripped", () => {
  const r = extractInbound(msg({ key: { id: "x", remoteJid: "60123456789:12@s.whatsapp.net", fromMe: false } }));
  assert.strictEqual(r.phone, "+60123456789");
});

test("an @lid contact is used only when the real number is supplied", () => {
  const lid = { id: "x", remoteJid: "999@lid", fromMe: false };
  assert.strictEqual(extractInbound(msg({ key: lid })), null);
  const r = extractInbound(msg({ key: { ...lid, senderPn: "60123456789@s.whatsapp.net" } }));
  assert.strictEqual(r.phone, "+60123456789");
});

test("captions and extended text are read; empty messages are not items", () => {
  assert.strictEqual(extractInbound(msg({ message: { extendedTextMessage: { text: "STOP" } } })).text, "STOP");
  assert.strictEqual(extractInbound(msg({ message: { imageMessage: { caption: "see" } } })).text, "see");
  assert.strictEqual(extractInbound(msg({ message: { stickerMessage: {} } })), null);
});

test("inbox dedupes, survives a restart, and removes only what was acked", () => {
  const f = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "inbox-")), "i.json");
  const a = new Inbox(f);
  a.add({ id: "1", phone: "+60123456789", text: "STOP", at: 1 });
  a.add({ id: "1", phone: "+60123456789", text: "STOP", at: 1 });
  a.add({ id: "2", phone: "+60123456789", text: "hi", at: 2 });
  const b = new Inbox(f);                 // relay restarted
  assert.strictEqual(b.list().length, 2);
  assert.strictEqual(b.ack(["1", "nope"]), 1);
  assert.deepStrictEqual(new Inbox(f).list().map((x) => x.id), ["2"]);
});
