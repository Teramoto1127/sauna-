// npm test: バックエンドのテスト(pytest)を実行する
const { spawnSync } = require("child_process");
const { BACKEND, VENV_PYTHON, ensureVenv } = require("./common");

ensureVenv();
const r = spawnSync(VENV_PYTHON, ["-m", "pytest", "-q"], { cwd: BACKEND, stdio: "inherit" });
process.exit(r.status ?? 1);
