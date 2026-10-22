/* ととのい黄金比 フロントエンド(ビルド不要・素の JavaScript) */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
const fmt1 = (n) => Number(n).toFixed(1);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const PHASE = {
  sauna: { key: "sauna_min", label: "サウナ" },
  water: { key: "water_min", label: "水風呂" },
  rest: { key: "rest_min", label: "外気浴" },
};
const FATIGUE_LABEL = { low: "低", mid: "中", high: "高" };

const state = {
  view: "record",
  user: null,
  editingId: null,
  includeSample: true,
  sessions: [], // 新しい順
  golden: null,
  dashboard: null,
  calMonth: startOfMonth(new Date()),
  charts: {},
};

try {
  const saved = localStorage.getItem("sauna.includeSample");
  if (saved !== null) state.includeSample = saved === "true";
} catch (_) { /* ストレージが使えなくても動く */ }

/* ---------- 共通 ---------- */

function startOfMonth(d) {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

function todayStr() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

async function api(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...options,
  });
  if (!res.ok) {
    let msg = "通信に失敗しました。もう一度お試しください。";
    let warned = null;
    try {
      const body = await res.json();
      if (res.status === 409 && body.detail && Array.isArray(body.detail.warnings)) {
        const err = new Error("入力内容を確認してください。");
        err.warnings = body.detail.warnings;
        warned = err;
      } else if (typeof body.detail === "string") msg = body.detail;
      else if (Array.isArray(body.detail)) msg = "入力内容に誤りがあります。数値の範囲を確認してください。";
    } catch (_) { /* 本文なし */ }
    if (warned) throw warned;
    if (res.status === 401) showAuthScreen();
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}

const qs = () => `include_sample=${state.includeSample}`;

let toastTimer = null;
function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2800);
}

function showError(id, message) {
  const el = $(id);
  el.textContent = message || "";
  el.hidden = !message;
}

/* ---------- 認証(ログイン/登録は login.html / register.html) ---------- */

function showAuthScreen() {
  state.user = null;
  Object.assign(state, { sessions: [], golden: null, dashboard: null });
  location.replace("/login.html");
}

function greetingFor(user) {
  const name = (user.display_name || user.email || "").trim();
  const h = new Date().getHours();
  const hello = h < 5 ? "こんばんは" : h < 11 ? "おはようございます" : h < 18 ? "こんにちは" : "こんばんは";
  return name ? `${hello}、${name}さん。` : `${hello}。`;
}

function todayLine() {
  const d = new Date();
  const w = ["日", "月", "火", "水", "木", "金", "土"][d.getDay()];
  return `今日は${d.getMonth() + 1}月${d.getDate()}日(${w})。体調はどうですか? 教えてもらえれば、いまの自分に合う時間をいっしょに探します。`;
}

async function startAuthenticatedApp(user) {
  state.user = user;
  Object.assign(state, { sessions: [], golden: null, dashboard: null });
  const name = user.display_name || user.email;
  $("#account-name").textContent = name;
  $("#account-avatar").textContent = Array.from(name)[0] || "";
  $("#greeting").textContent = greetingFor(user);
  $("#today-line").textContent = todayLine();
  $("#app").hidden = false;
  $("#consent-banner").hidden = user.consent_current !== false;
  $("#consent-status").textContent = user.consent_current === false
    ? "データの扱いへの同意が、新しい説明文に更新されていません。"
    : "データの扱いに同意いただいています。";
  showView("record");
  const restored = restoreDraft();
  resumeTimer();
  await refresh();
  if (restored) toast("途中まで入力していた内容を戻しました");
  try {
    const flash = sessionStorage.getItem("sauna.flash");
    if (flash) { sessionStorage.removeItem("sauna.flash"); toast(flash); }
  } catch (_) { /* 表示できなくても続行 */ }
}

$("#logout-button").addEventListener("click", async () => {
  try { await api("/auth/logout", { method: "POST" }); }
  catch (_) { /* サーバー側ですでに期限切れでも、画面はログアウトする */ }
  showAuthScreen();
});

function dots(score) {
  return `<span class="dots" aria-label="ととのい度 ${score}/5">${[1, 2, 3, 4, 5]
    .map((i) => `<i class="${i <= score ? "on" : ""}"></i>`).join("")}</span>`;
}

function phaseBar(g) {
  const total = g.sauna_min + g.water_min + g.rest_min || 1;
  const w = (v) => ((v / total) * 100).toFixed(1);
  return `<div class="bar" role="img" aria-label="サウナ${fmt1(g.sauna_min)}分、水風呂${fmt1(g.water_min)}分、休憩${fmt1(g.rest_min)}分">
    <i class="s" style="flex:${w(g.sauna_min)}"></i>
    <i class="w" style="flex:${w(g.water_min)}"></i>
    <i class="r" style="flex:${w(g.rest_min)}"></i>
  </div>`;
}

function legend3(g) {
  return `<div class="legend3">
    <div class="s"><span class="k">サウナ</span><span class="v">${fmt1(g.sauna_min)}<small> 分</small></span></div>
    <div class="w"><span class="k">水風呂</span><span class="v">${fmt1(g.water_min)}<small> 分</small></span></div>
    <div class="r"><span class="k">休憩</span><span class="v">${fmt1(g.rest_min)}<small> 分</small></span></div>
  </div>`;
}

/* ---------- 画面切り替え ---------- */

function showView(name) {
  state.view = name;
  $$(".tab").forEach((t) => t.classList.toggle("is-active", t.dataset.view === name));
  $$(".view").forEach((v) => { v.hidden = v.id !== `view-${name}`; });
  renderActive();
  if (name === "predict") loadReview();
  window.scrollTo({ top: 0 });
}

/* ---------- 予測の答え合わせ ---------- */

async function loadReview() {
  try { renderReview(await api("/predictions/review")); }
  catch (err) { $("#review-card").innerHTML = `<p class="error">${esc(err.message)}</p>`; }
}

