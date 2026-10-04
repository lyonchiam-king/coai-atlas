const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { ContactBook, phoneOf, seconds } = require("./contacts");

const tmp = () => path.join(fs.mkdtempSync(path.join(os.tmpdir(), "cb-")), "c.json");

test("only real phone numbers become contacts", () => {
  assert.strictEqual(phoneOf("60123456789@s.whatsapp.net"), "+60123456789");
  assert.strictEqual(phoneOf("60123456789:3@s.whatsapp.net"), "+60123456789");
  assert.strictEqual(phoneOf("123-456@g.us"), "");
  assert.strictEqual(phoneOf("999@lid"), "");
  assert.strictEqual(phoneOf("999@lid", "60123456789@s.whatsapp.net"), "+60123456789");
});

test("saved name, their own name and last chat are merged per number", () => {
  const b = new ContactBook(tmp());
  b.addContacts([{ id: "60123456789@s.whatsapp.net", name: "Aisha Bakery", notify: "Aisha" }]);
  b.addChats([{ id: "60123456789@s.whatsapp.net", conversationTimestamp: { toString: () => "1600000000" } }]);
  b.addChats([{ id: "60123456789@s.whatsapp.net", conversationTimestamp: 1500000000 }]);   // older: ignored
  b.addContacts([{ id: "777@lid", lid: "777@lid", name: "Anon" }]);
  assert.deepStrictEqual(b.list(), [{ phone: "+60123456789", name: "Aisha Bakery", notify: "Aisha", last_chat: 1600000000 }]);
});

test("the book survives a restart", () => {
  const f = tmp();
  const a = new ContactBook(f);
  a.addContacts([{ id: "60111111111@s.whatsapp.net", notify: "Tan" }]);
  a.save();
  assert.strictEqual(new ContactBook(f).list()[0].notify, "Tan");
});

test("timestamps in ms or protobuf Longs come out as seconds", () => {
  assert.strictEqual(seconds(1600000000000), 1600000000);
  assert.strictEqual(seconds({ toString: () => "1600000000" }), 1600000000);
  assert.strictEqual(seconds(undefined), 0);
});
