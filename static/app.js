/* Video NAS Tools 前端逻辑 */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  currentDir: "",
  parentDir: null,
  files: [],           // {name, path, size, mtime}
  dirs: [],
  selectedFile: null,  // 当前播放的文件对象
  checked: new Set(),  // 勾选的文件 path
  rotateDegree: 90,
  view: "list",        // list | grid
  sort: "name-asc",
  thumbVersion: 0,     // 任务完成后递增，用于缩略图破缓存
  showPreview: true,   // 由左侧工具栏按钮统一控制视频预览显隐
  concatOrder: [],     // 拼接顺序（勾选文件的 path 列表，可手动调整）
};
const boxSel = { started: false, active: false, x0: 0, y0: 0, adding: false, base: new Set(), suppressClick: false };
let selBoxEl = null;  // 框选矩形（renderFiles 重建 DOM 后需重新挂载）

/* ---------- 工具 ---------- */

function fmtSize(bytes) {
  if (bytes >= 1 << 30) return (bytes / (1 << 30)).toFixed(2) + " GB";
  if (bytes >= 1 << 20) return (bytes / (1 << 20)).toFixed(1) + " MB";
  if (bytes >= 1 << 10) return (bytes / 1 << 10).toFixed(0) + " KB";
  return bytes + " B";
}

function fmtTime(sec) {
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

function parseTime(str) {
  if (!str || !str.trim()) return null;
  const parts = str.trim().split(":").map(Number);
  if (parts.some(isNaN)) return null;
  if (parts.length === 1) return parts[0];
  return parts.reverse().reduce((acc, v, i) => acc + v * 60 ** i, 0);
}

function toast(msg, isErr = false) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (el.className = "toast"), 2600);
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch { /* ignore */ }
    throw new Error(msg);
  }
  return res.json();
}

const getMode = (name) =>
  document.querySelector(`input[name=${name}]:checked`).value === "lossless";

function esc(s) {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------- 设置与目录选择 ---------- */

async function loadSettings() {
  try {
    const { root } = await api("/api/settings");
    $("rootInput").value = root;
  } catch (e) { toast("读取设置失败: " + e.message, true); }
}

async function saveRoot() {
  try {
    const { root } = await api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ root: $("rootInput").value.trim() }),
    });
    $("rootInput").value = root;
    state.currentDir = "";
    state.checked.clear();
    state.selectedFile = null;
    stopPlayer();
    await browse("");
    updateWorkspace();
    toast("根目录已保存: " + root);
  } catch (e) { toast(e.message, true); }
}

async function pickRoot() {
  const btn = $("pickBtn");
  btn.disabled = true;
  btn.textContent = "⏳ 选择中...";
  try {
    const { path } = await api("/api/pick-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initial: $("rootInput").value.trim() }),
    });
    if (!path) { toast("已取消选择"); return; }
    $("rootInput").value = path;
    await saveRoot();
  } catch (e) {
    toast("打开目录选择失败: " + e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "📂 浏览";
  }
}

/* ---------- 文件浏览 ---------- */

async function browse(path) {
  try {
    const data = await api(`/api/files?path=${encodeURIComponent(path)}`);
    state.currentDir = data.path;
    localStorage.setItem("lastDir", data.path);  // 刷新后恢复上次浏览目录
    state.parentDir = data.parent;
    state.dirs = data.dirs;
    state.files = data.files;
    // 剔除已不存在的勾选项
    const names = new Set(data.files.map((f) => f.path));
    [...state.checked].forEach((p) => { if (!names.has(p)) state.checked.delete(p); });
    if (state.selectedFile && !names.has(state.selectedFile.path)) {
      state.selectedFile = null;
      stopPlayer();
    }
    renderDirs();
    renderFiles();
    updateBatchCount();
  } catch (e) { toast(e.message, true); }
}

