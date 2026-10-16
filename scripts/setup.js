// npm run setup: Python の専用環境(.venv)を作り、必要なライブラリを入れる
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");
const { ROOT, VENV, VENV_PYTHON, findSystemPython, fail } = require("./common");

const sys = findSystemPython();
if (!sys) {
  fail("Python 3.10 以上が見つかりません。https://www.python.org/downloads/ から入れてください。");
}

if (!fs.existsSync(VENV_PYTHON)) {
  console.log(`Python ${sys.version} で専用環境(.venv)を作ります...`);
  const args = sys.cmd === "py" ? ["-3", "-m", "venv", VENV] : ["-m", "venv", VENV];
  const r = spawnSync(sys.cmd, args, { stdio: "inherit" });
  if (r.status !== 0) fail("専用環境の作成に失敗しました。");
} else {
  console.log("専用環境(.venv)は作成済みです。ライブラリだけ更新します。");
}

console.log("ライブラリをインストールします(数分かかることがあります)...");
const pip = spawnSync(VENV_PYTHON, ["-m", "pip", "install", "-r", path.join(ROOT, "requirements.txt")], {
  stdio: "inherit",
});
if (pip.status !== 0) fail("ライブラリのインストールに失敗しました。上のエラーを確認してください。");

console.log("\n準備ができました。  npm run dev  で起動できます。");
