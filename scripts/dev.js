// npm run dev: サーバーを起動する。8000 番が使用中なら、空いている次のポートを使う。
const net = require("net");
const { spawn } = require("child_process");
const { BACKEND, VENV_PYTHON, ensureVenv, fail } = require("./common");

ensureVenv();

function isFree(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.once("listening", () => server.close(() => resolve(true)));
    server.listen(port, "127.0.0.1");
  });
}

async function pickPort(start) {
  for (let p = start; p < start + 20; p++) {
    if (await isFree(p)) return p;
  }
  return null;
}

(async () => {
  const wanted = Number(process.env.PORT) || 8000;
  const port = await pickPort(wanted);
  if (port === null) fail(`ポート ${wanted} からの20個がすべて使用中です。PORT=9000 npm run dev のように指定してください。`);
  if (port !== wanted) console.log(`ポート ${wanted} は使用中のため、${port} を使います。`);

  console.log(`\n  ブラウザで  http://127.0.0.1:${port}  を開いてください。(止めるときは Ctrl+C)\n`);

  const child = spawn(VENV_PYTHON, ["-m", "uvicorn", "app.main:app", "--reload", "--port", String(port)], {
    cwd: BACKEND,
    stdio: "inherit",
  });
  child.on("exit", (code) => process.exit(code ?? 0));
  for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => child.kill(sig));
})();