function renderReview(r) {
  const sm = r.summary;
  const box = $("#review-card");
  if (sm.n_predictions === 0) {
    box.innerHTML = `<h2>予測の答え合わせ</h2>
      <p class="meta lead">予測を出すと、ここに残ります。その日に実際の記録を保存すると、予測と結果を並べて見られます。</p>`;
    return;
  }
  const head = sm.mean_abs_error != null
    ? `<p class="lead">予測と実際のととのい度は、平均で <strong class="em">${sm.mean_abs_error.toFixed(2)}</strong> ずれました(${sm.n_matched}回分)。${Math.abs(sm.mean_error) >= 0.3 ? (sm.mean_error > 0 ? "実際のほうが、予測より高めに出ています。" : "実際のほうが、予測より低めに出ています。") : "高めにも低めにも、大きく偏ってはいません。"}</p>
       <p class="meta">少ない回数の平均は、ぶれます。回数が増えるほど、信頼できます。</p>`
    : `<p class="meta lead">予測と実際を並べられたのは ${sm.n_matched} 回です。${r.min_review}回たまると、平均のずれを出します。</p>`;
  const rows = r.items.slice(0, 10).map((i) => {
    const a = i.actual;
    return `<tr>
      <td>${esc(i.for_date)}</td>
      <td>${fmt1(i.plan.sauna_min)}/${fmt1(i.plan.water_min)}/${fmt1(i.plan.rest_min)}</td>
      <td>${fmt1(i.predicted_score)}</td>
      <td>${a ? `${fmt1(a.sauna_min)}/${fmt1(a.water_min)}/${fmt1(a.rest_min)}` : "—"}</td>
      <td>${a ? a.score : "—"}</td>
      <td>${a ? (i.error > 0 ? "+" : "") + i.error.toFixed(1) : "—"}</td></tr>`;
  }).join("");
  box.innerHTML = `<h2>予測の答え合わせ</h2>${head}
    <div class="table-wrap"><table>
      <thead><tr><th>日付</th><th>提案(サウナ/水風呂/休憩)</th><th>予測</th><th>実際の時間</th><th>実際</th><th>差</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <p class="meta">「実際」は、同じ日に保存したあなたの記録です(サンプルは除きます)。</p>`;
}

function renderActive() {
  if (state.view === "record") renderRecent();
  if (state.view === "golden") renderGolden();
  if (state.view === "history") renderHistory();
  if (state.view === "dashboard") renderDashboard();
}

$$(".tab").forEach((t) => t.addEventListener("click", () => showView(t.dataset.view)));

async function refresh() {
  if (!state.user) return;
  try {
    const [sessions, golden, dashboard] = await Promise.all([
      api(`/sessions?${qs()}`),
      api(`/analysis/golden?${qs()}`),
      api(`/analysis/dashboard?${qs()}`),
    ]);
    Object.assign(state, { sessions, golden, dashboard });
  } catch (err) {
    toast(err.message);
    return;
  }
  renderFacilityList();
  renderSampleControls();
  renderActive();
}

/* ---------- ストップウォッチ ---------- */

const timer = { phase: null, startedAt: 0, row: null, tick: null, wakeLock: null };

