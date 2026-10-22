renderNav("calibrate");

const $ = (id) => document.getElementById(id);
const canvas = $("canvas");
const ctx = canvas.getContext("2d");
const HANDLE_PX = 14;
const IN_COLOR = "#3ccf6e";
const OUT_COLOR = "#ff9f2e";
const LINE_COLOR = "#ff00c8";
const MAX_ZONE_POINTS = 32;

const STEPS = {
  line: [
    "Click điểm đầu A của line",
    "Click điểm cuối B của line",
    'Click vào phía "BÊN TRONG" (mũi tên xanh chỉ chiều VÀO)',
    "Bấm Lưu",
  ],
  zone: [
    "Click lần lượt các góc của vùng (ít nhất 3 điểm)",
    "Kéo điểm để chỉnh · click lên cạnh để chèn điểm · chuột phải vào điểm để xóa",
    "Bấm Lưu",
  ],
};
const HINTS = {
  line: "Kéo các điểm A, B, I để chỉnh. Người được tính khi điểm chân (mắt cá) đi qua đoạn A–B. Nên đặt line trên sàn, vuông góc hướng đi, tránh mép khung hình.",
  zone: "Người được tính VÀO khi điểm chân (mắt cá) đi từ ngoài vào trong vùng, RA khi đi ra; sự kiện RA kèm thời gian ở trong vùng. Vẽ vùng trên sàn và chừa khoảng trống với mép khung hình ở phía người đi tới, để thấy được người trước khi họ bước vào.",
};

const st = {
  camId: null,
  img: null,
  mode: "line",
  pts: [], // line A, B (normalized)
  inward: null, // line inward point (normalized)
  zone: [], // polygon vertices (normalized)
  drag: null, // {kind: "a" | "b" | "i" | "z", idx}
  live: false,
  liveTimer: null,
  dirty: false,
  enabled: true,
};

// ---------- geometry (pixel space of the canvas) ----------
const W = () => canvas.width;
const H = () => canvas.height;
const S = () => Math.max(1, W() / 1280);
const toPx = (p) => ({ x: p.x * W(), y: p.y * H() });
const toNorm = (p) => ({ x: Math.min(1, Math.max(0, p.x / W())), y: Math.min(1, Math.max(0, p.y / H())) });

function eventPx(e) {
  const r = canvas.getBoundingClientRect();
  return { x: ((e.clientX - r.left) / r.width) * W(), y: ((e.clientY - r.top) / r.height) * H() };
}

function normalUnit(a, b) {
  const nx = -(b.y - a.y), ny = b.x - a.x;
  const n = Math.hypot(nx, ny) || 1;
  return { x: nx / n, y: ny / n };
}

function segDist(p, a, b) {
  const dx = b.x - a.x, dy = b.y - a.y;
  const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / (dx * dx + dy * dy || 1)));
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

function defaultInward() {
  const a = toPx(st.pts[0]), b = toPx(st.pts[1]);
  const n = normalUnit(a, b);
  const d = 0.12 * Math.min(W(), H());
  return toNorm({ x: (a.x + b.x) / 2 + n.x * d, y: (a.y + b.y) / 2 + n.y * d });
}

function flipInward() {
  if (st.pts.length < 2 || !st.inward) return;
  const a = toPx(st.pts[0]), b = toPx(st.pts[1]), i = toPx(st.inward);
  const n = normalUnit(a, b);
  const d = (i.x - a.x) * n.x + (i.y - a.y) * n.y;
  st.inward = toNorm({ x: i.x - 2 * d * n.x, y: i.y - 2 * d * n.y });
  markDirty();
}

// ---------- drawing ----------
function arrow(from, to, color, width) {
  const ang = Math.atan2(to.y - from.y, to.x - from.x);
  const head = 8 + width * 3;
  ctx.strokeStyle = ctx.fillStyle = color;
  ctx.lineWidth = width;
  ctx.beginPath(); ctx.moveTo(from.x, from.y); ctx.lineTo(to.x, to.y); ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(to.x, to.y);
  ctx.lineTo(to.x - head * Math.cos(ang - 0.45), to.y - head * Math.sin(ang - 0.45));
  ctx.lineTo(to.x - head * Math.cos(ang + 0.45), to.y - head * Math.sin(ang + 0.45));
  ctx.closePath(); ctx.fill();
}

function text(str, p, color, size) {
  ctx.font = `bold ${size}px Segoe UI, sans-serif`;
  ctx.lineWidth = 4;
  ctx.strokeStyle = "#000";
  ctx.strokeText(str, p.x, p.y);
  ctx.fillStyle = color;
  ctx.fillText(str, p.x, p.y);
}

