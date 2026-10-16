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
    try {
      const body = await res.json();
      if (typeof body.detail === "string") msg = body.detail;
      else if (Array.isArray(body.detail)) msg = "入力内容に誤りがあります。数値の範囲を確認してください。";
    } catch (_) { /* 本文なし */ }
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

/* ---------- 認証 ---------- */

function showAuthView(name) {
  $$(".auth-tab").forEach((tab) => {
    const active = tab.dataset.authView === name;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  $("#login-form").hidden = name !== "login";
  $("#register-form").hidden = name !== "register";
  showError("#login-error", "");
  showError("#register-error", "");
}

function showAuthScreen() {
  state.user = null;
  Object.assign(state, { sessions: [], golden: null, dashboard: null });
  $("#auth-screen").hidden = false;
  $("#app-header").hidden = true;
  $("#app-main").hidden = true;
  $("#app-footer").hidden = true;
}

async function startAuthenticatedApp(user) {
  state.user = user;
  Object.assign(state, { sessions: [], golden: null, dashboard: null });
  $("#account-name").textContent = user.display_name || user.email;
  $("#auth-screen").hidden = true;
  $("#app-header").hidden = false;
  $("#app-main").hidden = false;
  $("#app-footer").hidden = false;
  showView("record");
  await refresh();
}

async function submitAuth(path, body, errorId, button) {
  showError(errorId, "");
  button.disabled = true;
  try {
    const user = await api(path, { method: "POST", body: JSON.stringify(body) });
    await startAuthenticatedApp(user);
    const claimed = Number(user.claimed_legacy_sessions || 0);
    toast(path === "/auth/register"
      ? (claimed ? `アカウントを作成し、既存記録 ${claimed} 件を引き継ぎました` : "アカウントを作成しました")
      : "ログインしました");
  } catch (err) {
    showError(errorId, err.message);
  } finally {
    button.disabled = false;
  }
}

$$(".auth-tab").forEach((tab) => tab.addEventListener("click", () => showAuthView(tab.dataset.authView)));

$("#login-form").addEventListener("submit", (event) => {
  event.preventDefault();
  submitAuth("/auth/login", {
    email: $("#login-email").value.trim(),
    password: $("#login-password").value,
  }, "#login-error", $("#login-form button[type=submit]"));
});

$("#register-form").addEventListener("submit", (event) => {
  event.preventDefault();
  submitAuth("/auth/register", {
    display_name: $("#register-name").value.trim(),
    email: $("#register-email").value.trim(),
    password: $("#register-password").value,
  }, "#register-error", $("#register-form button[type=submit]"));
});

$("#logout-button").addEventListener("click", async () => {
  try { await api("/auth/logout", { method: "POST" }); }
  catch (_) { /* サーバー側ですでに期限切れでも、画面はログアウトする */ }
  showAuthScreen();
  showAuthView("login");
  $("#login-password").value = "";
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
  window.scrollTo({ top: 0 });
}

function renderActive() {
  if (state.view === "record") renderRecent();
  if (state.view === "golden") renderGolden();
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
  if (field) field.value = fmt1(minutes);
  timer.phase = null;
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
  });
  $("#sets").appendChild(row);
  renumberSets();
  return row;
}

$("#add-set").addEventListener("click", () => addSetRow());

function resetForm() {
  $("#sets").innerHTML = "";
  addSetRow();
  $$('input[name="score"]').forEach((r) => { r.checked = false; });
  $("#f-note").value = "";
}

/* ---------- 記録フォーム ---------- */

function numOrNull(id) {
  const v = $(id).value.trim();
  return v === "" ? null : Number(v);
}

$("#session-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  stopPhase();
  showError("#form-error", "");

  const sets = $$(".set-row")
    .filter(rowHasData)
    .map((row) => {
      const get = (key) => {
        const v = $(`[data-field="${key}"]`, row).value;
        return v === "" ? 0 : Number(v);
      };
      return { sauna_min: get("sauna_min"), water_min: get("water_min"), rest_min: get("rest_min") };
    });
  if (sets.length === 0) return showError("#form-error", "セットの時間を1つ以上入力してください。");

  const score = $('input[name="score"]:checked');
  if (!score) return showError("#form-error", "ととのい度を選んでください。");

  const body = {
    visited_on: $("#f-date").value || todayStr(),
    facility: $("#f-facility").value.trim(),
    sauna_temp: numOrNull("#f-sauna-temp"),
    water_temp: numOrNull("#f-water-temp"),
    sleep_hours: numOrNull("#f-sleep"),
    fatigue: $('input[name="fatigue"]:checked').value,
    ate_before: $('input[name="ate"]:checked').value === "yes",
    score: Number(score.value),
    note: $("#f-note").value.trim(),
    sets,
  };

  try {
    await api("/sessions", { method: "POST", body: JSON.stringify(body) });
  } catch (err) {
    return showError("#form-error", err.message);
  }
  toast("記録しました");
  resetForm();
  await refresh();
});

function renderFacilityList() {
  const names = [...new Set(state.sessions.map((s) => s.facility).filter(Boolean))];
  $("#facility-list").innerHTML = names.map((n) => `<option value="${esc(n)}"></option>`).join("");
}

/* ---------- 最近の記録 ---------- */

function renderRecent() {
  const box = $("#recent");
  if (state.sessions.length === 0) {
    box.innerHTML = `<p class="empty">まだ記録がありません。上のフォームから最初のセッションを記録するか、ページ下の「サンプルを追加」で動作を試せます。</p>`;
    return;
  }
  box.innerHTML = state.sessions.slice(0, 8).map((s) => {
    const sets = s.sets.map((x) => `${fmt1(x.sauna_min)}/${fmt1(x.water_min)}/${fmt1(x.rest_min)}`).join(" → ");
    const env = [
      s.sauna_temp != null ? `室温${s.sauna_temp}℃` : null,
      s.water_temp != null ? `水風呂${s.water_temp}℃` : null,
    ].filter(Boolean).join(" ");
    return `<div class="recent-item" data-id="${s.id}">
      <div class="recent-title">${esc(s.visited_on)} ${esc(s.facility) || "施設未入力"} ${dots(s.score)}
        ${s.is_sample ? '<span class="tag">サンプル</span>' : ""}</div>
      <div class="recent-meta">${s.sets.length}セット(サウナ/水風呂/休憩) ${esc(sets)}${env ? `<br>${esc(env)}` : ""}</div>
      <button type="button" class="btn ghost del" data-id="${s.id}">削除</button>
    </div>`;
  }).join("");

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
        toast("削除しました");
        await refresh();
      } catch (err) {
        toast(err.message);
      }
    });
  });
}

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
    <div class="card">
      <h2>施設ごとの黄金比</h2>
      ${facilities}
    </div>`;
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
  } catch (err) {
    showError("#predict-error", err.message);
  } finally {
    btn.disabled = false;
  }
});

function renderPrediction(r) {
  const box = $("#predict-result");
  destroyChart("predict");

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

/* ---------- 起動 ---------- */

async function boot() {
  showAuthScreen();
  try {
    const response = await fetch("/api/auth/me", { credentials: "same-origin" });
    if (!response.ok) return;
    await startAuthenticatedApp(await response.json());
  } catch (_) {
    // オフライン時もログイン画面を表示し、利用者が原因を確認できるようにする。
  }
}

$("#f-date").value = todayStr();
resetForm();
updateTimerUi();
boot();

// 端末のテーマが変わったらグラフの色を取り直す
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => renderActive());