function fmtClock(ms) {
  const s = Math.floor(ms / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function updateTimerUi() {
  const running = timer.phase;
  $("#timer-display").textContent = running ? fmtClock(Date.now() - timer.startedAt) : "00:00";
  const label = $("#timer-phase");
  label.textContent = running ? `${PHASE[running].label}中` : "待機中";
  label.dataset.phase = running || "";
  $$(".phase-btn").forEach((b) => b.classList.toggle("is-running", b.dataset.phase === running));
}

function rowHasData(row) {
  return $$("input", row).some((i) => i.value !== "");
}

function stopPhase() {
  if (!timer.phase) return;
  const minutes = (Date.now() - timer.startedAt) / 60000;
  const field = $(`[data-field="${PHASE[timer.phase].key}"]`, timer.row);
  if (field) { field.value = fmt1(minutes); saveDraft(); }
  timer.phase = null;
  storeRemove("sauna.timer");
  clearInterval(timer.tick);
  if (timer.wakeLock) { timer.wakeLock.release().catch(() => {}); timer.wakeLock = null; }
  updateTimerUi();
}

function startPhase(phase) {
  stopPhase();
  const rows = $$(".set-row");
  let row = rows[rows.length - 1];
  // サウナを押し直したとき、直前のセットに記録があれば次のセットに進む
  if (phase === "sauna" && rowHasData(row)) row = addSetRow();
  timer.phase = phase;
  timer.startedAt = Date.now();
  timer.row = row;
  storeSet("sauna.timer", { uid: state.user && state.user.id, phase, startedAt: timer.startedAt, rowIndex: $$(".set-row").indexOf(row) });
  timer.tick = setInterval(updateTimerUi, 250);
  if (navigator.wakeLock) {
    navigator.wakeLock.request("screen").then((l) => { timer.wakeLock = l; }).catch(() => {});
  }
  updateTimerUi();
}

$$(".phase-btn").forEach((b) =>
  b.addEventListener("click", () => {
    if (timer.phase === b.dataset.phase) stopPhase();
    else startPhase(b.dataset.phase);
  })
);

/* ---------- セット入力 ---------- */

function renumberSets() {
  const rows = $$(".set-row");
  rows.forEach((row, i) => {
    $(".set-no", row).textContent = `${i + 1}セット目`;
    $(".rm", row).hidden = rows.length === 1;
  });
}

function addSetRow(values = {}) {
  const row = document.createElement("div");
  row.className = "set-row";
  const input = (k, id) => `<div><label data-k="${k}">${PHASE[k].label}</label>
    <input type="number" inputmode="decimal" min="0" step="0.5" data-field="${PHASE[k].key}" value="${values[PHASE[k].key] ?? ""}" aria-label="${PHASE[k].label} (分)"></div>`;
  row.innerHTML = `<div class="set-no"></div>${input("sauna")}${input("water")}${input("rest")}
    <button type="button" class="btn ghost rm">削除</button>`;
  $(".rm", row).addEventListener("click", () => {
    if (timer.row === row) stopPhase();
    row.remove();
    renumberSets();
    saveDraft();
  });
  $("#sets").appendChild(row);
  renumberSets();
  saveDraft();
  return row;
}

$("#add-set").addEventListener("click", () => addSetRow());

function resetForm() {
  $("#sets").innerHTML = "";
  addSetRow();
  $$('input[name="score"]').forEach((r) => { r.checked = false; });
  $("#f-note").value = "";
}

/* ---------- 下書き・タイマーの保存(この端末のブラウザだけ) ---------- */

function storeSet(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) { /* 保存できなくても続行 */ }
}
function storeGet(key) {
  try { return JSON.parse(localStorage.getItem(key) || "null"); } catch (_) { return null; }
}
function storeRemove(key) {
  try { localStorage.removeItem(key); } catch (_) { /* 続行 */ }
}

function readDraft() {
  const val = (id) => $(id).value;
  return {
    uid: state.user && state.user.id,
    date: val("#f-date"), facility: val("#f-facility"),
    sauna_temp: val("#f-sauna-temp"), water_temp: val("#f-water-temp"),
    sleep: val("#f-sleep"), hr: val("#f-hr"),
    fatigue: ($('input[name="fatigue"]:checked') || {}).value,
    ate: ($('input[name="ate"]:checked') || {}).value,
    score: ($('input[name="score"]:checked') || {}).value || "",
    note: val("#f-note"),
    sets: $$(".set-row").map((row) => ({
      sauna_min: $('[data-field="sauna_min"]', row).value,
      water_min: $('[data-field="water_min"]', row).value,
      rest_min: $('[data-field="rest_min"]', row).value,
    })),
  };
}

let draftTimer = null;
function saveDraft() {
  if (state.editingId || !state.user) return; // 編集中は、過去の記録を下書きに混ぜない
  clearTimeout(draftTimer);
  draftTimer = setTimeout(() => {
    const d = readDraft();
    const dirty = d.sets.some((x) => x.sauna_min || x.water_min || x.rest_min) || d.score || d.note.trim();
    if (dirty) storeSet("sauna.draft", d); else storeRemove("sauna.draft");
  }, 300);
}

function clearDraft() {
  clearTimeout(draftTimer);
  storeRemove("sauna.draft");
}

function restoreDraft() {
  const d = storeGet("sauna.draft");
  if (!d || !state.user || d.uid !== state.user.id || state.editingId) return false;
  $("#f-date").value = d.date || todayStr();
  $("#f-facility").value = d.facility || "";
  $("#f-sauna-temp").value = d.sauna_temp ?? "";
  $("#f-water-temp").value = d.water_temp ?? "";
  $("#f-sleep").value = d.sleep ?? "";
  $("#f-hr").value = d.hr ?? "";
  if (d.fatigue) setRadio("fatigue", d.fatigue);
  if (d.ate) setRadio("ate", d.ate);
  if (d.score) setRadio("score", d.score);
  $("#f-note").value = d.note || "";
  $("#sets").innerHTML = "";
  (d.sets && d.sets.length ? d.sets : [{}]).forEach((x) => addSetRow(x));
  return true;
}

// 閉じる前に測りかけだったタイマーを、続きから動かす(3時間を過ぎたものは捨てる)
function resumeTimer() {
  const t = storeGet("sauna.timer");
  if (!t || !state.user || t.uid !== state.user.id || !PHASE[t.phase]) { storeRemove("sauna.timer"); return false; }
  if (Date.now() - t.startedAt > 3 * 3600 * 1000 || t.startedAt > Date.now()) { storeRemove("sauna.timer"); return false; }
  let rows = $$(".set-row");
  while (rows.length <= t.rowIndex) { addSetRow(); rows = $$(".set-row"); }
  timer.phase = t.phase;
  timer.startedAt = t.startedAt;
  timer.row = rows[Math.max(0, t.rowIndex)];
  timer.tick = setInterval(updateTimerUi, 250);
  if (navigator.wakeLock) {
    navigator.wakeLock.request("screen").then((l) => { timer.wakeLock = l; }).catch(() => {});
  }
  updateTimerUi();
  return true;
}

$("#session-form").addEventListener("input", saveDraft);
$("#session-form").addEventListener("change", saveDraft);

/* ---------- 記録フォーム ---------- */

function numOrNull(id) {
  const v = $(id).value.trim();
  return v === "" ? null : Number(v);
}

function collectBody() {
  const sets = $$(".set-row")
    .filter(rowHasData)
    .map((row) => {
      const get = (key) => {
        const v = $(`[data-field="${key}"]`, row).value;
        return v === "" ? 0 : Number(v);
      };
      return { sauna_min: get("sauna_min"), water_min: get("water_min"), rest_min: get("rest_min") };
    });
  if (sets.length === 0) return { error: "セットの時間を1つ以上入力してください。" };

  const score = $('input[name="score"]:checked');
  if (!score) return { error: "ととのい度を選んでください。" };

  return {
    body: {
      visited_on: $("#f-date").value || todayStr(),
      facility: $("#f-facility").value.trim(),
      sauna_temp: numOrNull("#f-sauna-temp"),
      water_temp: numOrNull("#f-water-temp"),
      sleep_hours: numOrNull("#f-sleep"),
      resting_hr: numOrNull("#f-hr"),
      fatigue: $('input[name="fatigue"]:checked').value,
      ate_before: $('input[name="ate"]:checked').value === "yes",
      score: Number(score.value),
      note: $("#f-note").value.trim(),
      sets,
    },
  };
}

function showWarnings(list) {
  const box = $("#form-warn");
  box.hidden = !list || list.length === 0;
  $("#form-warn-list").innerHTML = (list || []).map((w) => `<li>${esc(w)}</li>`).join("");
}

// confirmed=true: 警告を見たうえで「このまま保存」を選んだ場合
async function saveSession(confirmed) {
  stopPhase();
  showError("#form-error", "");
  const { body, error } = collectBody();
  if (error) { showWarnings(null); return showError("#form-error", error); }

  const editing = state.editingId;
  const query = confirmed ? "?confirmed=true" : "";
  try {
    if (editing) await api(`/sessions/${editing}${query}`, { method: "PUT", body: JSON.stringify(body) });
    else await api(`/sessions${query}`, { method: "POST", body: JSON.stringify(body) });
  } catch (err) {
    if (err.warnings) { showWarnings(err.warnings); return $("#form-warn").scrollIntoView({ block: "nearest", behavior: "smooth" }); }
    showWarnings(null);
    return showError("#form-error", err.message);
  }
  showWarnings(null);
  toast(editing ? "記録を更新しました" : "記録しました");
  clearDraft();
  endEdit();
  await refresh();
}

$("#session-form").addEventListener("submit", (e) => { e.preventDefault(); saveSession(false); });
$("#warn-save").addEventListener("click", () => saveSession(true));
$("#warn-fix").addEventListener("click", () => { showWarnings(null); $("#f-date").focus(); });

/* ---------- 過去の記録の編集 ---------- */

function setRadio(name, value) {
  const el = $(`input[name="${name}"][value="${value}"]`);
  if (el) el.checked = true;
}

function startEdit(id) {
  const s = state.sessions.find((x) => x.id === id);
  if (!s) return;
  stopPhase();
  state.editingId = id;
  showWarnings(null);
  showError("#form-error", "");
  $("#f-date").value = s.visited_on;
  $("#f-facility").value = s.facility || "";
  $("#f-sauna-temp").value = s.sauna_temp ?? "";
  $("#f-water-temp").value = s.water_temp ?? "";
  $("#f-sleep").value = s.sleep_hours ?? "";
  $("#f-hr").value = s.resting_hr ?? "";
  setRadio("fatigue", s.fatigue);
  setRadio("ate", s.ate_before ? "yes" : "no");
  setRadio("score", s.score);
  $("#f-note").value = s.note || "";
  $("#sets").innerHTML = "";
  s.sets.forEach((x) => addSetRow(x));
  $("#form-title").textContent = "記録を編集する";
  $("#edit-banner").hidden = false;
  $("#submit-btn").textContent = "変更を保存する";
  $("#session-form").scrollIntoView({ behavior: "smooth", block: "start" });
}

function endEdit() {
  state.editingId = null;
  clearTimeout(draftTimer);
  $("#form-title").textContent = "今日のサウナ";
  $("#edit-banner").hidden = true;
  $("#submit-btn").textContent = "今日の記録を保存する";
  resetForm();
  $("#f-date").value = todayStr();
}

$("#cancel-edit").addEventListener("click", () => { showWarnings(null); showError("#form-error", ""); endEdit(); });

function renderFacilityList() {
  const names = [...new Set(state.sessions.map((s) => s.facility).filter(Boolean))];
  $("#facility-list").innerHTML = names.map((n) => `<option value="${esc(n)}"></option>`).join("");
}

/* ---------- 最近の記録 ---------- */

const MIN_SESSIONS = 20; // backend/app/ml.py の MIN_SESSIONS と同じ

function weekStartStr() {
  const d = new Date();
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); // 月曜はじまり
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function goalBlock() {
  const goal = Number((storeGet("sauna.weeklyGoal") || {}).n) || 0;
  const week = weekStartStr();
  const done = state.sessions.filter((x) => !x.is_sample && x.visited_on >= week).length;
  const opts = [0, 1, 2, 3, 4, 5, 7].map((n) => `<option value="${n}"${n === goal ? " selected" : ""}>${n ? `週に${n}回` : "決めない"}</option>`).join("");
  const text = goal
    ? (done >= goal ? `今週は ${done}回。目標を達成しました。` : `今週は ${done} / ${goal} 回。あと ${goal - done}回です。`)
    : "今週の目標を決めると、ここに進み具合が出ます。";
  return `<div class="goal"><label for="goal-select">今週の目標</label><select id="goal-select">${opts}</select><p class="meta">${text}</p></div>`;
}

function renderNextCard() {
  const own = state.sessions.filter((x) => !x.is_sample).length;
  const left = Math.max(0, MIN_SESSIONS - own);
  const pct = Math.min(100, Math.round((own / MIN_SESSIONS) * 100));
  const lead = left > 0
    ? `あなたの記録があと <strong class="em">${left}回</strong> たまると、その日の体調に合わせた時間をお伝えできます。`
    : "記録が十分にたまりました。「予測」から、今日の体調に合わせた時間を探せます。";
  $("#next-card").innerHTML = `<h2>あなたに合う時間の提案</h2>
    <p class="meta lead">${lead}</p>
    <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="${MIN_SESSIONS}" aria-valuenow="${Math.min(own, MIN_SESSIONS)}" aria-label="記録の数"><i style="width:${pct}%"></i></div>
    <span class="num-small">${Math.min(own, MIN_SESSIONS)} / ${MIN_SESSIONS} 回</span>
    <button type="button" class="btn ghost" data-go="${left > 0 ? "golden" : "predict"}">${left > 0 ? "黄金比を見る" : "予測を試す"}</button>
    ${goalBlock()}`;
  $("#goal-select").addEventListener("change", (e) => { storeSet("sauna.weeklyGoal", { n: Number(e.target.value) }); renderNextCard(); });
  const go = $("#next-card [data-go]");
  go.addEventListener("click", () => showView(go.dataset.go));
}

function renderRecent() {
  renderNextCard();
  const box = $("#recent");
  if (state.sessions.length === 0) {
    box.innerHTML = `<p class="empty">まだ記録がありません。左のフォームから最初のセッションを記録するか、ページ下の「サンプルを追加」で動作を試せます。</p>`;
    return;
  }
  box.innerHTML = state.sessions.slice(0, 8).map(sessionItemHtml).join("");
  wireItemButtons(box);
}

function sessionItemHtml(s) {
  const sets = s.sets.map((x) => `${fmt1(x.sauna_min)}/${fmt1(x.water_min)}/${fmt1(x.rest_min)}`).join(" → ");
  const env = [
    s.sauna_temp != null ? `室温${s.sauna_temp}℃` : null,
    s.water_temp != null ? `水風呂${s.water_temp}℃` : null,
    s.sleep_hours != null ? `睡眠${s.sleep_hours}時間` : null,
    s.resting_hr != null ? `安静時心拍${s.resting_hr}bpm` : null,
    s.fatigue ? `疲労${FATIGUE_LABEL[s.fatigue]}` : null,
  ].filter(Boolean).join(" ");
  return `<div class="recent-item" data-id="${s.id}">
    <div class="recent-title">${esc(s.visited_on)} ${esc(s.facility) || "施設未入力"} ${dots(s.score)}
      ${s.is_sample ? '<span class="tag">サンプル</span>' : ""}</div>
    <div class="recent-meta">${s.sets.length}セット(サウナ/水風呂/休憩) ${esc(sets)}${env ? `<br>${esc(env)}` : ""}${s.note ? `<br>メモ: ${esc(s.note)}` : ""}</div>
    <div class="recent-actions">
      <button type="button" class="btn ghost edit" data-id="${s.id}">編集</button>
      <button type="button" class="btn ghost del" data-id="${s.id}">削除</button>
    </div>
  </div>`;
}

function wireItemButtons(box) {
  $$(".edit", box).forEach((btn) => btn.addEventListener("click", () => {
    showView("record");
    startEdit(Number(btn.dataset.id));
  }));

  // 削除は2回押しで確定(ブラウザの確認ダイアログは使わない)
  $$(".del", box).forEach((btn) => {
    let armed = false;
    let t = null;
    btn.addEventListener("click", async () => {
      if (!armed) {
        armed = true;
        btn.textContent = "本当に削除";
        btn.classList.add("danger");
        t = setTimeout(() => { armed = false; btn.textContent = "削除"; btn.classList.remove("danger"); }, 4000);
        return;
      }
      clearTimeout(t);
      try {
        await api(`/sessions/${btn.dataset.id}`, { method: "DELETE" });
        if (state.editingId === Number(btn.dataset.id)) endEdit();
        toast("削除しました");
        await refresh();
      } catch (err) {
        toast(err.message);
      }
    });
  });
}

/* ---------- 記録一覧(全件・絞り込み) ---------- */

const HISTORY_PAGE = 20;
const historyFilter = { text: "", from: "", to: "", minScore: 0, shown: HISTORY_PAGE };

function filteredSessions() {
  const q = historyFilter.text.trim().toLowerCase();
  return state.sessions.filter((s) =>
    (!q || `${s.facility} ${s.note}`.toLowerCase().includes(q)) &&
    (!historyFilter.from || s.visited_on >= historyFilter.from) &&
    (!historyFilter.to || s.visited_on <= historyFilter.to) &&
    s.score >= historyFilter.minScore);
}

function renderHistory() {
  const list = filteredSessions();
  const shown = list.slice(0, historyFilter.shown);
  $("#history-count").textContent = `${list.length}件${list.length !== state.sessions.length ? `(全${state.sessions.length}件中)` : ""}`;
  const box = $("#history-list");
  box.innerHTML = shown.length
    ? shown.map(sessionItemHtml).join("")
    : `<p class="empty">${state.sessions.length ? "条件に合う記録がありません。" : "まだ記録がありません。"}</p>`;
  wireItemButtons(box);
  $("#history-more").hidden = list.length <= shown.length;
}

function bindHistoryControls() {
  const upd = () => { historyFilter.shown = HISTORY_PAGE; renderHistory(); };
  $("#h-text").addEventListener("input", (e) => { historyFilter.text = e.target.value; upd(); });
  $("#h-from").addEventListener("change", (e) => { historyFilter.from = e.target.value; upd(); });
  $("#h-to").addEventListener("change", (e) => { historyFilter.to = e.target.value; upd(); });
  $("#h-score").addEventListener("change", (e) => { historyFilter.minScore = Number(e.target.value); upd(); });
  $("#h-reset").addEventListener("click", () => {
    Object.assign(historyFilter, { text: "", from: "", to: "", minScore: 0, shown: HISTORY_PAGE });
    ["#h-text", "#h-from", "#h-to"].forEach((id) => { $(id).value = ""; });
    $("#h-score").value = "0";
    renderHistory();
  });
  $("#history-more").addEventListener("click", () => { historyFilter.shown += HISTORY_PAGE; renderHistory(); });
}
bindHistoryControls();

/* ---------- 黄金比 ---------- */

function goldenBlock(g, emptyText) {
  if (!g) return `<p class="empty">${emptyText}</p>`;
  const ratio = g.ratio
    ? `<p class="meta">水風呂を1とすると、サウナ ${g.ratio.sauna} : 水風呂 1 : 休憩 ${g.ratio.rest}</p>`
    : "";
  return `${phaseBar(g)}${legend3(g)}${ratio}
    <p class="meta">ととのい度4以上の ${g.n_sessions} 件の平均(セット1回あたり)</p>`;
}

function renderGolden() {
  const box = $("#golden-body");
  const g = state.golden;
  if (!g) return;
  const th = g.high_score_threshold;
  const emptyHigh = `ととのい度${th}以上の記録がまだありません。`;

  const facilities = g.by_facility.length
    ? g.by_facility.map((f) => `<div class="fac-row">
        <div class="fac-head">${esc(f.facility)}<span>${f.n_sessions}回 / 平均 ${fmt1(f.avg_score)}</span></div>
        ${f.golden ? `${phaseBar(f.golden)}<p class="meta">${esc(f.golden.label)}(${f.golden.n_sessions}件)</p>`
                   : `<p class="meta">${emptyHigh}</p>`}
      </div>`).join("")
    : `<p class="empty">施設名を入力した記録があると、施設ごとの黄金比がここに出ます。</p>`;

  box.innerHTML = `
    <div class="card">
      <h2>あなたのととのい黄金比</h2>
      ${goldenBlock(g.overall, emptyHigh)}
    </div>
    <div class="compare">
      <div class="card">
        <h2>水風呂 ${g.cold_water.threshold_c}℃以下</h2>
        <p class="meta">${g.cold_water.n_sessions} 件の記録</p>
        ${goldenBlock(g.cold_water.golden, emptyHigh)}
      </div>
      <div class="card">
        <h2>水風呂 ${g.warm_water.threshold_c}℃より上</h2>
        <p class="meta">${g.warm_water.n_sessions} 件の記録</p>
        ${goldenBlock(g.warm_water.golden, emptyHigh)}
      </div>
    </div>
    ${conditionCards(g.by_condition, th)}
    <div class="card">
      <h2>施設ごとの黄金比</h2>
      ${facilities}
    </div>`;
}

function conditionCards(c, th) {
  if (!c) return "";
  const dims = c.dimensions.map((d) => {
    const rows = d.groups.map((r) => {
      const g = r.golden;
      const best = r.best
        ? `ベスト: ${esc(r.best.visited_on)} ${esc(r.best.facility) || "施設未入力"}(ととのい度${r.best.score}・${fmt1(r.best.sauna_min)}/${fmt1(r.best.water_min)}/${fmt1(r.best.rest_min)}分)`
        : "";
      return `<div class="cond-row">
        <div class="fac-head">${esc(r.label)}<span>${r.n_sessions}回${r.avg_score != null ? ` / 平均 ${fmt1(r.avg_score)}` : ""}${r.n_sessions && !r.reliable ? ' <em class="tag">参考程度</em>' : ""}</span></div>
        ${g ? `${phaseBar(g)}<p class="meta">${esc(g.label)}(ととのい度${th}以上 ${g.n_sessions}件)</p>`
            : `<p class="meta">${r.n_sessions ? `ととのい度${th}以上の記録がまだありません。` : "この条件の記録はまだありません。"}</p>`}
        ${best ? `<p class="meta">${best}</p>` : ""}
      </div>`;
    }).join("");
    return `<div class="card"><h2>${esc(d.label)}で比べると</h2>${rows}</div>`;
  }).join("");

  const gaps = c.gaps.length
    ? `<div class="card gaps"><h2>あと少しで比べられる条件</h2>
        <p class="meta lead">条件ごとの記録が${c.min_group}回以上あると、体調による違いを見やすくなります。次に行く日の目安にしてください。</p>
        <ul>${c.gaps.map((x) => `<li><strong>${esc(x.dimension)}: ${esc(x.label)}</strong> ${x.n_sessions}回${x.needed ? `(あと${x.needed}回)` : ""}${x.note ? ` ${esc(x.note)}` : ""}</li>`).join("")}</ul>
      </div>`
    : "";
  return `<div class="cond-intro"><h2>体調によって、ととのう時間は変わる?</h2>
      <p class="meta lead">睡眠・疲れ・食事で記録を分けて、それぞれの黄金比を出しました。「参考程度」の印は、記録が少なくて数字がぶれやすい条件です。少ない記録から、体調の影響を決めつけないでください。</p></div>
    <div class="cols">${dims}</div>${gaps}`;
}

/* ---------- 予測 ---------- */

$("#predict-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  showError("#predict-error", "");
  const body = {
    sauna_temp: numOrNull("#p-sauna-temp"),
    water_temp: numOrNull("#p-water-temp"),
    sleep_hours: numOrNull("#p-sleep"),
    fatigue: $('input[name="p-fatigue"]:checked').value,
    ate_before: $('input[name="p-ate"]:checked').value === "yes",
    n_sets: Number($("#p-sets").value) || 3,
    drank: $("#p-drank").checked,
    unwell: $("#p-unwell").checked,
    restricted: $("#p-restricted").checked,
    for_date: todayStr(),
  };
  const plan = [numOrNull("#p-plan-sauna"), numOrNull("#p-plan-water"), numOrNull("#p-plan-rest")];
  if (plan.every((v) => v !== null)) {
    body.plan = { sauna_min: plan[0], water_min: plan[1], rest_min: plan[2] };
  }

  const btn = $("#predict-form button[type=submit]");
  btn.disabled = true;
  try {
    const result = await api(`/predict?${qs()}`, { method: "POST", body: JSON.stringify(body) });
    renderPrediction(result);
    if (result.status === "ok") loadReview();
  } catch (err) {
    showError("#predict-error", err.message);
  } finally {
    btn.disabled = false;
  }
});

