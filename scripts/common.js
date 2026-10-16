// setup / dev / test で共通の処理(Node.js 標準モジュールのみ。追加の npm install は不要)
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const ROOT = path.resolve(__dirname, "..");
const BACKEND = path.join(ROOT, "backend");
const VENV = path.join(ROOT, ".venv");
const IS_WIN = process.platform === "win32";

// 専用環境(.venv)の中の python。macOS/Linux は bin/、Windows は Scripts/
const VENV_PYTHON = path.join(VENV, IS_WIN ? "Scripts" : "bin", IS_WIN ? "python.exe" : "python");

function fail(message) {
  console.error(`\n${message}\n`);
  process.exit(1);
}

// システムの Python を探す(python3 → python → Windows の py)。3.10 以上が必要。
function findSystemPython() {
  const candidates = IS_WIN ? ["py", "python", "python3"] : ["python3", "python"];
  for (const cmd of candidates) {
    const r = spawnSync(cmd, ["-c", "import sys; print('%d.%d' % sys.version_info[:2])"], { encoding: "utf8" });
    if (r.status !== 0) continue;
    const [major, minor] = r.stdout.trim().split(".").map(Number);
    if (major > 3 || (major === 3 && minor >= 10)) return { cmd, version: r.stdout.trim() };
  }
  return null;
}

function ensureVenv() {
  if (!fs.existsSync(VENV_PYTHON)) {
    fail("専用環境(.venv)がまだありません。先に  npm run setup  を実行してください。");
  }
}

module.exports = { ROOT, BACKEND, VENV, VENV_PYTHON, findSystemPython, ensureVenv, fail };