function renderDirs() {
  $("dirList").innerHTML = state.dirs
    .map((d) => `<div class="dir-item" data-p="${d.path}">📁 <span class="name">${esc(d.name)}</span></div>`)
    .join("");
  $("dirList").querySelectorAll(".dir-item").forEach((el) =>
    el.addEventListener("click", () => browse(el.dataset.p)));
}

function sortedFiles() {
  const list = [...state.files];
  const [key, dir] = state.sort.split("-");
  const mult = dir === "desc" ? -1 : 1;
  return list.sort((a, b) => {
    if (key === "name") return a.name.localeCompare(b.name, "zh-CN") * mult;
    if (key === "time") return (a.mtime - b.mtime) * mult;
    if (key === "size") return (a.size - b.size) * mult;
    return 0;
  });
}

function renderFiles() {
  const files = sortedFiles();
  const listEl = $("fileList");
  listEl.className = state.view === "grid" ? "file-grid" : "file-list";
  listEl.innerHTML = files.map((f) => {
    const active = state.selectedFile?.path === f.path ? " active" : "";
    const checked = state.checked.has(f.path) ? "checked" : "";
    if (state.view === "grid") {
      return `<div class="grid-item${active}" data-p="${f.path}">
        <input type="checkbox" ${checked} data-cb="${f.path}">
        <img src="/api/thumbnail?path=${encodeURIComponent(f.path)}&v=${state.thumbVersion}" alt="" loading="lazy" draggable="false">
        <div class="grid-name" title="${esc(f.path)}">${esc(f.name)}</div>
        <div class="grid-size">${fmtSize(f.size)}</div>
      </div>`;
    }
    return `<div class="file-item${active}" data-p="${f.path}">
      <input type="checkbox" ${checked} data-cb="${f.path}">
      <span>🎬</span><span class="name" title="${esc(f.path)}">${esc(f.name)}</span>
      <span class="size">${fmtSize(f.size)}</span>
    </div>`;
  }).join("");

  if (selBoxEl) listEl.appendChild(selBoxEl);  // 重建 innerHTML 后重新挂载框选矩形
  listEl.querySelectorAll(state.view === "grid" ? ".grid-item" : ".file-item").forEach((el) => {
    el.addEventListener("click", (e) => {
      if (boxSel.suppressClick) return;  // 框选结束后抑制误触的单击预览
      if (e.target.dataset.cb === undefined && e.target.type !== "checkbox")
        selectFile(el.dataset.p);
    });
    const cb = el.querySelector('input[type=checkbox]');
    cb.addEventListener("click", (e) => e.stopPropagation());
    cb.addEventListener("change", (e) => {
      if (e.target.checked) state.checked.add(e.target.dataset.cb);
      else state.checked.delete(e.target.dataset.cb);
      updateBatchCount();
    });
  });
}

/* ---------- 框选（列表 / 缩略图通用） ---------- */