function renderPrediction(r) {
  const box = $("#predict-result");
  destroyChart("predict");

  if (r.status === "blocked") {
    box.innerHTML = `<div class="card caution">
      <h2>今日は予測を出しません</h2>
      <p>${esc(r.message)}</p>
      <p class="meta">当てはまった状態: ${r.reasons.map(esc).join("、")}</p>
    </div>`;
    return;
  }

  if (r.status === "insufficient_data") {
    box.innerHTML = `<div class="card">
      <h2>まだ予測できません</h2>
      <p>${esc(r.message)}</p>
      <p class="meta">データが少ないうちは、「黄金比」画面の平均のほうが安定します。動作を試すだけなら、ページ下からサンプルを追加できます。</p>
    </div>`;
    return;
  }

  const b = r.best;
  const pct = b.p_high == null ? null : Math.round(b.p_high * 100);
  const m = r.metrics;
  const better = m.improvement_vs_baseline != null && m.improvement_vs_baseline > 0;
  const metricsLine = `未知の記録に対する平均誤差は ${m.cv_mae} 点(平均点を答えるだけの基準は ${m.baseline_mae} 点)。` +
    (better ? ` 基準より ${Math.round(m.improvement_vs_baseline * 100)}% 小さい誤差です。` : " 基準を下回れていないので、参考程度にしてください。");

  const planBlock = r.plan ? `<div class="card">
      <h2>入力した配分の予測</h2>
      <p>サウナ${fmt1(r.plan.sauna_min)}分・水風呂${fmt1(r.plan.water_min)}分・休憩${fmt1(r.plan.rest_min)}分</p>
      <div class="big-score">${fmt1(r.plan.predicted_score)}<small> / 5</small></div>
      ${r.plan.p_high != null ? `<p class="meta">ととのい度4以上になる確率 ${Math.round(r.plan.p_high * 100)}%</p>` : ""}
    </div>` : "";

  box.innerHTML = `
    <div class="card best">
      <h2>今日のおすすめ配分</h2>
      ${legend3({ sauna_min: b.sauna_min, water_min: b.water_min, rest_min: b.rest_min })}
      <div class="big-score">${fmt1(b.predicted_score)}<small> / 5 の予測</small></div>
      ${pct == null ? "" : `<p><span class="pill ${pct >= 60 ? "ok" : ""}">ととのい度4以上になる確率 ${pct}%</span></p>`}
      <p class="meta">${esc(metricsLine)}(学習に使った記録 ${m.n_sessions} 件)</p>
    </div>
    ${planBlock}
    <div class="card">
      <h2>サウナ時間による予測の変化</h2>
      <p class="meta">水風呂 ${fmt1(b.water_min)} 分・休憩 ${fmt1(b.rest_min)} 分に固定した場合</p>
      <div class="chart-box short"><canvas id="chart-predict" aria-label="サウナ時間と予測ととのい度"></canvas></div>
    </div>
    <div class="card">
      <h2>ほかの候補</h2>
      <div class="table-wrap"><table>
        <thead><tr><th>サウナ</th><th>水風呂</th><th>休憩</th><th>予測</th><th>確率</th></tr></thead>
        <tbody>${r.top.map((t) => `<tr><td>${fmt1(t.sauna_min)}分</td><td>${fmt1(t.water_min)}分</td><td>${fmt1(t.rest_min)}分</td>
          <td>${fmt1(t.predicted_score)}</td><td>${t.p_high == null ? "-" : Math.round(t.p_high * 100) + "%"}</td></tr>`).join("")}</tbody>
      </table></div>
    </div>`;

  if (!window.Chart) return; // グラフ用ライブラリを読み込めない環境では数値だけ表示する
  setChartDefaults();
  state.charts.predict = new Chart($("#chart-predict"), {
    type: "line",
    data: {
      labels: r.sauna_curve.map((p) => `${p.sauna_min}`),
      datasets: [{
        data: r.sauna_curve.map((p) => p.predicted_score),
        borderColor: css("--sauna"),
        backgroundColor: css("--sauna"),
        pointRadius: 2,
        tension: 0.25,
      }],
    },
    options: chartOptions({ xTitle: "サウナ (分)", yTitle: "予測ととのい度", yMin: 1, yMax: 5 }),
  });
}

