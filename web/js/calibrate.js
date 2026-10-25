renderNav("calibrate");

const $ = (id) => document.getElementById(id);
const canvas = $("canvas");
const ctx = canvas.getContext("2d");
const HANDLE_PX = 14;
const IN_COLOR = "#3ccf6e";
const OUT_COLOR = "#ff9f2e";
const LINE_COLOR = "#ff00c8";
const MAX_ZONE_POINTS = 32;
const MAX_ZONES = 12;

const STEPS = {
  line: [
    "Click điểm đầu A của line",
    "Click điểm cuối B của line",
    'Click vào phía "BÊN TRONG" (mũi tên xanh chỉ chiều VÀO)',
    "Bấm Lưu",
  ],
  zone: [
    "Thêm vùng (Booth area trước, rồi các khu bên trong) và chọn vùng trong danh sách",
    "Click lần lượt các góc của vùng đang chọn (ít nhất 3 điểm); kéo điểm để chỉnh, click lên cạnh để chèn, chuột phải để xóa",
    "Chọn task và quy tắc cho từng vùng, rồi bấm Lưu",
  ],
};
const HINTS = {
  line: "Kéo các điểm A, B, I để chỉnh. Người được tính khi điểm chân (mắt cá) đi qua đoạn A–B. Nên đặt line trên sàn, vuông góc hướng đi, tránh mép khung hình.",
  zone: "Chỉ vùng đang chọn sửa được, các vùng khác vẽ mờ. Click gần một đỉnh của vùng khác để chọn vùng đó. Vẽ vùng trên sàn và chừa khoảng trống với mép khung hình ở phía người đi tới.",
};

const st = {
  camId: null,
  img: null,
  mode: "line",
  pts: [], // line A, B (normalized)
  inward: null, // line inward point (normalized)
  zones: [], // [{id, kind, name, points, tasks, rule, overlap, crowd_max, crowd_s}]
  sel: -1, // selected zone index
  drag: null, // {kind: "a" | "b" | "i" | "z", idx}
  live: false,
  liveTimer: null,
  dirty: false,
  meta: { kinds: {}, colors: {} },
};

const zoneColor = (z) => st.meta.colors[z.kind] || "#c8c8c8";
const selZone = () => st.zones[st.sel] || null;

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