function initBoxSelection() {
  const container = $("fileList");
  selBoxEl = document.createElement("div");
  selBoxEl.className = "select-box";
  selBoxEl.hidden = true;
  container.appendChild(selBoxEl);

  // mousedown：在列表空白区按下即开始（点在文件项/复选框上不启动，避免干扰单击预览与勾选）
  container.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    if (e.target.closest(".file-item, .grid-item, input")) return;
    const r = container.getBoundingClientRect();
    boxSel.started = true;
    boxSel.active = false;
    boxSel.x0 = e.clientX - r.left + container.scrollLeft;
    boxSel.y0 = e.clientY - r.top + container.scrollTop;
    boxSel.adding = e.shiftKey || e.ctrlKey || e.metaKey;
    boxSel.base = new Set(state.checked);
    e.preventDefault();  // 阻止文本选择
  });

  document.addEventListener("mousemove", (e) => {
    if (!boxSel.started) return;
    const r = container.getBoundingClientRect();
    const x = e.clientX - r.left + container.scrollLeft;
    const y = e.clientY - r.top + container.scrollTop;
    if (!boxSel.active) {
      if (Math.hypot(x - boxSel.x0, y - boxSel.y0) < 5) return;  // 阈值：避免单击误触
      boxSel.active = true;
      selBoxEl.hidden = false;
      document.body.classList.add("selecting-files");
    }
    const left = Math.min(x, boxSel.x0), top = Math.min(y, boxSel.y0);
    const w = Math.abs(x - boxSel.x0), h = Math.abs(y - boxSel.y0);
    Object.assign(selBoxEl.style, { left: left + "px", top: top + "px", width: w + "px", height: h + "px" });
    // 命中检测：容器坐标 vs 各项相对容器的位置
    container.querySelectorAll(".file-item, .grid-item").forEach((el) => {
      if (el === selBoxEl) return;
      const er = el.getBoundingClientRect();
      const elx = er.left - r.left + container.scrollLeft;
      const ely = er.top - r.top + container.scrollTop;
      const hit = elx < left + w && elx + er.width > left && ely < top + h && ely + er.height > top;
      el.classList.toggle("box-hit", hit);
    });
  });

  document.addEventListener("mouseup", () => {
    if (!boxSel.started) return;
    boxSel.started = false;
    if (!boxSel.active) return;  // 未激活：保留普通单击行为
    boxSel.active = false;
    selBoxEl.hidden = true;
    document.body.classList.remove("selecting-files");
    const hit = [...container.querySelectorAll(".box-hit")].map((el) => el.dataset.p);
    container.querySelectorAll(".box-hit").forEach((el) => el.classList.remove("box-hit"));
    // 应用勾选：Shift/Ctrl 追加，否则替换
    if (!boxSel.adding) state.checked.clear();
    hit.forEach((p) => state.checked.add(p));
    // 同步复选框
    container.querySelectorAll(".file-item, .grid-item").forEach((el) => {
      const cb = el.querySelector('input[type=checkbox]');
      if (cb) cb.checked = state.checked.has(el.dataset.p);
    });
    updateBatchCount();
    // 抑制框选松手时浏览器补发的 click（否则会误触发预览）
    boxSel.suppressClick = true;
    setTimeout(() => { boxSel.suppressClick = false; }, 0);
  });
}

function updateBatchCount() {
  updateFileToolbar();
  updateWorkspace();
  updateRenamePreview();
  syncConcatOrder();
}

/* ---------- 拼接顺序：与勾选集合同步，保留已排好的相对顺序 ---------- */

function syncConcatOrder() {
  const valid = new Set([...state.checked]);
  state.concatOrder = state.concatOrder.filter((p) => valid.has(p));  // 移除取消勾选的
  for (const f of state.files) {                                      // 追加新勾选的
    if (state.checked.has(f.path) && !state.concatOrder.includes(f.path))
      state.concatOrder.push(f.path);
  }
  renderConcatList();
}

function renderConcatList() {
  const el = $("concatList");
  if (!el) return;
  const paths = state.concatOrder;
  if (!paths.length) { el.innerHTML = '<div class="concat-empty">请先勾选文件</div>'; return; }
  el.innerHTML = paths.map((p, i) => `
    <div class="concat-row-item" draggable="true" data-i="${i}">
      <span class="chandle" title="拖拽调整顺序">⠿</span>
      <span class="cidx">${i + 1}</span>
      <span class="cname" title="${esc(p)}">${esc(p.split("/").pop())}</span>
    </div>`).join("");
  // 拖拽排序（整行为拖拽区）
  let dragIdx = null;
  el.querySelectorAll(".concat-row-item").forEach((row) => {
    row.addEventListener("dragstart", (e) => {
      dragIdx = Number(row.dataset.i);
      row.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", String(dragIdx));
    });
    row.addEventListener("dragend", () => {
      row.classList.remove("dragging", "drag-over");
      dragIdx = null;
    });
    row.addEventListener("dragover", (e) => {
      e.preventDefault(); e.dataTransfer.dropEffect = "move"; row.classList.add("drag-over");
    });
    row.addEventListener("dragleave", (e) => {
      if (!row.contains(e.relatedTarget)) row.classList.remove("drag-over");
    });
    row.addEventListener("drop", (e) => {
      e.preventDefault();
      row.classList.remove("drag-over");
      const from = dragIdx ?? Number(e.dataTransfer.getData("text/plain"));
      const to = Number(row.dataset.i);
      if (Number.isInteger(from) && from !== to) {
        const [item] = state.concatOrder.splice(from, 1);
        state.concatOrder.splice(to, 0, item);
        renderConcatList();
      }
      dragIdx = null;
    });
  });
}

