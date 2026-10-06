const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { resolveMedia } = require("./media");

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "media-"));
fs.writeFileSync(path.join(dir, "intro.mp4"), "x");
fs.writeFileSync(path.join(dir, "flyer.PNG"), "x");
fs.writeFileSync(path.join(dir, "notes.txt"), "x");

test("files in the media folder resolve with their kind", () => {
  assert.deepStrictEqual(resolveMedia("intro.mp4", dir), { path: path.join(dir, "intro.mp4"), kind: "video" });
  assert.strictEqual(resolveMedia("flyer.PNG", dir).kind, "image");
});

test("anything that is not a bare media file name is refused", () => {
  for (const bad of ["../secret.mp4", "/etc/passwd", "C:\\\\Windows\\\\x.mp4", "sub/intro.mp4",
                     "notes.txt", "missing.mp4", "", null, "..", "intro.mp4/.."]) {
    assert.strictEqual(resolveMedia(bad, dir), null, String(bad));
  }
});