function handle(p, label, color) {
  const s = S();
  ctx.fillStyle = color;
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 2 * s;
  ctx.beginPath(); ctx.arc(p.x, p.y, 9 * s, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  text(label, { x: p.x + 12 * s, y: p.y - 10 * s }, "#fff", 18 * s);
}

function drawLine() {
  const s = S();
  if (st.pts.length >= 1) handle(toPx(st.pts[0]), "A", LINE_COLOR);
  if (st.pts.length < 2) return;
  const a = toPx(st.pts[0]), b = toPx(st.pts[1]);
  ctx.strokeStyle = LINE_COLOR; ctx.lineWidth = 4 * s;
  ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
  handle(a, "A", LINE_COLOR);
  handle(b, "B", LINE_COLOR);
  if (!st.inward) return;
  const m = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  let n = normalUnit(a, b);
  const i = toPx(st.inward);
  if ((i.x - m.x) * n.x + (i.y - m.y) * n.y < 0) n = { x: -n.x, y: -n.y };
  const len = 70 * s;
  arrow(m, { x: m.x + n.x * len, y: m.y + n.y * len }, IN_COLOR, 4 * s);
  text("BÊN TRONG (VÀO)", { x: m.x + n.x * (len + 14 * s) - 70 * s, y: m.y + n.y * (len + 14 * s) + 6 * s }, IN_COLOR, 20 * s);
  text("BÊN NGOÀI (RA)", { x: m.x - n.x * (len * 0.8) - 70 * s, y: m.y - n.y * (len * 0.8) + 6 * s }, OUT_COLOR, 20 * s);
  handle(i, "I", IN_COLOR);
}

function drawZone() {
  const s = S();
  const pts = st.zone.map(toPx);
  if (!pts.length) return;
  ctx.beginPath();
  pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
  const closed = pts.length >= 3;
  if (closed) {
    ctx.closePath();
    ctx.fillStyle = "rgba(60, 207, 110, 0.22)";
    ctx.fill();
  }
  ctx.strokeStyle = LINE_COLOR;
  ctx.lineWidth = 4 * s;
  ctx.setLineDash(closed ? [] : [10 * s, 8 * s]);
  ctx.stroke();
  ctx.setLineDash([]);
  pts.forEach((p, i) => handle(p, String(i + 1), LINE_COLOR));
  if (closed) {
    const c = pts.reduce((a, p) => ({ x: a.x + p.x / pts.length, y: a.y + p.y / pts.length }), { x: 0, y: 0 });
    text("VÙNG (BÊN TRONG)", { x: c.x - 90 * s, y: c.y + 8 * s }, IN_COLOR, 22 * s);
  }
}

function draw() {
  ctx.clearRect(0, 0, W(), H());
  if (st.img) ctx.drawImage(st.img, 0, 0, W(), H());
  else { ctx.fillStyle = "#111"; ctx.fillRect(0, 0, W(), H()); text("Đang chờ khung hình…", { x: 30, y: 50 }, "#aaa", 28); }
  if (st.mode === "zone") drawZone();
  else drawLine();
  updatePanel();
}

function updatePanel() {
  const zone = st.mode === "zone";
  document.querySelectorAll("#modeTabs button").forEach((b) => b.classList.toggle("active", b.dataset.mode === st.mode));
  const done = zone
    ? [st.zone.length >= 3, st.zone.length >= 3, !st.dirty && st.zone.length >= 3]
    : [st.pts.length >= 1, st.pts.length >= 2, !!st.inward, !st.dirty && st.pts.length >= 2];
  const cur = done.indexOf(false);
  $("steps").innerHTML = STEPS[st.mode].map((t, i) => `<li class="${done[i] ? "done" : ""} ${i === cur ? "cur" : ""}">${esc(t)}</li>`).join("");
  $("hint").textContent = HINTS[st.mode];
  $("btnFlip").classList.toggle("hidden", zone);
  $("countLabel").textContent = zone ? "Đếm vào/ra vùng" : "Đếm qua line";
  $("ppeWrap").classList.toggle("hidden", !zone);
  $("ppeHint").classList.toggle("hidden", !zone);
  if (!zone) $("chkPpe").checked = false;
  const f = (p) => `(${p.x.toFixed(3)}, ${p.y.toFixed(3)})`;
  $("coords").textContent = zone
    ? (st.zone.length ? `${st.zone.length} điểm: ${st.zone.map(f).join(" ")}` : "")
    : (st.pts.length ? `A ${f(st.pts[0])}${st.pts[1] ? ` · B ${f(st.pts[1])}` : ""}${st.inward ? ` · I ${f(st.inward)}` : ""}` : "");
  $("btnSave").textContent = st.dirty ? "Lưu *" : "Lưu";
}

function markDirty() {
  st.dirty = true;
  draw();
}

// ---------- interaction ----------
function radius() {
  return HANDLE_PX * S() * 1.4;
}

function hitHandle(p) {
  const r = radius();
  if (st.mode === "zone") {
    for (let i = st.zone.length - 1; i >= 0; i--) {
      const q = toPx(st.zone[i]);
      if (Math.hypot(q.x - p.x, q.y - p.y) <= r) return { kind: "z", idx: i };
    }
    return null;
  }
  const cands = [];
  if (st.inward) cands.push([{ kind: "i" }, toPx(st.inward)]);
  st.pts.forEach((q, k) => cands.push([{ kind: k === 0 ? "a" : "b" }, toPx(q)]));
  for (const [key, q] of cands) if (Math.hypot(q.x - p.x, q.y - p.y) <= r) return key;
  return null;
}

function hitEdge(p) {
  if (st.zone.length < 3) return -1;
  const pts = st.zone.map(toPx);
  for (let i = 0; i < pts.length; i++) {
    if (segDist(p, pts[i], pts[(i + 1) % pts.length]) <= radius() * 0.6) return i;
  }
  return -1;
}

canvas.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  const p = eventPx(e);
  const h = hitHandle(p);
  if (h) { st.drag = h; return; }
  if (st.mode === "zone") {
    if (st.zone.length >= MAX_ZONE_POINTS) return toast(`Tối đa ${MAX_ZONE_POINTS} điểm`, true);
    const edge = hitEdge(p);
    const idx = edge >= 0 ? edge + 1 : st.zone.length;
    st.zone.splice(idx, 0, toNorm(p));
    st.drag = { kind: "z", idx };
  } else if (st.pts.length < 2) {
    st.pts.push(toNorm(p));
    if (st.pts.length === 2) st.inward = defaultInward();
  } else {
    st.inward = toNorm(p);
  }
  markDirty();
});
canvas.addEventListener("mousemove", (e) => {
  const p = eventPx(e);
  canvas.style.cursor = st.drag || hitHandle(p) ? "grab" : st.mode === "zone" && hitEdge(p) >= 0 ? "copy" : "crosshair";
  if (!st.drag) return;
  const n = toNorm(p);
  const d = st.drag;
  if (d.kind === "a") st.pts[0] = n;
  else if (d.kind === "b") st.pts[1] = n;
  else if (d.kind === "i") st.inward = n;
  else st.zone[d.idx] = n;
  markDirty();
});
canvas.addEventListener("contextmenu", (e) => {
  if (st.mode !== "zone") return;
  e.preventDefault();
  const h = hitHandle(eventPx(e));
  if (h) {
    st.zone.splice(h.idx, 1);
    markDirty();
  }
});
window.addEventListener("mouseup", () => (st.drag = null));

