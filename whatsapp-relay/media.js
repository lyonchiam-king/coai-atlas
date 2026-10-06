/**
 * Which file Atlas asked to attach, resolved safely. Kept apart from server.js
 * so the refusal rules can be tested without Baileys.
 */
const fs = require("fs");
const path = require("path");

const KINDS = { ".mp4": "video", ".jpg": "image", ".jpeg": "image", ".png": "image" };

// A media file name -> { path, kind }, or null. Bare names only: anything with
// a folder in it, or that would resolve outside the media folder, is refused.
function resolveMedia(name, dir) {
  const n = String(name || "");
  if (!n || n !== path.basename(n) || n.includes("..") || /[\\/:]/.test(n)) return null;
  const kind = KINDS[path.extname(n).toLowerCase()];
  if (!kind) return null;
  const root = path.resolve(dir);
  const full = path.resolve(root, n);
  if (path.dirname(full) !== root || !fs.existsSync(full)) return null;
  return { path: full, kind };
}

module.exports = { resolveMedia };