/* ---------- 医学論文 RAG ---------- */

$$('.research-example').forEach((button) => button.addEventListener('click', () => {
  $('#research-question').value = button.textContent;
  $('#research-question').focus();
}));

$('#research-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  showError('#research-error', '');
  const question = $('#research-question').value.trim();
  if (question.length < 2) return showError('#research-error', '質問を2文字以上で入力してください。');

  const button = $('#research-form button[type=submit]');
  button.disabled = true;
  button.textContent = 'PubMed を検索中…';
  try {
    const result = await api('/research', { method: 'POST', body: JSON.stringify({ question }) });
    renderResearch(result);
  } catch (err) {
    showError('#research-error', err.message);
  } finally {
    button.disabled = false;
    button.textContent = '論文を検索する';
  }
});

function renderResearch(result) {
  const box = $('#research-result');
  const cards = result.articles.map((article) => {
    const types = article.publication_types.length
      ? `<div class="paper-types">${article.publication_types.map((type) => `<span class="tag">${esc(type)}</span>`).join('')}</div>`
      : '';
    const meta = [article.journal, article.published, article.authors].filter(Boolean).map(esc).join(' · ');
    return `<article class="card paper-card">
      <div class="paper-head">
        <span class="label">PMID ${esc(article.pmid)}</span>
        ${types}
      </div>
      <h3>${esc(article.title)}</h3>
      ${meta ? `<p class="meta">${meta}</p>` : ''}
      <div class="paper-excerpt"><span class="label">抄録からの関連抜粋（原文）</span><p>${esc(article.abstract_excerpt)}</p></div>
      <p><a class="paper-link" href="${esc(article.url)}" target="_blank" rel="noopener noreferrer">PubMedで原著・抄録を確認する <span aria-hidden="true">↗</span></a></p>
    </article>`;
  }).join('');

  box.innerHTML = `<div class="card research-summary">
      <h2>検索結果</h2>
      <p>${esc(result.answer)}</p>
      <p class="research-safety">${esc(result.safety_note)}</p>
    </div>
    ${cards || `<div class="card"><p class="empty">別の言い回しで試してください。例: 「sauna safety」「cold water immersion recovery」</p></div>`}`;
}