/* ---------- 右侧工作区显隐 ---------- */

function updateWorkspace() {
  const hasPreview = !!state.selectedFile;
  const hasChecked = state.checked.size > 0;
  $("previewArea").hidden = !hasPreview || !state.showPreview;
  $("selectedPanel").hidden = !hasChecked;
  $("emptyState").hidden = hasPreview || hasChecked;
  // 勾选了文件但尚未预览时，提示可点击文件名预览
  $("previewHint").hidden = hasPreview || !hasChecked;
}

function stopPlayer() {
  const v = $("player");
  try { v.pause(); v.removeAttribute("src"); v.load(); } catch { /* ignore */ }
}

/* ---------- 全选 / 取消全选 ---------- */

function updateFileToolbar() {
  const total = state.files.length;
  const n = state.files.filter((f) => state.checked.has(f.path)).length;
  $("fileCount").textContent = `文件 ${total}`;
  const btn = $("selectAllBtn");
  btn.textContent = total && n === total ? "☒ 取消全选" : "☑ 全选";
  btn.disabled = !total;
}

function selectAllToggle() {
  const all = state.files.every((f) => state.checked.has(f.path));
  if (all) state.files.forEach((f) => state.checked.delete(f.path));
  else state.files.forEach((f) => state.checked.add(f.path));
  renderFiles();
  updateBatchCount();
}

/* ---------- 预览与元信息 ---------- */

async function selectFile(path, force = false) {
  const f = state.files.find((x) => x.path === path);
  if (!f) return;
  state.selectedFile = f;
  state.checked.add(path);  // 点击列表/缩略图/任务输出即勾选当前文件
  $("previewTitle").textContent = "🎬 " + f.name;
  renderFiles();
  updateBatchCount();  // 同步工具栏、功能区与重命名预览的勾选状态
  // force 时加时间戳破除浏览器视频缓存，确保看到替换/处理后的最新内容
  $("player").src = `/api/stream?path=${encodeURIComponent(path)}${force ? `&t=${Date.now()}` : ""}`;

  let info = {};
  try { info = await api(`/api/probe?path=${encodeURIComponent(path)}`); }
  catch (e) { toast("读取元信息失败: " + e.message, true); }
  const bits = [
    info.width && info.height ? `${info.width}×${info.height}` : null,
    info.duration ? `时长 ${fmtTime(info.duration)}` : null,
    info.codec ? info.codec : null,
    info.rotation ? `旋转 ${(360 - info.rotation) % 360}°` : null,
    info.bitrate ? `${(info.bitrate / 1e6).toFixed(1)} Mbps` : null,
    fmtSize(info.size || f.size),
  ].filter(Boolean);
  $("metaInfo").innerHTML = bits.map((b) => `<span>${b}</span>`).join("");
}

/* ---------- 截取 / 旋转（统一按勾选文件批量执行） ---------- */

function markCurrentTime(inputId) {
  if (!state.selectedFile) return toast("请先点击文件开始预览", true);
  $(inputId).value = fmtTime($("player").currentTime);
}

function playRange() {
  const s = parseTime($("batchStart").value), e = parseTime($("batchEnd").value);
  if (s === null || e === null || e <= s) return toast("请先设置有效的起点/终点", true);
  const v = $("player");
  v.currentTime = s;
  v.play();
  const stopAt = () => { if (v.currentTime >= e) { v.pause(); v.removeEventListener("timeupdate", stopAt); } };
  v.addEventListener("timeupdate", stopAt);
}

