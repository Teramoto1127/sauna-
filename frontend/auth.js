/* ログイン/新規登録ページ用。成功したらアプリ本体(/)へ移動する。 */
"use strict";

const $ = (sel) => document.querySelector(sel);

function showError(id, message) {
  const el = $(id);
  el.textContent = message || "";
  el.hidden = !message;
}

function flash(message) {
  try { sessionStorage.setItem("sauna.flash", message); } catch (_) { /* 保存できなくても続行 */ }
}

async function submit(path, body, errorId, button) {
  showError(errorId, "");
  button.disabled = true;
  try {
    const res = await fetch(`/api${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      let msg = "通信に失敗しました。もう一度お試しください。";
      try {
        const data = await res.json();
        if (typeof data.detail === "string") msg = data.detail;
        else if (Array.isArray(data.detail)) msg = "入力内容に誤りがあります。メールアドレスの形式と、パスワードが8文字以上か、同意にチェックが入っているかを確認してください。";
      } catch (_) { /* 本文なし */ }
      throw new Error(msg);
    }
    const user = await res.json();
    const claimed = Number(user.claimed_legacy_sessions || 0);
    if (path === "/auth/register") {
      flash(claimed ? `アカウントを作成し、既存記録 ${claimed} 件を引き継ぎました` : "アカウントを作成しました");
    }
    location.replace("/");
  } catch (err) {
    showError(errorId, err.message);
    button.disabled = false;
  }
}

const loginForm = $("#login-form");
if (loginForm) {
  loginForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submit("/auth/login", {
      email: $("#login-email").value.trim(),
      password: $("#login-password").value,
    }, "#login-error", loginForm.querySelector("button[type=submit]"));
  });
}

const registerForm = $("#register-form");
if (registerForm) {
  registerForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!$("#register-agree").checked) return showError("#register-error", "データの扱いへの同意にチェックを入れてください。");
    submit("/auth/register", {
      display_name: $("#register-name").value.trim(),
      email: $("#register-email").value.trim(),
      password: $("#register-password").value,
      agree: $("#register-agree").checked,
    }, "#register-error", registerForm.querySelector("button[type=submit]"));
  });
}

// すでにログイン済みなら、そのままアプリへ
fetch("/api/auth/me", { credentials: "same-origin" })
  .then((res) => { if (res.ok) location.replace("/"); })
  .catch(() => { /* オフラインならそのままフォームを表示 */ });