/* ---------- 振り返り ---------- */

function setChartDefaults() {
  if (!window.Chart) return;
  Chart.defaults.color = css("--muted");
  Chart.defaults.borderColor = css("--line");
  Chart.defaults.font.family = css("--font-body");
}

function destroyChart(name) {
  if (state.charts[name]) { state.charts[name].destroy(); delete state.charts[name]; }
}

function chartOptions({ xTitle, yTitle, yMin, yMax, stacked = false, legend = false } = {}) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: legend, position: "bottom" } },
    scales: {
      x: { stacked, title: { display: !!xTitle, text: xTitle }, grid: { display: false }, ticks: { maxTicksLimit: 8 } },
      y: { stacked, min: yMin, max: yMax, beginAtZero: yMin === undefined, title: { display: !!yTitle, text: yTitle }, ticks: { precision: 0 } },
    },
  };
}

function movingAverage(values, window = 5) {
  return values.map((_, i) => {
    const slice = values.slice(Math.max(0, i - window + 1), i + 1);
    return Math.round((slice.reduce((a, b) => a + b, 0) / slice.length) * 100) / 100;
  });
}

function renderDashboard() {
  const d = state.dashboard;
  if (!d) return;

  $("#stats").innerHTML = d.total_sessions === 0
    ? `<div class="stat" style="grid-column:1/-1"><div class="k">記録</div><div class="v">0 件</div><p class="meta">記録を追加すると、ここにグラフが出ます。</p></div>`
    : `<div class="stat"><div class="k">記録回数</div><div class="v">${d.total_sessions}</div></div>
       <div class="stat"><div class="k">総セット数</div><div class="v">${d.total_sets}</div></div>
       <div class="stat"><div class="k">平均ととのい度</div><div class="v">${fmt1(d.avg_score)}</div></div>`;

  setChartDefaults();
  ["trend", "monthly", "sets"].forEach(destroyChart);
  if (d.total_sessions > 0 && window.Chart) {
    const scores = d.score_trend.map((p) => p.score);
    state.charts.trend = new Chart($("#chart-trend"), {
      type: "line",
      data: {
        labels: d.score_trend.map((p) => p.date),
        datasets: [
          { label: "ととのい度", data: scores, borderColor: css("--sauna"), backgroundColor: css("--sauna"), pointRadius: 3, showLine: false },
          { label: "直近5回の平均", data: movingAverage(scores), borderColor: css("--ink") || "#000", borderWidth: 2, pointRadius: 0, tension: 0.3 },
        ],
      },
      options: chartOptions({ yMin: 0.5, yMax: 5.5, legend: true }),
    });
    state.charts.monthly = new Chart($("#chart-monthly"), {
      type: "bar",
      data: {
        labels: d.monthly_counts.map((m) => m.month),
        datasets: [{ label: "回数", data: d.monthly_counts.map((m) => m.count), backgroundColor: css("--water") }],
      },
      options: chartOptions({}),
    });
    state.charts.sets = new Chart($("#chart-sets"), {
      type: "bar",
      data: {
        labels: d.set_distribution.map((s) => `${s.set_no}セット目`),
        datasets: [
          { label: "サウナ", data: d.set_distribution.map((s) => s.sauna), backgroundColor: css("--sauna") },
          { label: "水風呂", data: d.set_distribution.map((s) => s.water), backgroundColor: css("--water") },
          { label: "休憩", data: d.set_distribution.map((s) => s.rest), backgroundColor: css("--rest") },
        ],
      },
      options: chartOptions({ stacked: true, yTitle: "分", legend: true }),
    });
  }
  renderCalendar();
}

