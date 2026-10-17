// 専用環境の Python で backend のモジュールを実行する(npm run corpus / eval から使う)
//   使い方: node scripts/pymodule.js app.evaluate [引数...]
const { spawnSync } = require("child_process");
const { BACKEND, VENV_PYTHON, ensureVenv } = require("./common");

ensureVenv();
const [moduleName, ...args] = process.argv.slice(2);
if (!moduleName) {
  console.error("実行するモジュール名を指定してください(例: app.evaluate)");
  process.exit(1);
}
const r = spawnSync(VENV_PYTHON, ["-m", moduleName, ...args], { cwd: BACKEND, stdio: "inherit" });
process.exit(r.status ?? 1);