function requireChecked() {
  const paths = [...state.checked];
  if (!paths.length) { toast("请先在左侧勾选文件", true); return null; }
  return paths;
}

async function doBatch(action) {
  const paths = requireChecked();
  if (!paths) return;
  const body = { action, paths, lossless: getMode(action === "cut" ? "batchCutMode" : "batchRotMode") };
  if (action === "cut") {
    const start = parseTime($("batchStart").value), end = parseTime($("batchEnd").value);
    if (start === null || end === null || end <= start)
      return toast("请输入有效的起点/终点（终点需大于起点）", true);
    Object.assign(body, { start, end });
  } else {
    body.degree = state.rotateDegree;
    // 旋转会原地替换文件：若当前预览的文件在批次中，先停播释放文件句柄
    if (state.selectedFile && paths.includes(state.selectedFile.path)) {
      stopPlayer();
      state.selectedFile = null;
      updateWorkspace();
    }
  }
  try {
    const r = await api("/api/batch", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    toast(`已提交 ${r.count} 个${action === "cut" ? "截取" : "旋转"}任务`);
    pollTasks();
  } catch (e) { toast(e.message, true); }
}

/* ---------- 批量删除 ---------- */

async function doDelete() {
  const paths = requireChecked();
  if (!paths) return;
  const note = paths.length > 1
    ? `确定删除已选择的 ${paths.length} 个文件吗？`
    : `确定删除「${paths[0].split("/").pop()}」吗？`;
  if (!confirm(note + "\n\n本地磁盘文件将移入回收站（可恢复）；网络位置可能被直接删除。")) return;
  try {
    const r = await api("/api/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths }),
    });
    if (r.failed) toast(`已删除 ${r.deleted} 个，${r.failed} 个失败：${r.errors[0] || ""}`, true);
    else toast(`已删除 ${r.deleted} 个文件（已移入回收站）`);
    await browse(state.currentDir);
  } catch (e) { toast("删除失败: " + e.message, true); }
}

/* ---------- 批量重命名 ---------- */

function renamePaths() {
  // 按当前文件列表顺序取勾选项，保证「第一个文件」与用户所见一致
  return state.files.filter((f) => state.checked.has(f.path)).map((f) => f.path);
}

function renameBaseName() {
  const mode = document.querySelector('input[name="renameBaseMode"]:checked')?.value || "first";
  if (mode === "custom") return $("renameBase").value.trim();
  const first = renamePaths()[0];
  if (!first) return "";
  const name = first.split("/").pop();
  return name.replace(/\.[^.]+$/, "");
}

function updateRenamePreview() {
  const el = $("renamePreview");
  if (!el) return;
  const paths = renamePaths();
  if (!paths.length) { el.innerHTML = '<span class="rp-more">请先勾选文件</span>'; return; }
  // 输入框始终显示「第一个文件名」作为默认基础名；切到自定义模式时直接带入、可编辑
  const mode = document.querySelector('input[name="renameBaseMode"]:checked')?.value || "first";
  if (mode === "first") $("renameBase").value = renameBaseName();
  const base = renameBaseName();
  if (!base) { el.innerHTML = '<span class="rp-more">请输入基础文件名</span>'; return; }
  const start = Math.max(0, parseInt($("renameStart").value, 10) || 0);
  const digits = parseInt($("renameDigits").value, 10) || 0;
  const rows = paths.slice(0, 6).map((p, i) => {
    const name = p.split("/").pop();
    const dot = name.lastIndexOf(".");
    const ext = dot > 0 ? name.slice(dot) : "";
    const num = String(start + i).padStart(digits, "0");
    return `<div class="rp-row"><span class="rp-new">${esc(base)}_${num}${esc(ext)}</span><span class="rp-old">← ${esc(name)}</span></div>`;
  });
  if (paths.length > 6) rows.push(`<div class="rp-more">… 共 ${paths.length} 个文件</div>`);
  el.innerHTML = rows.join("");
}