function renderCalendar() {
  const d = state.dashboard;
  const m = state.calMonth;
  const year = m.getFullYear();
  const month = m.getMonth();
  $("#cal-title").textContent = `${year}年${month + 1}月`;

  const byDate = {};
  (d ? d.calendar : []).forEach((c) => { (byDate[c.date] ||= []).push(c); });

  const first = new Date(year, month, 1).getDay();
  const days = new Date(year, month + 1, 0).getDate();
  const cells = ["日", "月", "火", "水", "木", "金", "土"].map((w) => `<div class="cal-dow">${w}</div>`);
  for (let i = 0; i < first; i++) cells.push(`<div class="cal-cell blank"></div>`);
  for (let day = 1; day <= days; day++) {
    const key = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const items = byDate[key];
    if (!items) {
      cells.push(`<div class="cal-cell"><span class="d">${day}</span></div>`);
      continue;
    }
    const top = items[0];
    const title = items.map((i) => `${i.facility || "施設未入力"} ととのい度${i.score}`).join(" / ");
    cells.push(`<div class="cal-cell has" style="--s:${Number(top.score)}" title="${esc(title)}">
      <span class="d">${day}</span>
      <span class="f">${esc(top.facility) || "—"}</span>
      <span class="sc">★${Number(top.score)}${items.length > 1 ? ` +${items.length - 1}` : ""}</span>
    </div>`);
  }
  $("#calendar").innerHTML = cells.join("");
}

$("#cal-prev").addEventListener("click", () => {
  state.calMonth = new Date(state.calMonth.getFullYear(), state.calMonth.getMonth() - 1, 1);
  renderCalendar();
});
$("#cal-next").addEventListener("click", () => {
  state.calMonth = new Date(state.calMonth.getFullYear(), state.calMonth.getMonth() + 1, 1);
  renderCalendar();
});

/* ---------- サンプルデータ ---------- */