// ---------- data ----------
function loadSnapshot() {
  if (!st.camId) return;
  const img = new Image();
  img.onload = () => {
    if (canvas.width !== img.naturalWidth || canvas.height !== img.naturalHeight) {
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
    }
    st.img = img;
    draw();
  };
  img.onerror = () => { if (!st.live) setTimeout(loadSnapshot, 1500); };
  img.src = `/api/cameras/${encodeURIComponent(st.camId)}/snapshot.jpg?t=${Date.now()}`;
}

async function selectCamera(id, push = true) {
  st.camId = id;
  if (push) history.replaceState(null, "", `/calibrate/${encodeURIComponent(id)}`);
  $("camSelect").value = id;
  $("lnkLive").onclick = () => localStorage.setItem("live.selected", id);
  const cam = await api(`/api/cameras/${encodeURIComponent(id)}`);
  const c = cam.crossing || {};
  st.mode = c.mode === "zone" ? "zone" : "line";
  st.pts = (c.points || []).slice(0, 2);
  st.inward = c.inward_point || null;
  st.zone = (c.zone || []).slice(0, MAX_ZONE_POINTS);
  if (st.pts.length === 2 && !st.inward) st.inward = defaultInward();
  const tasks = Array.isArray(c.tasks) ? c.tasks : (c.enabled !== false ? ["count"] : []);
  $("chkCount").checked = tasks.includes("count");
  $("chkPpe").checked = tasks.includes("ppe");
  st.dirty = false;
  st.img = null;
  draw();
  loadSnapshot();
}