async function doRename() {
  const paths = renamePaths();
  if (!paths.length) return toast("请先在左侧勾选文件", true);
  const mode = document.querySelector('input[name="renameBaseMode"]:checked')?.value || "first";
  const custom = $("renameBase").value.trim();
  if (mode === "custom" && !custom) return toast("请输入基础文件名", true);
  const start = parseInt($("renameStart").value, 10);
  if (!Number.isInteger(start) || start < 0) return toast("起始序号需为非负整数", true);
  const digits = parseInt($("renameDigits").value, 10) || 0;
  if (![0, 2, 3].includes(digits)) return toast("补零位数无效", true);
  const label = mode === "custom" ? `「${custom}_序号」` : "第一个文件名 + 序号";
  if (!confirm(`确定将选中的 ${paths.length} 个文件按 ${label} 重命名吗？原扩展名保持不变。`)) return;

  try {
    const r = await api("/api/rename", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths, base: mode === "custom" ? custom : "", start, digits }),
    });
    // 同步勾选集合与当前预览的文件路径，避免重命名后出现「文件不存在」
    const map = new Map(r.items.map((x) => [x.from, x.to]));
    state.checked = new Set([...state.checked].map((p) => map.get(p) || p));
    const cur = state.selectedFile?.path;
    if (cur && map.has(cur)) { state.selectedFile = null; stopPlayer(); }
    await browse(state.currentDir);
    if (cur && map.has(cur) && state.files.some((f) => f.path === map.get(cur)))
      await selectFile(map.get(cur), true);
    state.thumbVersion++;
    updateRenamePreview();
    if (r.failed) toast(`已重命名 ${r.renamed} 个，${r.failed} 个失败：${r.errors[0] || ""}`, true);
    else toast(`已重命名 ${r.renamed} 个文件${r.skipped ? `（${r.skipped} 个无需改动）` : ""}`);
  } catch (e) { toast("重命名失败: " + e.message, true); }
}

/* ---------- 视频拼接 ---------- */

async function doConcat() {
  const paths = state.concatOrder.slice();
  if (paths.length < 2) return toast("拼接至少需要勾选 2 个视频", true);
  const mode = document.querySelector('input[name="concatMode"]:checked')?.value || "copy";
  try {
    const r = await api("/api/concat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths, mode }),  // name 留空：后端用第一个文件名 + _拼接
    });
    toast(`已提交拼接任务：${r.output.split("/").pop()}`);
    pollTasks();
  } catch (e) { toast("拼接失败: " + e.message, true); }
}

/* ---------- 任务队列 ---------- */

const STATUS_TEXT = { queued: "排队中", running: "处理中", done: "完成", failed: "失败" };
let pollTimer = null;
let seenDoneIds = null;   // 已知完成的任务 ID，用于检测「新完成」以回刷预览