function renderSampleControls() {
  const nSample = state.sessions.filter((s) => s.is_sample).length;
  const box = $("#sample-controls");
  box.innerHTML = `<div class="row">
      <button type="button" class="btn ghost" id="sample-add">サンプルを追加 (60件)</button>
      ${nSample > 0 ? `<button type="button" class="btn ghost" id="sample-clear">サンプル ${nSample} 件を削除</button>
      <label><input type="checkbox" id="sample-include" ${state.includeSample ? "checked" : ""}> 分析にサンプルを含める</label>` : ""}
    </div>
    <p class="meta" style="margin-top:8px">サンプルは動作確認用の架空データです。自分の記録とは区別され、いつでも削除できます。</p>`;

  $("#sample-add").addEventListener("click", async () => {
    try { await api("/sample?n=60", { method: "POST" }); toast("サンプルを追加しました"); await refresh(); }
    catch (err) { toast(err.message); }
  });
  const clear = $("#sample-clear");
  if (clear) clear.addEventListener("click", async () => {
    try { const r = await api("/sample", { method: "DELETE" }); toast(`サンプル ${r.deleted} 件を削除しました`); await refresh(); }
    catch (err) { toast(err.message); }
  });
  const inc = $("#sample-include");
  if (inc) inc.addEventListener("change", async () => {
    state.includeSample = inc.checked;
    try { localStorage.setItem("sauna.includeSample", String(inc.checked)); } catch (_) { /* 保存できなくても続行 */ }
    await refresh();
  });
}

/* ---------- データ・設定 ---------- */

$("#consent-agree").addEventListener("click", async () => {
  try {
    await api("/account/consent", { method: "POST", body: JSON.stringify({ agree: true }) });
    $("#consent-banner").hidden = true;
    $("#consent-status").textContent = "データの扱いに同意いただいています。";
    toast("ありがとうございます");
  } catch (err) { toast(err.message); }
});

// --- CSV取り込み(先に確認 → 取り込む)
let importBytes = null;

async function postImport(commit) {
  const res = await fetch(`/api/import/sessions?commit=${commit}`, {
    method: "POST",
    headers: { "Content-Type": "text/csv" },
    credentials: "same-origin",
    body: importBytes,
  });
  let data = null;
  try { data = await res.json(); } catch (_) { /* 本文なし */ }
  if (res.status === 401) showAuthScreen();
  if (!res.ok) throw new Error((data && typeof data.detail === "string") ? data.detail : "取り込みに失敗しました。");
  return data;
}

function renderImportReport(r) {
  const box = $("#import-report");
  const list = (items, fmt) => items.slice(0, 20).map((x) => `<li>${fmt(x)}</li>`).join("")
    + (items.length > 20 ? `<li>ほか ${items.length - 20} 件</li>` : "");
  box.innerHTML = `<div class="import-summary">
      <p><strong>${r.importable}件</strong>を取り込めます。${r.duplicates ? `同じ記録 ${r.duplicates}件はすでにあるため、取り込みません。` : ""}</p>
      ${r.errors.length ? `<p class="error">${r.errors.length}件は、形式が正しくないため取り込めません。</p>
        <ul class="plain">${list(r.errors, (e) => `記録 ${esc(e.record)}(${e.line}行目): ${esc(e.reason)}`)}</ul>` : ""}
      ${r.warnings.length ? `<p class="warn-text">${r.warnings.length}件は、打ち間違いかもしれない値を含みます(取り込みは可能です)。</p>
        <ul class="plain">${list(r.warnings, (w) => `記録 ${esc(w.record)}(${w.line}行目): ${esc(w.warnings.join(" "))}`)}</ul>` : ""}
      ${r.importable ? `<button type="button" class="btn primary" id="import-commit">${r.importable}件を取り込む</button>` : ""}
    </div>`;
  const go = $("#import-commit");
  if (go) go.addEventListener("click", async () => {
    go.disabled = true;
    try {
      const done = await postImport(true);
      box.innerHTML = `<p><strong>${done.imported}件</strong>を取り込みました。</p>`;
      $("#import-file").value = "";
      importBytes = null;
      toast(`${done.imported}件を取り込みました`);
      await refresh();
    } catch (err) { go.disabled = false; toast(err.message); }
  });
}

$("#import-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  $("#import-report").innerHTML = "";
  if (!file) return;
  importBytes = await file.arrayBuffer();
  try { renderImportReport(await postImport(false)); }
  catch (err) { $("#import-report").innerHTML = `<p class="error">${esc(err.message)}</p>`; }
});

// --- パスワード変更
$("#password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  showError("#pw-error", "");
  const body = { current_password: $("#pw-current").value, new_password: $("#pw-new").value };
  if (!body.current_password) return showError("#pw-error", "いまのパスワードを入力してください。");
  if (body.new_password.length < 8) return showError("#pw-error", "新しいパスワードは8文字以上にしてください。");
  try {
    await api("/account/password", { method: "POST", body: JSON.stringify(body) });
    $("#pw-current").value = "";
    $("#pw-new").value = "";
    toast("パスワードを変更しました");
  } catch (err) { showError("#pw-error", err.message); }
});

// --- 削除(パスワード + 2回押し)
function armedButton(btn, label, action) {
  let armed = false;
  let t = null;
  btn.addEventListener("click", async () => {
    showError("#del-error", "");
    if (!$("#del-password").value) return showError("#del-error", "パスワードを入力してください。");
    if (!armed) {
      armed = true;
      btn.textContent = "本当に削除する";
      btn.classList.add("danger");
      t = setTimeout(() => { armed = false; btn.textContent = label; btn.classList.remove("danger"); }, 5000);
      return;
    }
    clearTimeout(t);
    armed = false;
    btn.textContent = label;
    btn.classList.remove("danger");
    try { await action($("#del-password").value); }
    catch (err) { showError("#del-error", err.message); }
  });
}

armedButton($("#del-data"), "記録をすべて削除", async (password) => {
  const r = await api("/account/delete-data", { method: "POST", body: JSON.stringify({ password }) });
  $("#del-password").value = "";
  endEdit();
  toast(`記録 ${r.deleted} 件を削除しました`);
  await refresh();
});

armedButton($("#del-account"), "アカウントごと削除", async (password) => {
  await api("/account/delete", { method: "POST", body: JSON.stringify({ password }) });
  location.replace("/login.html");
});

/* ---------- 起動 ---------- */

async function boot() {
  try {
    const response = await fetch("/api/auth/me", { credentials: "same-origin" });
    if (!response.ok) { showAuthScreen(); return; }
    await startAuthenticatedApp(await response.json());
  } catch (_) {
    $("#app").hidden = false;
    toast("サーバーに接続できません。起動しているか確認してください。");
  }
}

$("#f-date").value = todayStr();
resetForm();
updateTimerUi();
boot();

// 端末のテーマが変わったらグラフの色を取り直す
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => renderActive());