async function save() {
  if (st.mode === "line" && st.pts.length === 1) return toast("Cần đủ 2 điểm A và B", true);
  if (st.mode === "zone" && st.zone.length > 0 && st.zone.length < 3) return toast("Vùng cần ít nhất 3 điểm", true);
  const tasks = [];
  if ($("chkCount").checked) tasks.push("count");
  if (st.mode === "zone" && $("chkPpe").checked) tasks.push("ppe");
  if (st.mode === "zone" && $("chkPpe").checked && st.zone.length < 3) {
    return toast("Bật PPE cần vùng ít nhất 3 điểm", true);
  }
  try {
    await api(`/api/cameras/${encodeURIComponent(st.camId)}/crossing`, {
      method: "PUT",
      body: {
        enabled: tasks.includes("count"),
        mode: st.mode,
        points: st.pts.length === 2 ? st.pts : [],
        inward_point: st.pts.length === 2 ? st.inward : null,
        zone: st.zone.length >= 3 ? st.zone : [],
        tasks,
      },
    });
    st.dirty = false;
    draw();
    const what = st.mode === "zone" ? "vùng" : "line";
    const empty = st.mode === "zone" ? !st.zone.length : !st.pts.length;
    const taskTxt = tasks.length ? ` [${tasks.join(", ")}]` : "";
    toast(empty ? `Đã xóa ${what}` : `Đã lưu ${what}${taskTxt}, áp dụng ngay`);
  } catch (e) {
    toast(e.message, true);
  }
}

async function pollCounts() {
  if (!st.camId) return;
  try {
    const s = await api("/api/status");
    const c = s.cameras.find((x) => x.id === st.camId);
    const cr = (c && c.crossing) || {};
    $("cVisible").textContent = (c && c.persons) ?? 0;
    $("cInside").textContent = cr.inside ?? 0;
    $("cOutside").textContent = cr.outside ?? 0;
    $("metaInfo").textContent = c ? `${c.name}: ${statusText(c.status)}` : "";
  } catch { /* ignore */ }
}

document.querySelectorAll("#modeTabs button").forEach((b) => (b.onclick = () => {
  if (st.mode === b.dataset.mode) return;
  st.mode = b.dataset.mode;
  markDirty();
}));
$("btnSnap").onclick = loadSnapshot;
$("btnLive").onclick = () => {
  st.live = !st.live;
  $("btnLive").classList.toggle("on", st.live);
  clearInterval(st.liveTimer);
  if (st.live) st.liveTimer = setInterval(loadSnapshot, 700);
};
$("btnFlip").onclick = flipInward;
$("btnClear").onclick = () => {
  if (st.mode === "zone") st.zone = [];
  else { st.pts = []; st.inward = null; }
  markDirty();
};
$("btnSave").onclick = save;
$("chkCount").onchange = () => { st.dirty = true; updatePanel(); };
$("chkPpe").onchange = () => { st.dirty = true; updatePanel(); };
$("btnResetCount").onclick = async () => {
  try {
    await api(`/api/cameras/${encodeURIComponent(st.camId)}/crossing/reset`, { method: "POST" });
    toast("Đã reset đếm");
  } catch (e) { toast(e.message, true); }
};
$("camSelect").onchange = () => {
  if (st.dirty && !confirm("Thay đổi chưa lưu, vẫn chuyển camera?")) { $("camSelect").value = st.camId; return; }
  selectCamera($("camSelect").value);
};
window.addEventListener("beforeunload", (e) => { if (st.dirty) e.preventDefault(); });

(async function init() {
  const cams = await api("/api/cameras");
  const enabled = cams.filter((c) => c.enabled);
  $("camSelect").innerHTML = cams
    .map((c) => `<option value="${esc(c.id)}" ${c.enabled ? "" : "disabled"}>${esc(c.name)}${c.enabled ? "" : " (tắt)"}</option>`)
    .join("");
  const fromPath = decodeURIComponent(location.pathname.split("/calibrate/")[1] || "");
  const target = cams.find((c) => c.id === fromPath && c.enabled) || enabled[0];
  if (!target) {
    draw();
    toast("Chưa có camera đang bật", true);
    return;
  }
  await selectCamera(target.id, !fromPath || fromPath !== target.id);
  pollCounts();
  setInterval(pollCounts, 1000);
})();