async function pollTasks() {
  try {
    const { tasks } = await api("/api/tasks");
    renderTasks(tasks);
    const active = tasks.some((t) => t.status === "queued" || t.status === "running");
    if (active) {
      if (!pollTimer) pollTimer = setInterval(pollTasks, 1500);
    } else if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }

    // 检测新完成的任务：刷新文件列表，并回刷正在预览的相关文件
    const doneIds = new Set(tasks.filter((t) => t.status === "done").map((t) => t.id));
    if (seenDoneIds === null) {
      seenDoneIds = doneIds;  // 首次轮询只记录，避免页面加载时误回刷
    } else {
      const fresh = tasks.filter((t) => t.status === "done" && !seenDoneIds.has(t.id));
      seenDoneIds = doneIds;
      if (fresh.length) {
        const cur = state.selectedFile?.path;
        // 旋转会原地替换，截取会新增文件——两者均需重播当前文件以看到最新内容
        const related = cur && fresh.some((t) =>
          t.source === cur || t.output === cur || (t.sources || []).includes(cur));
        // 主动清掉相关文件的旧缩略图：旋转保留 mtime，后端缓存 key 不会自己变化
        const touched = [...new Set(fresh.flatMap((t) =>
          [t.source, t.output, ...(t.sources || [])]).filter(Boolean))];
        if (touched.length) {
          try {
            await api("/api/thumbnail/invalidate", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ paths: touched }),
            });
          } catch { /* 失效失败不阻断列表刷新 */ }
        }
        state.thumbVersion++;  // 递增版本号，刷新缩略图（破浏览器缓存）
        await browse(state.currentDir);
        if (related && state.files.some((f) => f.path === cur))
          await selectFile(cur, true);  // force 破缓存
        else if (cur && !state.files.some((f) => f.path === cur))
          toast("预览文件已不存在，已刷新列表", true);
      }
    }
    const done = tasks.filter((t) => t.status === "done").length;
    $("taskSummary").textContent = tasks.length ? `${done}/${tasks.length} 完成` : "";
    const running = tasks.filter((t) => t.status === "queued" || t.status === "running").length;
    $("fabBadge").hidden = running === 0;
    $("fabBadge").textContent = running;
  } catch { /* 后端不可达时静默 */ }
}

function renderTasks(tasks) {
  $("taskList").innerHTML = tasks.map((t) => `
    <div class="task-item">
      <div class="task-title">
        <span class="t" title="${esc(t.title)}">${esc(t.title)}</span>
        <span class="task-status ${t.status}">${STATUS_TEXT[t.status]}${t.status === "running" ? ` ${Math.round(t.progress * 100)}%` : ""}</span>
      </div>
      <div class="progress-bar"><div class="progress-fill ${t.status}" style="width:${Math.round(t.progress * 100)}%"></div></div>
      ${t.output && t.status === "done" ? `<div class="task-output" data-p="${esc(t.output)}" title="点击预览">↳ ${esc(t.output)}</div>` : ""}
      ${t.error ? `<div class="task-error">${esc(t.error)}</div>` : ""}
    </div>`).join("");
  $("taskList").querySelectorAll(".task-output").forEach((el) =>
    el.addEventListener("click", () => { selectFile(el.dataset.p); }));
}

/* ---------- 事件绑定 ---------- */

$("rootSaveBtn").addEventListener("click", saveRoot);
$("rootInput").addEventListener("keydown", (e) => e.key === "Enter" && saveRoot());
$("pickBtn").addEventListener("click", pickRoot);
$("playRangeBtn").addEventListener("click", playRange);
function updatePreviewToggleBtn() {
  $("previewToggleBtn").textContent = state.showPreview ? "🎬 隐藏预览" : "🎬 显示预览";
}
$("previewToggleBtn").addEventListener("click", () => {
  state.showPreview = !state.showPreview;
  if (!state.showPreview) { try { $("player").pause(); } catch { /* ignore */ } }
  localStorage.setItem("showPreview", state.showPreview ? "1" : "0");
  updatePreviewToggleBtn();
  updateWorkspace();
});
$("batchCutBtn").addEventListener("click", () => doBatch("cut"));
$("batchRotateBtn").addEventListener("click", () => doBatch("rotate"));
$("batchDeleteBtn").addEventListener("click", doDelete);
$("batchRenameBtn").addEventListener("click", doRename);
$("batchConcatBtn").addEventListener("click", doConcat);
$("concatClearOrderBtn").addEventListener("click", () => {
  state.concatOrder = [];
  syncConcatOrder();  // 按当前文件列表顺序重建
});
document.querySelectorAll('input[name="renameBaseMode"]').forEach((el) => {
  el.addEventListener("change", () => {
    $("renameBase").disabled = el.value !== "custom" || !el.checked;
    if (el.checked && el.value === "custom") $("renameBase").focus();
    updateRenamePreview();
  });
});
["renameBase", "renameStart", "renameDigits"].forEach((id) =>
  $(id).addEventListener("input", updateRenamePreview));