function handle(p, label, color, r = 9) {
  const s = S();
  ctx.fillStyle = color;
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 2 * s;
  ctx.beginPath(); ctx.arc(p.x, p.y, r * s, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  if (label) text(label, { x: p.x + 12 * s, y: p.y - 10 * s }, "#fff", 18 * s);
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

function drawOneZone(z, selected) {
  const s = S();
  const pts = z.points.map(toPx);
  if (!pts.length) return;
  const color = zoneColor(z);
  const closed = pts.length >= 3;
  ctx.globalAlpha = selected ? 1 : 0.55;
  ctx.beginPath();
  pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
  if (closed) ctx.closePath();
  if (selected && closed) {
    ctx.fillStyle = color + "26"; // light tint while editing only; the live overlay draws edges only
    ctx.fill();
  }
  ctx.strokeStyle = color;
  ctx.lineWidth = (selected ? 4 : 2.5) * s;
  ctx.setLineDash(closed ? [] : [10 * s, 8 * s]);
  ctx.stroke();
  ctx.setLineDash([]);
  if (selected) pts.forEach((p, i) => handle(p, String(i + 1), color));
  else pts.forEach((p) => handle(p, "", color, 5));
  if (closed) {
    const top = pts.reduce((a, p) => (p.y < a.y ? p : a), pts[0]);
    text(z.name, { x: top.x + 8 * s, y: top.y - 12 * s }, color, (selected ? 22 : 18) * s);
  }
  ctx.globalAlpha = 1;
}

function draw() {
  ctx.clearRect(0, 0, W(), H());
  if (st.img) ctx.drawImage(st.img, 0, 0, W(), H());
  else { ctx.fillStyle = "#111"; ctx.fillRect(0, 0, W(), H()); text("Đang chờ khung hình…", { x: 30, y: 50 }, "#aaa", 28); }
  if (st.mode === "zone") {
    st.zones.forEach((z, i) => i !== st.sel && drawOneZone(z, false));
    if (selZone()) drawOneZone(selZone(), true);
  } else drawLine();
  updatePanel();
}

function renderZoneList() {
  $("zoneList").innerHTML = st.zones.length
    ? st.zones
        .map((z, i) => {
          const tasks = [z.tasks.includes("count") ? "đếm" : "", z.tasks.includes("ppe") ? "PPE" : "", z.crowd_max ? `đông>${z.crowd_max}` : ""]
            .filter(Boolean).join(" · ") || "không task";
          const warn = z.points.length < 3 ? ` <span class="tag alert">cần ≥ 3 điểm</span>` : "";
          return `<div class="it ${i === st.sel ? "active" : ""}" data-i="${i}" style="cursor:pointer">
            <i class="led" style="display:inline-block;width:12px;height:12px;border-radius:50%;background:${zoneColor(z)}"></i>
            <strong>${esc(z.name)}</strong>
            <span class="muted small grow">${esc(st.meta.kinds[z.kind] || z.kind)} · ${z.points.length} điểm · ${esc(tasks)}</span>${warn}
          </div>`;
        })
        .join("")
    : `<div class="muted small">Chưa có vùng. Chọn loại rồi bấm "Thêm vùng".</div>`;
  $("zoneList").querySelectorAll(".it").forEach((el) => (el.onclick = () => selectZone(Number(el.dataset.i))));
}

function renderZoneProps() {
  const z = selZone();
  $("zoneProps").classList.toggle("hidden", !z);
  if (!z) return;
  $("zName").value = z.name;
  $("zKind").value = z.kind;
  $("zCount").checked = z.tasks.includes("count");
  $("zPpe").checked = z.tasks.includes("ppe");
  $("selRule").value = z.rule;
  $("numOverlap").value = Math.round(z.overlap * 100);
  $("zCrowdMax").value = z.crowd_max;
  $("zCrowdS").value = z.crowd_s;
}

function updatePanel() {
  const zone = st.mode === "zone";
  document.querySelectorAll("#modeTabs button").forEach((b) => b.classList.toggle("active", b.dataset.mode === st.mode));
  const valid = st.zones.length > 0 && st.zones.every((z) => z.points.length >= 3);
  const done = zone
    ? [st.zones.length > 0, valid, !st.dirty && valid]
    : [st.pts.length >= 1, st.pts.length >= 2, !!st.inward, !st.dirty && st.pts.length >= 2];
  const cur = done.indexOf(false);
  $("steps").innerHTML = STEPS[st.mode].map((t, i) => `<li class="${done[i] ? "done" : ""} ${i === cur ? "cur" : ""}">${esc(t)}</li>`).join("");
  $("hint").textContent = HINTS[st.mode];
  $("lineWrap").classList.toggle("hidden", zone);
  $("zoneWrap").classList.toggle("hidden", !zone);
  $("overlapWrap").classList.toggle("hidden", $("selRule").value === "foot");
  $("overlapWhat").textContent = $("selRule").value === "box" ? "% khung người" : "% thân dưới (40% dưới của khung người)";
  const f = (p) => `(${p.x.toFixed(3)}, ${p.y.toFixed(3)})`;
  const z = selZone();
  $("coords").textContent = zone
    ? (z && z.points.length ? `${z.name}: ${z.points.length} điểm ${z.points.map(f).join(" ")}` : "")
    : (st.pts.length ? `A ${f(st.pts[0])}${st.pts[1] ? ` · B ${f(st.pts[1])}` : ""}${st.inward ? ` · I ${f(st.inward)}` : ""}` : "");
  $("btnSave").textContent = st.dirty ? "Lưu *" : "Lưu";
  if (zone) renderZoneList();
}

function markDirty() {
  st.dirty = true;
  draw();
}

function selectZone(i) {
  st.sel = i;
  renderZoneProps();
  draw();
}

function newZoneId() {
  const used = new Set(st.zones.map((z) => z.id));
  let n = 1;
  while (used.has(`z${n}`)) n++;
  return `z${n}`;
}

function addZone(kind) {
  if (st.zones.length >= MAX_ZONES) return toast(`Tối đa ${MAX_ZONES} vùng`, true);
  const sameKind = st.zones.filter((z) => z.kind === kind).length;
  const base = st.meta.kinds[kind] || kind;
  st.zones.push({
    id: newZoneId(), kind, name: sameKind ? `${base} ${sameKind + 1}` : base, points: [],
    tasks: kind === "booth" ? ["count"] : [], rule: "body", overlap: 0.5, crowd_max: 0, crowd_s: 10,
  });
  selectZone(st.zones.length - 1);
  markDirty();
  toast("Click lên ảnh để vẽ các góc của vùng mới");
}

// ---------- interaction ----------
function radius() {
  return HANDLE_PX * S() * 1.4;
}

function hitHandle(p) {
  const r = radius();
  if (st.mode === "zone") {
    const z = selZone();
    if (z) {
      for (let i = z.points.length - 1; i >= 0; i--) {
        const q = toPx(z.points[i]);
        if (Math.hypot(q.x - p.x, q.y - p.y) <= r) return { kind: "z", idx: i };
      }
    }
    return null;
  }
  const cands = [];
  if (st.inward) cands.push([{ kind: "i" }, toPx(st.inward)]);
  st.pts.forEach((q, k) => cands.push([{ kind: k === 0 ? "a" : "b" }, toPx(q)]));
  for (const [key, q] of cands) if (Math.hypot(q.x - p.x, q.y - p.y) <= r) return key;
  return null;
}

function hitOtherZone(p) {
  const r = radius();
  for (let k = st.zones.length - 1; k >= 0; k--) {
    if (k === st.sel) continue;
    if (st.zones[k].points.some((q) => { const v = toPx(q); return Math.hypot(v.x - p.x, v.y - p.y) <= r; })) return k;
  }
  return -1;
}

function hitEdge(p) {
  const z = selZone();
  if (!z || z.points.length < 3) return -1;
  const pts = z.points.map(toPx);
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
    const other = hitOtherZone(p);
    if (other >= 0) return selectZone(other);
    const z = selZone();
    if (!z) return toast('Thêm hoặc chọn một vùng trước ("+ Thêm vùng")', true);
    if (z.points.length >= MAX_ZONE_POINTS) return toast(`Tối đa ${MAX_ZONE_POINTS} điểm`, true);
    const edge = hitEdge(p);
    const idx = edge >= 0 ? edge + 1 : z.points.length;
    z.points.splice(idx, 0, toNorm(p));
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
  canvas.style.cursor = st.drag || hitHandle(p) ? "grab"
    : st.mode === "zone" && hitOtherZone(p) >= 0 ? "pointer"
    : st.mode === "zone" && hitEdge(p) >= 0 ? "copy" : "crosshair";
  if (!st.drag) return;
  const n = toNorm(p);
  const d = st.drag;
  if (d.kind === "a") st.pts[0] = n;
  else if (d.kind === "b") st.pts[1] = n;
  else if (d.kind === "i") st.inward = n;
  else if (selZone()) selZone().points[d.idx] = n;
  markDirty();
});
canvas.addEventListener("contextmenu", (e) => {
  if (st.mode !== "zone") return;
  e.preventDefault();
  const h = hitHandle(eventPx(e));
  if (h && selZone()) {
    selZone().points.splice(h.idx, 1);
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
  st.zones = (c.zones || []).map((z) => ({ ...z, points: (z.points || []).slice(0, MAX_ZONE_POINTS), tasks: [...(z.tasks || [])] }));
  st.sel = st.zones.length ? 0 : -1;
  if (st.pts.length === 2 && !st.inward) st.inward = defaultInward();
  $("chkCount").checked = (c.tasks || []).includes("count");
  st.dirty = false;
  st.img = null;
  renderZoneProps();
  draw();
  loadSnapshot();
}

async function save() {
  if (st.mode === "line" && st.pts.length === 1) return toast("Cần đủ 2 điểm A và B", true);
  const bad = st.zones.find((z) => z.points.length < 3);
  if (st.mode === "zone" && bad) {
    selectZone(st.zones.indexOf(bad));
    return toast(`Vùng "${bad.name}" cần ít nhất 3 điểm (hoặc xóa vùng đó)`, true);
  }
  try {
    const cr = await api(`/api/cameras/${encodeURIComponent(st.camId)}/crossing`, {
      method: "PUT",
      body: {
        mode: st.mode,
        points: st.pts.length === 2 ? st.pts : [],
        inward_point: st.pts.length === 2 ? st.inward : null,
        tasks: $("chkCount").checked ? ["count"] : [],
        zones: st.zones,
      },
    });
    st.zones = cr.zones.map((z) => ({ ...z, tasks: [...z.tasks] })); // server-normalized (ids, ranges)
    st.sel = Math.min(st.sel, st.zones.length - 1);
    st.dirty = false;
    renderZoneProps();
    draw();
    toast(st.mode === "zone" ? `Đã lưu ${st.zones.length} vùng, áp dụng ngay` : (st.pts.length ? "Đã lưu line, áp dụng ngay" : "Đã xóa line"));
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
    $("cZones").innerHTML = (cr.zones || [])
      .map((z) => `<div class="it"><i style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${st.meta.colors[z.kind] || "#ccc"}"></i>
        <strong>${esc(z.name)}</strong><span class="grow"></span><span>${z.inside} người</span>
        ${z.tasks.includes("count") ? `<span class="muted">vào ${z.entries} / ra ${z.exits}</span>` : ""}
        ${z.crowded ? `<span class="tag alert">ĐÔNG</span>` : ""}</div>`)
      .join("");
    $("metaInfo").textContent = c ? `${c.name}: ${statusText(c.status)}` : "";
  } catch { /* ignore */ }
}

function editZone(fn) {
  const z = selZone();
  if (!z) return;
  fn(z);
  markDirty();
}

document.querySelectorAll("#modeTabs button").forEach((b) => (b.onclick = () => {
  if (st.mode === b.dataset.mode) return;
  st.mode = b.dataset.mode;
  renderZoneProps();
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
$("btnClear").onclick = () => { st.pts = []; st.inward = null; markDirty(); };
$("btnSave").onclick = save;
$("chkCount").onchange = () => { st.dirty = true; updatePanel(); };
$("btnAddZone").onclick = () => addZone($("selNewKind").value);
$("btnClearZone").onclick = () => editZone((z) => (z.points = []));
$("btnDelZone").onclick = () => {
  const z = selZone();
  if (!z || !confirm(`Xóa vùng "${z.name}"?`)) return;
  st.zones.splice(st.sel, 1);
  st.sel = Math.min(st.sel, st.zones.length - 1);
  renderZoneProps();
  markDirty();
};
$("zName").oninput = () => editZone((z) => (z.name = $("zName").value));
$("zKind").onchange = () => editZone((z) => {
  const oldDefault = st.meta.kinds[z.kind];
  z.kind = $("zKind").value;
  if (!z.name || z.name === oldDefault) z.name = st.meta.kinds[z.kind] || z.kind;
  $("zName").value = z.name;
});
const setTask = (task, on) => editZone((z) => {
  z.tasks = z.tasks.filter((t) => t !== task);
  if (on) z.tasks.push(task);
});
$("zCount").onchange = () => setTask("count", $("zCount").checked);
$("zPpe").onchange = () => setTask("ppe", $("zPpe").checked);
$("selRule").onchange = () => editZone((z) => (z.rule = $("selRule").value));
$("numOverlap").oninput = () => editZone((z) => (z.overlap = Math.min(90, Math.max(10, Number($("numOverlap").value) || 50)) / 100));
$("zCrowdMax").oninput = () => editZone((z) => (z.crowd_max = Math.max(0, Math.round(Number($("zCrowdMax").value) || 0))));
$("zCrowdS").oninput = () => editZone((z) => (z.crowd_s = Math.max(1, Number($("zCrowdS").value) || 10)));
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
  try {
    st.meta = await api("/api/zones/meta");
  } catch { /* defaults */ }
  const kindOpts = Object.entries(st.meta.kinds || {}).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join("");
  $("selNewKind").innerHTML = kindOpts;
  $("zKind").innerHTML = kindOpts;
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
