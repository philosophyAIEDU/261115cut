"use strict";
const $ = (id) => document.getElementById(id);
const api = async (url, opt = {}) => {
  const r = await fetch(url, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `요청 실패 (${r.status})`);
  return j;
};
const jsonOpt = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const fmt = (t) => {
  t = Math.max(0, t || 0);
  const m = Math.floor(t / 60), s = t - m * 60;
  return `${String(m).padStart(2, "0")}:${s.toFixed(1).padStart(4, "0")}`;
};
const uid = () => Math.random().toString(16).slice(2, 10);
const FILLERS = new Set(["음", "어", "으", "흠", "엄", "응", "아", "에", "음음", "어어", "으음"]);

const S = {
  pid: null, project: null, clips: [], flat: [], overlays: [], style: null,
  sel: null, anchor: null, dragging: false, moved: false, checked: new Set(),
  selOv: null, focus: "script",
  undo: [], redo: [], saveTimer: null, poll: null, followScroll: true, exporting: false,
};

/* ================= toast / modal ================= */
function toast(msg, ms = 2400) {
  const t = $("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
}
function modal(title, text, pct, actions = []) {
  $("modal").hidden = false; $("modalTitle").textContent = title; $("modalText").textContent = text || "";
  $("modalBar").parentElement.hidden = pct == null;
  if (pct != null) $("modalBar").style.width = `${Math.round(pct * 100)}%`;
  const box = $("modalActions"); box.innerHTML = "";
  for (const [label, fn, primary] of actions) {
    const b = document.createElement("button"); b.textContent = label; if (primary) b.className = "primary";
    b.onclick = fn; box.appendChild(b);
  }
}
const closeModal = () => ($("modal").hidden = true);

/* ================= home ================= */
async function initHome() {
  try {
    const sys = await api("/api/system");
    $("modelSel").innerHTML = sys.models.map((m) =>
      `<option value="${m}" ${m === sys.default_model ? "selected" : ""}>${m}${m === "large-v3-turbo" ? " (추천)" : ""}</option>`).join("");
    $("sysInfo").textContent = `${sys.gpu ? "GPU 가속 사용" : "CPU 모드 (GPU 없음)"}${sys.ffmpeg ? "" : " · ⚠ ffmpeg 없음"}`;
  } catch (e) { $("sysInfo").textContent = e.message; }
  loadRecent();
}
async function loadRecent() {
  const list = await api("/api/projects").catch(() => []);
  const ul = $("recentList");
  if (!list.length) { ul.innerHTML = `<li class="empty">아직 프로젝트가 없습니다.</li>`; return; }
  ul.innerHTML = "";
  for (const p of list) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="n"></span><span class="m">${fmt(p.duration)} · ${new Date(p.created * 1000).toLocaleString()}${p.transcribed ? "" : " · 전사 전"}</span><button class="x" title="삭제">✕</button>`;
    li.querySelector(".n").textContent = p.name;
    li.onclick = () => openProject(p.id);
    li.querySelector(".x").onclick = async (ev) => {
      ev.stopPropagation();
      if (!confirm(`'${p.name}' 프로젝트를 삭제할까요?`)) return;
      await api(`/api/projects/${p.id}`, { method: "DELETE" }); loadRecent();
    };
    ul.appendChild(li);
  }
}
function upload(file) {
  $("uploadBox").hidden = false; $("uploadText").textContent = `${file.name} 불러오는 중…`;
  const xhr = new XMLHttpRequest();
  xhr.open("POST", `/api/projects?model=${encodeURIComponent($("modelSel").value)}`);
  xhr.setRequestHeader("X-Filename", encodeURIComponent(file.name));
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) $("uploadBar").style.width = `${(e.loaded / e.total) * 100}%`; };
  xhr.onload = () => {
    $("uploadBox").hidden = true; $("uploadBar").style.width = "0";
    let j = {}; try { j = JSON.parse(xhr.responseText); } catch {}
    if (xhr.status !== 200) return toast(j.error || "파일을 열 수 없습니다.", 5000);
    openProject(j.id);
  };
  xhr.onerror = () => { $("uploadBox").hidden = true; toast("업로드 실패"); };
  xhr.send(file);
}
const drop = $("drop");
$("fileInput").onchange = (e) => e.target.files[0] && upload(e.target.files[0]);
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && upload(e.dataTransfer.files[0]));
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => e.preventDefault());

function showHome() {
  stopPoll(); S.pid = null; $("video").removeAttribute("src"); $("video").load();
  $("editor").hidden = true; $("ribbon").hidden = true; $("tabs").hidden = true; $("home").hidden = false;
  $("editorActions").hidden = true; $("projTitle").textContent = "";
  history.replaceState(null, "", "/"); loadRecent();
}
$("goHome").onclick = showHome;

/* ================= tabs ================= */
$("tabs").addEventListener("click", (e) => {
  const tab = e.target.dataset.tab; if (!tab) return;
  $("tabs").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  document.querySelectorAll(".rpanel").forEach((p) => (p.hidden = p.dataset.panel !== tab));
  $("presetPop").hidden = true;
});

/* ================= project ================= */
async function openProject(pid) {
  const { project, status } = await api(`/api/projects/${pid}`);
  S.pid = pid; S.project = project; S.undo = []; S.redo = []; S.sel = null; S.checked.clear(); S.selOv = null;
  S.style = project.style; S.overlays = project.overlays || [];
  setClips(project.clips || []);
  $("home").hidden = true; $("editor").hidden = false; $("ribbon").hidden = false; $("tabs").hidden = false;
  $("editorActions").hidden = false;
  $("projTitle").textContent = project.name; history.replaceState(null, "", `#${pid}`);
  $("saveState").textContent = "";
  loadVideo(); fitPlayer(); syncStyleControls(); render(); renderOverlays(); updateSpeedInfo(); handleStatus(status); startPoll();
}
function loadVideo() {
  const v = $("video");
  if (S.project.preview) {
    if (!v.src.endsWith(`/api/projects/${S.pid}/media`)) v.src = `/api/projects/${S.pid}/media`;
    $("videoOverlay").hidden = true;
  } else {
    $("videoOverlay").hidden = false; $("videoOverlay").textContent = "미리보기용 영상을 만드는 중입니다…";
  }
}
function fitPlayer() {
  if (!S.project) return;
  const m = S.project.meta, ar = m.width && m.height ? m.width / m.height : 16 / 9;
  const col = $("leftCol"), cw = col.clientWidth - 28, maxH = Math.max(220, window.innerHeight * 0.52);
  const w = Math.min(cw, maxH * ar);
  Object.assign($("player").style, { width: `${w}px`, height: `${w / ar}px`, aspectRatio: "auto" });
  drawTimeline();
}
window.addEventListener("resize", fitPlayer);

function startPoll() {
  stopPoll();
  S.poll = setInterval(async () => {
    if (!S.pid) return;
    try { handleStatus(await api(`/api/projects/${S.pid}/status`)); } catch {}
  }, 800);
}
function stopPoll() { clearInterval(S.poll); S.poll = null; }

let lastTranscribeState = null;
async function handleStatus(st) {
  const tr = st.transcribe || {}, pv = st.preview || {}, ex = st.export || {};
  const box = $("statusBox");
  if (tr.state === "running") {
    box.hidden = false; $("statusBar").style.width = `${Math.round((tr.progress || 0) * 100)}%`;
    $("statusText").textContent = `${tr.message || "전사 중…"} (${Math.round((tr.progress || 0) * 100)}%)`;
  } else if (tr.state === "error") {
    box.hidden = false; $("statusBar").style.width = "0";
    $("statusText").innerHTML = ""; $("statusText").append(`⚠ ${tr.error} `); $("statusText").appendChild(retryButton());
  } else if (!S.project.transcribed && tr.state !== "done") {
    box.hidden = false; $("statusBar").style.width = "0";
    $("statusText").innerHTML = "아직 전사되지 않았습니다. "; $("statusText").appendChild(retryButton("전사 시작"));
  } else box.hidden = true;

  if (tr.state === "done" && lastTranscribeState === "running") {
    const { project } = await api(`/api/projects/${S.pid}`);
    S.project = project; setClips(project.clips); render(); toast(tr.message);
  }
  lastTranscribeState = tr.state;

  if (pv.state === "running") $("videoOverlay").textContent = `미리보기용 영상을 만드는 중… ${Math.round(pv.progress * 100)}%`;
  if (pv.state === "error") $("videoOverlay").textContent = `미리보기 생성 실패: ${pv.error}`;
  if (pv.state === "done" && !S.project.preview) { S.project.preview = "preview.mp4"; loadVideo(); }

  if (ex.state === "running" && S.exporting) {
    modal("영상 내보내는 중", ex.progress >= 0.6 && ex.progress < 0.62 ? "자막과 이미지를 입히는 중…" : "삭제한 구간을 잘라내고 이어 붙이고 있습니다.", ex.progress);
  } else if (ex.state && ex.state !== "running" && S.exporting) {
    S.exporting = false;
    if (ex.state === "done") exportDone(ex.file);
    else modal("내보내기 실패", ex.error, null, [["닫기", closeModal, true]]);
  }
}
function retryButton(label = "다시 시도") {
  const b = document.createElement("button"); b.textContent = label;
  b.onclick = async () => {
    try { await api(`/api/projects/${S.pid}/transcribe`, jsonOpt("POST", { model: S.project.model || "large-v3-turbo" })); }
    catch (e) { toast(e.message); }
  };
  return b;
}

/* ================= model / undo / save ================= */
function setClips(clips) { S.clips = clips; reindex(); }
function reindex() {
  S.flat = [];
  S.clips.forEach((c, ci) => c.tokens.forEach((t) => { t._i = S.flat.length; t._c = ci; S.flat.push(t); }));
}
const strip = (k, v) => (k[0] === "_" ? undefined : v);
const snapshot = () => JSON.stringify({ clips: S.clips, overlays: S.overlays }, strip);
function pushUndo() { S.undo.push(snapshot()); if (S.undo.length > 200) S.undo.shift(); S.redo = []; }
function restore(json) {
  const o = JSON.parse(json);
  setClips(o.clips); S.overlays = o.overlays; S.sel = null; S.checked.clear();
  if (!S.overlays.find((x) => x.id === S.selOv)) S.selOv = null;
  render(); renderOverlays(); scheduleSave();
}
function undo() { if (!S.undo.length) return; S.redo.push(snapshot()); restore(S.undo.pop()); }
function redo() { if (!S.redo.length) return; S.undo.push(snapshot()); restore(S.redo.pop()); }
const payload = () => `{"clips":${JSON.stringify(S.clips, strip)},"overlays":${JSON.stringify(S.overlays)},"style":${JSON.stringify(S.style)}}`;
function scheduleSave() {
  $("saveState").textContent = "저장 중…";
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(saveNow, 500);
}
async function saveNow() {
  clearTimeout(S.saveTimer);
  try {
    await api(`/api/projects/${S.pid}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: payload() });
    $("saveState").textContent = "자동 저장됨";
  } catch { $("saveState").textContent = "저장 실패"; }
}
function commit() { render(); scheduleSave(); updateCaption(); renderOverlays(); }

function selRange() {
  if (!S.sel) return null;
  return [Math.min(S.sel.a, S.sel.b), Math.max(S.sel.a, S.sel.b)];
}
function toggleDelete(a, b) {
  const toks = S.flat.slice(a, b + 1);
  if (!toks.length) return;
  pushUndo();
  const del = !toks.every((t) => t.deleted);
  toks.forEach((t) => (t.deleted = del));
  commit();
}
function toggleDeleteClips(idxs) {
  const toks = idxs.flatMap((ci) => S.clips[ci].tokens);
  if (!toks.length) return;
  pushUndo();
  const del = !toks.every((t) => t.deleted);
  toks.forEach((t) => (t.deleted = del));
  commit();
}
function splitAt(i) {
  const t = S.flat[i], clip = S.clips[t._c], k = clip.tokens.indexOf(t);
  if (k <= 0) return toast("클립의 첫 단어에서는 나눌 수 없습니다.");
  pushUndo();
  const rest = clip.tokens.splice(k);
  rest[0].space = true;
  S.clips.splice(t._c + 1, 0, { id: uid(), tokens: rest });
  S.checked.clear(); reindex(); commit();
}
function mergeClips(idxs) {
  idxs = [...new Set(idxs)].sort((a, b) => a - b);
  if (idxs.length < 2) return toast("합칠 클립을 2개 이상 선택하세요.");
  for (let k = 1; k < idxs.length; k++) if (idxs[k] !== idxs[k - 1] + 1) return toast("붙어 있는 클립만 합칠 수 있습니다.");
  pushUndo();
  const first = S.clips[idxs[0]];
  for (const ci of idxs.slice(1)) first.tokens.push(...S.clips[ci].tokens);
  S.clips.splice(idxs[0] + 1, idxs.length - 1);
  S.checked.clear(); reindex(); commit();
}
const clipSpeed = (c) => Math.min(4, Math.max(0.5, +c.speed || 1));
const keptDuration = () => S.flat.reduce((acc, t) => acc + (t.deleted ? 0 : (t.end - t.start) / clipSpeed(S.clips[t._c])), 0);
const clipRange = (c) => [c.tokens[0].start, c.tokens[c.tokens.length - 1].end];

/* ================= clip list render ================= */
function render() {
  const box = $("script"), scroll = box.scrollTop;
  box.innerHTML = "";
  const frag = document.createDocumentFragment();
  const hasVideo = S.project?.meta?.has_video;
  S.clips.forEach((c, ci) => {
    const [cs, ce] = clipRange(c);
    const row = document.createElement("div");
    row.className = "clip" + (c.tokens.every((t) => t.deleted) ? " alldel" : "") + (S.checked.has(ci) ? " checked" : "");
    row.dataset.c = ci;
    const ck = document.createElement("label"); ck.className = "ck";
    ck.innerHTML = `<input type="checkbox" ${S.checked.has(ci) ? "checked" : ""}>`;
    const no = document.createElement("div"); no.className = "no"; no.textContent = ci + 1;
    const chips = document.createElement("div"); chips.className = "chips";
    if (clipSpeed(c) !== 1) {
      const b = document.createElement("span"); b.className = "chip spd"; b.title = "배속 효과 (효과 탭에서 변경)";
      b.textContent = `⏩ ${clipSpeed(c)}x`; chips.appendChild(b);
    }
    for (const o of S.overlays) {
      if (o.start < ce && o.end > cs && o.start >= cs - 0.001) {
        const b = document.createElement("span"); b.className = "chip img"; b.dataset.ov = o.id; b.textContent = "🖼 이미지";
        chips.appendChild(b);
      }
    }
    for (const t of c.tokens) {
      const sp = document.createElement("span");
      sp.dataset.i = t._i;
      if (t.type === "gap") { sp.className = "chip gap"; sp.textContent = `⏸ ${(t.end - t.start).toFixed(1)}초`; }
      else {
        const low = t.prob != null && t.prob < 0.45;
        sp.className = "chip word" + (low ? " low" : ""); sp.textContent = t.text;
        if (low) sp.title = "AI가 확신하지 못한 단어입니다. 더블클릭으로 수정하세요.";
      }
      if (t.deleted) sp.classList.add("del");
      chips.appendChild(sp);
    }
    const side = document.createElement("div"); side.className = "side";
    side.innerHTML = (hasVideo ? `<img class="thumb" loading="lazy" src="/api/projects/${S.pid}/thumb?t=${(cs + Math.min(0.3, (ce - cs) / 2)).toFixed(1)}" alt="">` : "")
      + `<div class="tm">${fmt(cs)} · ${(ce - cs).toFixed(1)}초</div>`;
    row.append(ck, no, chips, side); frag.appendChild(row);
  });
  box.appendChild(frag); box.scrollTop = scroll;
  $("clipCount").textContent = `클립 ${S.clips.length}개`;
  paintSel(); updateCheckUI(); updateLen(); drawTimeline(); applySearch(); nowIdx = -1; highlightNow();
}
const tokEl = (i) => $("script").querySelector(`.chip[data-i="${i}"]`);
function paintSel() {
  $("script").querySelectorAll(".chip.sel").forEach((e) => e.classList.remove("sel"));
  const r = selRange(); if (!r) return;
  for (let i = r[0]; i <= r[1]; i++) tokEl(i)?.classList.add("sel");
}
function updateLen() {
  $("lenText").textContent = `편집 후 ${fmt(keptDuration())} / 원본 ${fmt(S.project?.meta?.duration || 0)}`;
}
function updateCheckUI() {
  const n = S.checked.size;
  $("ckDelBtn").disabled = !n; $("ckMergeBtn").disabled = n < 2;
  $("checkAll").checked = n && n === S.clips.length;
  $("checkAll").indeterminate = n > 0 && n < S.clips.length;
}

/* ================= clip list interaction ================= */
const script = $("script");
script.addEventListener("mousedown", (e) => {
  if (e.target.closest("input")) return;
  const ovChip = e.target.closest(".chip.img");
  if (ovChip) { selectOverlay(ovChip.dataset.ov, true); return; }
  const el = e.target.closest(".chip[data-i]");
  if (!el) return;
  e.preventDefault();
  S.focus = "script";
  const i = +el.dataset.i;
  if (e.shiftKey && S.anchor != null) S.sel = { a: S.anchor, b: i };
  else { S.anchor = i; S.sel = { a: i, b: i }; }
  S.dragging = true; S.moved = false; paintSel(); drawTimeline();
});
script.addEventListener("mouseover", (e) => {
  if (!S.dragging) return;
  const el = e.target.closest(".chip[data-i]"); if (!el) return;
  const i = +el.dataset.i;
  if (i !== S.sel.b) { S.sel.b = i; S.moved = true; paintSel(); drawTimeline(); }
});
document.addEventListener("mouseup", (e) => {
  if (!S.dragging) return;
  S.dragging = false;
  if (!S.moved && !e.shiftKey && S.sel) seek(S.flat[S.sel.a].start + 0.001);
});
script.addEventListener("dblclick", (e) => {
  const el = e.target.closest(".chip.word"); if (!el) return;
  editWord(+el.dataset.i, el);
});
script.addEventListener("change", (e) => {
  if (!e.target.matches(".ck input")) return;
  const ci = +e.target.closest(".clip").dataset.c;
  e.target.checked ? S.checked.add(ci) : S.checked.delete(ci);
  e.target.closest(".clip").classList.toggle("checked", e.target.checked);
  updateCheckUI();
});
$("checkAll").onchange = (e) => {
  S.checked = new Set(e.target.checked ? S.clips.map((_, i) => i) : []);
  render();
};
$("ckDelBtn").onclick = () => toggleDeleteClips([...S.checked]);
$("ckMergeBtn").onclick = () => mergeClips([...S.checked]);

function editWord(i, el) {
  const t = S.flat[i];
  const input = document.createElement("input");
  input.value = t.text; el.textContent = ""; el.appendChild(input); input.focus(); input.select();
  input.style.width = `${Math.max(3, t.text.length + 2)}em`;
  let done = false;
  const finish = (save) => {
    if (done) return; done = true;
    const v = input.value.trim();
    if (save && v && v !== t.text) { pushUndo(); t.text = v; commit(); } else render();
  };
  input.addEventListener("keydown", (ev) => {
    ev.stopPropagation();
    if (ev.key === "Enter") finish(true);
    if (ev.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
}

document.addEventListener("keydown", (e) => {
  if ($("editor").hidden || e.target.matches("input, select, textarea")) return;
  const r = selRange(), k = e.key;
  if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
  if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === "y") { e.preventDefault(); redo(); return; }
  if (k === " ") { e.preventDefault(); togglePlay(); return; }
  if (k === "Escape") { S.sel = null; selectOverlay(null); paintSel(); drawTimeline(); return; }
  if ((k === "Delete" || k === "Backspace") && S.focus === "ov" && S.selOv) { e.preventDefault(); deleteOverlay(); return; }
  if ((k === "Delete" || k === "Backspace") && !r && S.checked.size) { e.preventDefault(); toggleDeleteClips([...S.checked]); return; }
  if (!r) return;
  if (k === "Delete" || k === "Backspace") { e.preventDefault(); toggleDelete(r[0], r[1]); }
  else if (k === "Enter") { e.preventDefault(); splitAt(r[0]); }
  else if (k === "ArrowRight" || k === "ArrowLeft") {
    e.preventDefault();
    const d = k === "ArrowRight" ? 1 : -1;
    const nb = Math.min(S.flat.length - 1, Math.max(0, S.sel.b + d));
    if (e.shiftKey) S.sel.b = nb; else { S.sel = { a: nb, b: nb }; S.anchor = nb; seek(S.flat[nb].start + 0.001); }
    paintSel(); drawTimeline(); tokEl(nb)?.scrollIntoView({ block: "nearest" });
  }
});

/* ================= edit ribbon ================= */
$("undoBtn").onclick = undo; $("redoBtn").onclick = redo;
$("delSelBtn").onclick = () => {
  const r = selRange();
  if (r) toggleDelete(r[0], r[1]);
  else if (S.checked.size) toggleDeleteClips([...S.checked]);
  else toast("먼저 단어나 클립을 선택하세요.");
};
$("splitBtn").onclick = () => { const r = selRange(); r ? splitAt(r[0]) : toast("나눌 위치의 단어를 클릭하세요."); };
$("mergeBtn").onclick = () => {
  if (S.checked.size >= 2) return mergeClips([...S.checked]);
  const r = selRange();
  if (r) { const a = S.flat[r[0]]._c, b = S.flat[r[1]]._c; if (a !== b) return mergeClips(range(a, b)); if (a > 0) return mergeClips([a - 1, a]); }
  toast("클립을 체크하거나, 여러 클립에 걸쳐 단어를 선택하세요.");
};
const range = (a, b) => Array.from({ length: b - a + 1 }, (_, i) => a + i);
$("silenceBtn").onclick = () => {
  const min = +$("silenceSel").value;
  const targets = S.flat.filter((t) => t.type === "gap" && !t.deleted && t.end - t.start >= min);
  if (!targets.length) return toast(`${min}초 이상의 무음이 없습니다.`);
  pushUndo(); targets.forEach((t) => (t.deleted = true)); commit();
  toast(`무음 ${targets.length}곳 삭제 (${targets.reduce((a, t) => a + t.end - t.start, 0).toFixed(1)}초)`);
};
$("fillerBtn").onclick = () => {
  const norm = (s) => s.replace(/[.,!?~…\s]/g, "");
  const targets = S.flat.filter((t) => t.type === "word" && !t.deleted && FILLERS.has(norm(t.text)));
  if (!targets.length) return toast("군말을 찾지 못했습니다.");
  pushUndo(); targets.forEach((t) => (t.deleted = true)); commit();
  toast(`군말 ${targets.length}개 삭제`);
};
$("restoreBtn").onclick = () => {
  if (!S.flat.some((t) => t.deleted)) return;
  pushUndo(); S.flat.forEach((t) => (t.deleted = false)); commit();
};
$("searchInput").addEventListener("input", applySearch);
$("searchInput").addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const hits = [...$("script").querySelectorAll(".chip.hit")]; if (!hits.length) return;
  S.searchIdx = ((S.searchIdx ?? -1) + 1) % hits.length;
  const el = hits[S.searchIdx]; el.scrollIntoView({ block: "center" });
  const i = +el.dataset.i; S.sel = { a: i, b: i }; S.anchor = i; paintSel(); seek(S.flat[i].start + 0.001);
});
function applySearch() {
  const q = $("searchInput").value.trim();
  $("script").querySelectorAll(".chip.hit").forEach((e) => e.classList.remove("hit"));
  if (!q) return;
  for (const t of S.flat) if (t.type === "word" && t.text.includes(q)) tokEl(t._i)?.classList.add("hit");
}

/* ================= subtitle style ================= */
function hexA(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}
/* u = CSS length of one "1080p pixel". Mirrors subrender.py so preview == export. */
function applyStyle(el, st, u) {
  const L = (n) => `calc(${n} * ${u})`;
  const boxed = st.bg.on || st.highlight.on;
  const padX = boxed ? st.bg.pad : 0, padY = boxed ? st.bg.pad * 0.4 : 0;
  const bgs = [];
  if (st.highlight.on) bgs.push(`linear-gradient(transparent 55%, ${st.highlight.color} 55%)`);
  // Outline = rings of hard text-shadows (outer stroke, like Pillow's stroke_width);
  // -webkit-text-stroke would eat into the glyphs where paint-order is unsupported.
  const shadows = [];
  if (st.outline.on) {
    const w = st.outline.width;
    for (const r of [w, w * 0.5]) for (let k = 0; k < 16; k++) {
      const a = (k / 16) * Math.PI * 2;
      shadows.push(`${L((Math.cos(a) * r).toFixed(2))} ${L((Math.sin(a) * r).toFixed(2))} 0 ${st.outline.color}`);
    }
  }
  if (st.shadow.on) shadows.push(`${L(st.shadow.x)} ${L(st.shadow.y)} ${L(st.shadow.blur)} ${hexA(st.shadow.color, st.shadow.opacity)}`);
  Object.assign(el.style, {
    fontFamily: "Pretendard", fontWeight: st.weight, fontSize: L(st.size), lineHeight: st.lineHeight,
    color: st.color, fontStyle: st.italic ? "italic" : "normal", letterSpacing: L(st.spacing),
    textShadow: shadows.join(",") || "none",
    backgroundColor: st.bg.on ? hexA(st.bg.color, st.bg.opacity) : "transparent",
    backgroundImage: bgs.join(",") || "none",
    padding: `${L(padY)} ${L(padX)}`, borderRadius: st.bg.on ? L(st.bg.radius) : "0",
  });
}
function renderSubStyle() {
  applyStyle($("subLine"), S.style, "100cqh / 1080");
  $("subBlock").style.bottom = `${(1 - S.style.posY) * 100}%`;
}
function syncStyleControls() {
  const st = S.style;
  $("sWeight").value = st.weight; $("sItalic").classList.toggle("on", st.italic);
  $("sSize").value = st.size; $("sColor").value = st.color;
  $("sOutlineOn").classList.toggle("on", st.outline.on); $("sOutlineColor").value = st.outline.color; $("sOutlineW").value = st.outline.width;
  $("sBgOn").classList.toggle("on", st.bg.on); $("sBgColor").value = st.bg.color; $("sBgOp").value = Math.round(st.bg.opacity * 100);
  $("sHlOn").classList.toggle("on", st.highlight.on); $("sHlColor").value = st.highlight.color;
  $("sShadowOn").classList.toggle("on", st.shadow.on); $("sShadowColor").value = st.shadow.color;
  $("sSpacing").value = st.spacing; $("sMaxChars").value = st.maxChars; $("sPosY").value = st.posY;
  renderSubStyle(); updateCaption();
}
function styleChanged() { syncStyleControls(); scheduleSave(); }
const num = (id, lo, hi) => Math.min(hi, Math.max(lo, +$(id).value || lo));
const bindStyle = (id, ev, fn) => $(id).addEventListener(ev, () => { fn(S.style); styleChanged(); });
bindStyle("sWeight", "change", (s) => (s.weight = +$("sWeight").value));
bindStyle("sItalic", "click", (s) => (s.italic = !s.italic));
bindStyle("sSize", "change", (s) => (s.size = num("sSize", 16, 200)));
bindStyle("sColor", "input", (s) => (s.color = $("sColor").value));
bindStyle("sOutlineOn", "click", (s) => (s.outline.on = !s.outline.on));
bindStyle("sOutlineColor", "input", (s) => { s.outline.color = $("sOutlineColor").value; s.outline.on = true; });
bindStyle("sOutlineW", "change", (s) => { s.outline.width = num("sOutlineW", 1, 20); s.outline.on = true; });
bindStyle("sBgOn", "click", (s) => (s.bg.on = !s.bg.on));
bindStyle("sBgColor", "input", (s) => { s.bg.color = $("sBgColor").value; s.bg.on = true; });
bindStyle("sBgOp", "change", (s) => { s.bg.opacity = num("sBgOp", 10, 100) / 100; s.bg.on = true; });
bindStyle("sHlOn", "click", (s) => (s.highlight.on = !s.highlight.on));
bindStyle("sHlColor", "input", (s) => { s.highlight.color = $("sHlColor").value; s.highlight.on = true; });
bindStyle("sShadowOn", "click", (s) => (s.shadow.on = !s.shadow.on));
bindStyle("sShadowColor", "input", (s) => { s.shadow.color = $("sShadowColor").value; s.shadow.on = true; });
bindStyle("sSpacing", "change", (s) => (s.spacing = num("sSpacing", -10, 30)));
bindStyle("sMaxChars", "change", (s) => (s.maxChars = num("sMaxChars", 8, 80)));
bindStyle("sPosY", "input", (s) => (s.posY = +$("sPosY").value));

/* presets */
$("presetBtn").onclick = async (e) => {
  e.stopPropagation();
  const pop = $("presetPop");
  if (!pop.hidden) { pop.hidden = true; return; }
  const items = await api("/api/presets").catch(() => []);
  pop.innerHTML = "";
  for (const p of items) {
    const tile = document.createElement("div"); tile.className = "preset"; tile.title = p.name;
    const sample = document.createElement("span"); sample.textContent = "텍스트";
    applyStyle(sample, { ...p.style, size: Math.min(p.style.size, 72) }, "100cqh / 160");
    tile.append(sample);
    tile.insertAdjacentHTML("beforeend", `<span class="nm"></span>`); tile.querySelector(".nm").textContent = p.name;
    if (!p.builtin) {
      const x = document.createElement("button"); x.className = "x"; x.textContent = "✕"; x.title = "서식 삭제";
      x.onclick = async (ev) => { ev.stopPropagation(); await api(`/api/presets/${p.id}`, { method: "DELETE" }); pop.hidden = true; $("presetBtn").click(); };
      tile.append(x);
    }
    tile.onclick = () => {
      const keep = { posY: S.style.posY, maxChars: S.style.maxChars };
      S.style = { ...structuredClone(p.style), ...keep }; pop.hidden = true; styleChanged(); toast(`'${p.name}' 서식 적용`);
    };
    pop.appendChild(tile);
  }
  const add = document.createElement("div"); add.className = "preset add"; add.innerHTML = "<b>+</b>현재 서식 저장";
  add.onclick = async () => {
    const name = prompt("서식 이름", "내 서식"); if (!name) return;
    await api("/api/presets", jsonOpt("POST", { name, style: S.style })); toast("서식을 저장했습니다.");
    pop.hidden = true;
  };
  pop.appendChild(add);
  pop.hidden = false;
};
$("presetPop").addEventListener("click", (e) => e.stopPropagation());
document.addEventListener("click", () => { $("presetPop").hidden = true; $("exportMenu").hidden = true; });

/* caption preview: same line splitting as the exported subtitles */
const joinWords = (ws) => ws.reduce((s, t) => s + (s && t.space ? " " : "") + t.text, "").trim();
function captionAt(t) {
  const i = findTok(t); if (i < 0) return "";
  const clip = S.clips[S.flat[i]._c], max = S.style?.maxChars || 32;
  const words = clip.tokens.filter((x) => x.type === "word" && !x.deleted && x.text.trim());
  const lines = []; let chunk = [];
  for (const w of words) {
    if (chunk.length && joinWords([...chunk, w]).length > max) { lines.push(chunk); chunk = []; }
    chunk.push(w);
  }
  if (chunk.length) lines.push(chunk);
  const hit = lines.find((c) => t >= c[0].start && t < c[c.length - 1].end);
  return hit ? joinWords(hit) : "";
}
function updateCaption() {
  const txt = $("capChk").checked ? captionAt(video.currentTime) : "";
  const el = $("subLine");
  if (el.textContent !== txt) el.textContent = txt;
  $("subBlock").hidden = !txt;
}
$("capChk").addEventListener("change", updateCaption);

/* ================= image overlays ================= */
$("imgInput").onchange = async (e) => {
  const file = e.target.files[0]; e.target.value = ""; if (!file) return;
  try {
    const r = await fetch(`/api/projects/${S.pid}/assets`, { method: "POST", headers: { "X-Filename": encodeURIComponent(file.name) }, body: file });
    const j = await r.json(); if (!r.ok) throw new Error(j.error);
    let start, end;
    const sr = selRange();
    if (S.checked.size) {
      const cs = [...S.checked].sort((a, b) => a - b);
      start = clipRange(S.clips[cs[0]])[0]; end = clipRange(S.clips[cs[cs.length - 1]])[1];
    } else if (sr) {
      start = clipRange(S.clips[S.flat[sr[0]]._c])[0]; end = clipRange(S.clips[S.flat[sr[1]]._c])[1];
    } else {
      const i = Math.max(0, findTok(video.currentTime));
      [start, end] = S.flat.length ? clipRange(S.clips[S.flat[i]._c]) : [0, S.project.meta.duration];
    }
    const m = S.project.meta, frameAr = (m.width || 16) / (m.height || 9), imgAr = j.width / j.height;
    const w = Math.min(0.4, 0.5 * imgAr / frameAr);  // keep height <= 50% of frame
    pushUndo();
    const o = { id: uid(), file: j.file, start, end, x: 0.5, y: 0.42, w: +w.toFixed(3), opacity: 1, ar: imgAr };
    S.overlays.push(o);
    commit(); selectOverlay(o.id, true);
    toast(`이미지를 ${fmt(start)}~${fmt(end)} 구간에 넣었습니다.`);
  } catch (err) { toast(err.message || "이미지를 넣지 못했습니다.", 4000); }
};
function renderOverlays() {
  const layer = $("ovLayer"), t = video.currentTime;
  const want = new Set();
  for (const o of S.overlays) {
    const vis = (t >= o.start && t < o.end) || o.id === S.selOv;
    if (!vis) continue;
    want.add(o.id);
    let el = layer.querySelector(`.ov[data-id="${o.id}"]`);
    if (!el) {
      el = document.createElement("div"); el.className = "ov"; el.dataset.id = o.id;
      el.innerHTML = `<img src="/api/projects/${S.pid}/assets/${o.file}" alt=""><span class="handle"></span>`;
      layer.appendChild(el);
    }
    Object.assign(el.style, { left: `${o.x * 100}%`, top: `${o.y * 100}%`, width: `${o.w * 100}%`, opacity: o.opacity });
    el.classList.toggle("sel", o.id === S.selOv);
  }
  layer.querySelectorAll(".ov").forEach((el) => { if (!want.has(el.dataset.id)) el.remove(); });
}
function selectOverlay(id, seekTo = false) {
  S.selOv = id; S.focus = id ? "ov" : "script";
  const o = S.overlays.find((x) => x.id === id);
  $("ovTools").hidden = !o; $("insertHint").hidden = !!o;
  if (o) {
    $("ovW").value = o.w; $("ovOp").value = o.opacity;
    if (seekTo && !(video.currentTime >= o.start && video.currentTime < o.end)) seek(o.start + 0.001);
    document.querySelector('#tabs [data-tab="insert"]').click();
  }
  renderOverlays(); drawTimeline();
}
const curOv = () => S.overlays.find((x) => x.id === S.selOv);
let ovDrag = null;
$("ovLayer").addEventListener("pointerdown", (e) => {
  const el = e.target.closest(".ov"); if (!el) return;
  e.preventDefault(); e.stopPropagation();
  selectOverlay(el.dataset.id);
  const o = curOv(), rect = $("stage").getBoundingClientRect();
  ovDrag = { o, rect, resize: e.target.classList.contains("handle"), sx: e.clientX, sy: e.clientY, ox: o.x, oy: o.y, ow: o.w, before: snapshot(), moved: false };
  el.setPointerCapture(e.pointerId);
});
$("ovLayer").addEventListener("pointermove", (e) => {
  if (!ovDrag) return;
  const { o, rect } = ovDrag, dx = (e.clientX - ovDrag.sx) / rect.width, dy = (e.clientY - ovDrag.sy) / rect.height;
  ovDrag.moved = true;
  if (ovDrag.resize) o.w = Math.min(1, Math.max(0.03, ovDrag.ow + dx * 2));
  else { o.x = Math.min(1, Math.max(0, ovDrag.ox + dx)); o.y = Math.min(1, Math.max(0, ovDrag.oy + dy)); }
  renderOverlays(); $("ovW").value = o.w;
});
$("ovLayer").addEventListener("pointerup", () => {
  if (!ovDrag) return;
  if (ovDrag.moved) { S.undo.push(ovDrag.before); S.redo = []; scheduleSave(); }
  ovDrag = null;
});
$("stage").addEventListener("pointerdown", (e) => { if (!e.target.closest(".ov") && S.selOv) selectOverlay(null); });
const ovEdit = (fn) => { const o = curOv(); if (!o) return; pushUndo(); fn(o); commit(); };
$("ovW").addEventListener("input", () => { const o = curOv(); if (o) { o.w = +$("ovW").value; renderOverlays(); scheduleSave(); } });
$("ovOp").addEventListener("input", () => { const o = curOv(); if (o) { o.opacity = +$("ovOp").value; renderOverlays(); scheduleSave(); } });
$("ovStartBtn").onclick = () => ovEdit((o) => { o.start = Math.min(video.currentTime, o.end - 0.1); });
$("ovEndBtn").onclick = () => ovEdit((o) => { o.end = Math.max(video.currentTime, o.start + 0.1); });
$("ovAllBtn").onclick = () => ovEdit((o) => { o.start = 0; o.end = S.project.meta.duration; });
$("ovDelBtn").onclick = deleteOverlay;
function deleteOverlay() {
  if (!S.selOv) return;
  pushUndo(); S.overlays = S.overlays.filter((x) => x.id !== S.selOv); selectOverlay(null); commit();
}

/* ================= speed effect ================= */
function applyPlaybackRate() {
  const i = findTok(video.currentTime);
  const rate = i >= 0 ? clipSpeed(S.clips[S.flat[i]._c]) : 1;
  if (video.playbackRate !== rate) { video.preservesPitch = true; video.playbackRate = rate; }
}
function speedTargets(scope) {
  if (scope === "all") return S.clips.map((_, i) => i);
  if (scope === "cur") { const i = findTok(video.currentTime); return i >= 0 ? [S.flat[i]._c] : []; }
  if (S.checked.size) return [...S.checked];
  const r = selRange();
  return r ? range(S.flat[r[0]]._c, S.flat[r[1]]._c) : [];
}
function setSpeed(v) {
  v = Math.min(4, Math.max(0.5, Math.round((+v || 1) * 100) / 100));
  $("spdRange").value = v; $("spdNum").value = v;
  return v;
}
function applySpeed(v) {
  const scope = $("spdScope").value, idx = speedTargets(scope);
  if (!idx.length) return toast(scope === "sel" ? "클립을 체크하거나 단어를 선택하세요." : "적용할 클립이 없습니다.");
  pushUndo();
  idx.forEach((ci) => { if (v === 1) delete S.clips[ci].speed; else S.clips[ci].speed = v; });
  commit(); applyPlaybackRate(); updateSpeedInfo();
  toast(`클립 ${idx.length}개에 ${v}배속 적용`);
}
function updateSpeedInfo() {
  const n = S.clips.filter((c) => clipSpeed(c) !== 1).length;
  $("spdInfo").textContent = n ? `배속이 적용된 클립 ${n}개` : "";
}
$("spdRange").addEventListener("input", () => setSpeed($("spdRange").value));
$("spdNum").addEventListener("change", () => setSpeed($("spdNum").value));
$("spdQuick").addEventListener("click", (e) => { if (e.target.dataset.v) setSpeed(e.target.dataset.v); });
$("spdApply").onclick = () => applySpeed(setSpeed($("spdNum").value));
$("spdReset").onclick = () => applySpeed(1);
$("script").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip.spd"); if (!chip) return;
  const ci = +chip.closest(".clip").dataset.c;
  setSpeed(clipSpeed(S.clips[ci])); S.checked = new Set([ci]); render();
  $("spdScope").value = "sel"; document.querySelector('#tabs [data-tab="effect"]').click();
});

/* ================= export ================= */
$("exportBtn").onclick = (e) => { e.stopPropagation(); $("exportMenu").hidden = !$("exportMenu").hidden; };
$("exportMenu").addEventListener("click", async (e) => {
  const kind = e.target.dataset.kind; if (!kind) return;
  $("exportMenu").hidden = true;
  await saveNow();
  try {
    const j = await api(`/api/projects/${S.pid}/export`, jsonOpt("POST", { kind }));
    if (kind.startsWith("mp4")) { S.exporting = true; modal("영상 내보내는 중", "준비 중…", 0); }
    else exportDone(j.file);
  } catch (err) { toast(err.message, 4000); }
});
function exportDone(file) {
  const url = `/api/projects/${S.pid}/exports/${encodeURIComponent(file)}`;
  modal("내보내기 완료", file, null, [
    ["폴더 열기", () => api(`/api/projects/${S.pid}/reveal`, { method: "POST" })],
    ["다운로드", () => { const a = document.createElement("a"); a.href = url; a.download = file; a.click(); }],
    ["닫기", closeModal, true],
  ]);
}

/* ================= playback (skips deleted ranges) ================= */
const video = $("video");
function findTok(t) {
  let lo = 0, hi = S.flat.length - 1, ans = -1;
  while (lo <= hi) { const m = (lo + hi) >> 1; if (S.flat[m].start <= t) { ans = m; lo = m + 1; } else hi = m - 1; }
  return ans;
}
function seek(t) { video.currentTime = Math.max(0, t); S.followScroll = true; drawTimeline(); }
function togglePlay() { if (!video.src) return; S.stopAt = null; video.paused ? video.play() : video.pause(); }
$("playBtn").onclick = togglePlay;
video.addEventListener("play", () => { $("playBtn").textContent = "❚❚"; requestAnimationFrame(tick); });
video.addEventListener("pause", () => ($("playBtn").textContent = "▶"));
video.addEventListener("seeked", () => { highlightNow(); drawTimeline(); });
video.addEventListener("timeupdate", () => { if (video.paused) highlightNow(); });
video.addEventListener("loadedmetadata", drawTimeline);

let nowIdx = -1, nowClip = -1;
function tick() {
  const t = video.currentTime;
  if (S.stopAt != null && t >= S.stopAt) { video.pause(); S.stopAt = null; }
  if ($("skipChk").checked && !video.seeking && S.flat.length) {
    const i = findTok(t + 0.01);
    if (i >= 0 && S.flat[i].deleted) {
      let j = i; while (j < S.flat.length && S.flat[j].deleted) j++;
      if (j >= S.flat.length) video.pause(); else video.currentTime = S.flat[j].start + 0.001;
    }
  }
  applyPlaybackRate();
  highlightNow(); drawTimeline();
  if (!video.paused) requestAnimationFrame(tick);
}
function highlightNow() {
  $("timeText").textContent = fmt(video.currentTime);
  updateCaption(); renderOverlays();
  const i = findTok(video.currentTime);
  if (i === nowIdx) return;
  tokEl(nowIdx)?.classList.remove("now");
  nowIdx = i;
  const el = tokEl(i);
  const ci = i >= 0 ? S.flat[i]._c : -1;
  if (ci !== nowClip) {
    $("script").querySelector(".clip.active")?.classList.remove("active");
    $("script").querySelector(`.clip[data-c="${ci}"]`)?.classList.add("active");
    nowClip = ci;
  }
  if (el) { el.classList.add("now"); if (!video.paused && S.followScroll) el.scrollIntoView({ block: "nearest" }); }
}
$("script").addEventListener("wheel", () => (S.followScroll = false), { passive: true });

/* ================= timeline ================= */
const cv = $("timeline");
function drawTimeline() {
  const dur = S.project?.meta?.duration || video.duration || 0;
  const w = cv.clientWidth, h = cv.clientHeight, dpr = window.devicePixelRatio || 1;
  if (!w) return;
  if (cv.width !== Math.round(w * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
  const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  g.fillStyle = "#1f232b"; g.fillRect(0, 0, w, h);
  if (!dur) return;
  for (const t of S.flat) {
    const x = (t.start / dur) * w, x2 = (t.end / dur) * w;
    if (t.deleted) { g.fillStyle = "rgba(255,93,98,.75)"; g.fillRect(x, 0, Math.max(1, x2 - x), h); }
    else if (t.type === "word") { g.fillStyle = "#4c8dff"; g.fillRect(x, h * 0.32, Math.max(0.5, x2 - x - 0.5), h * 0.36); }
  }
  for (const o of S.overlays) {
    g.fillStyle = o.id === S.selOv ? "#ffd34d" : "rgba(243,214,122,.6)";
    g.fillRect((o.start / dur) * w, 2, Math.max(2, ((o.end - o.start) / dur) * w), 4);
  }
  const r = selRange();
  if (r) {
    const x = (S.flat[r[0]].start / dur) * w, x2 = (S.flat[r[1]].end / dur) * w;
    g.strokeStyle = "#ffffff"; g.lineWidth = 1.5; g.strokeRect(x + 1, 1, Math.max(2, x2 - x - 2), h - 2);
  }
  g.fillStyle = "#ffffff"; g.fillRect((video.currentTime / dur) * w - 1, 0, 2, h);
}
cv.addEventListener("click", (e) => {
  const dur = S.project?.meta?.duration || video.duration || 0;
  const rect = cv.getBoundingClientRect();
  seek(((e.clientX - rect.left) / rect.width) * dur);
});

/* ================= boot ================= */
initHome();
if (location.hash.length > 1) openProject(location.hash.slice(1)).catch(() => showHome());