$("selectAllBtn").addEventListener("click", selectAllToggle);
$("sortSelect").addEventListener("change", (e) => { state.sort = e.target.value; renderFiles(); });
$("viewListBtn").addEventListener("click", () => switchView("list"));
$("viewGridBtn").addEventListener("click", () => switchView("grid"));

function switchView(view) {
  state.view = view;
  $("viewListBtn").classList.toggle("active", view === "list");
  $("viewGridBtn").classList.toggle("active", view === "grid");
  renderFiles();
}
function clearSelection() {
  state.checked.clear();
  renderFiles();
  updateBatchCount();
}
$("clearCheckBtn").addEventListener("click", clearSelection);

/* 已选择面板：功能标签切换 + 各功能独立折叠 */
$("opTabs").querySelectorAll(".op-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const op = tab.dataset.op;
    $("opTabs").querySelectorAll(".op-tab").forEach((b) => b.classList.toggle("active", b === tab));
    document.querySelectorAll("[data-op-card]").forEach((card) => {
      card.hidden = card.dataset.opCard !== op;
    });
    if (op === "concat") syncConcatOrder();  // 切到拼接时按勾选实时重建顺序列表
  });
});
$("taskFab").addEventListener("click", () => {
  $("taskFab").hidden = true;
  $("taskPanel").hidden = false;
});
$("collapseTasksBtn").addEventListener("click", () => {
  $("taskPanel").hidden = true;
  $("taskFab").hidden = false;
});
$("clearTasksBtn").addEventListener("click", async () => {
  try {
    const r = await api("/api/tasks", { method: "DELETE" });
    renderTasks([]);
    $("taskSummary").textContent = "";
    $("fabBadge").hidden = true;
    toast(`已清除 ${r.cleared} 条任务记录`);
  } catch (err) { toast(err.message, true); }
});

document.querySelectorAll("[data-mark]").forEach((btn) =>
  btn.addEventListener("click", () => markCurrentTime(btn.dataset.mark)));

document.querySelectorAll(".degree-group button").forEach((btn) => {
  btn.addEventListener("click", () => {
    btn.parentElement.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.rotateDegree = Number(btn.dataset.bdeg);
  });
});

/* ---------- 左右分栏拖拽 ---------- */

function initSplitter() {
  const bar = $("splitter"), sidebar = $("sidebar");
  const saved = Number(localStorage.getItem("sidebarWidthPct"));
  if (saved >= 20 && saved <= 80) sidebar.style.width = `${saved}%`;

  bar.addEventListener("mousedown", (e) => {
    e.preventDefault();
    bar.classList.add("dragging");
    document.body.classList.add("resizing");
    const move = (ev) => {
      const rect = document.querySelector("main").getBoundingClientRect();
      let pct = (ev.clientX - rect.left) / rect.width * 100;
      pct = Math.max(20, Math.min(80, pct));  // 两侧至少各留 20%
      sidebar.style.width = `${pct}%`;
      localStorage.setItem("sidebarWidthPct", String(pct));
    };
    const up = () => {
      bar.classList.remove("dragging");
      document.body.classList.remove("resizing");
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  });
}

/* ---------- 启动 ---------- */

const savedShowPreview = localStorage.getItem("showPreview");
if (savedShowPreview !== null) state.showPreview = savedShowPreview === "1";
else if (localStorage.getItem("previewCollapsed") === "1") state.showPreview = false;
updatePreviewToggleBtn();

loadSettings().then(async () => {
  const last = localStorage.getItem("lastDir");
  if (last !== null) {
    await browse(last);
    // 上次目录已失效（换根目录/被删除）时回退到根目录
    if (state.currentDir !== last) await browse("");
  } else {
    await browse("");
  }
});
pollTasks();
setInterval(() => { if (!pollTimer) pollTasks(); }, 10000); // 空闲时低频轮询
initSplitter();
initBoxSelection();
