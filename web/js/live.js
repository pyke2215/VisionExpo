renderNav("live");

const state = {
  cams: [],
  selected: localStorage.getItem("live.selected") || null,
  grid: false,
  auto: false,
  autoTimer: null,
  gridTimer: null,
};
const AUTO_INTERVAL_MS = 10000;
const PPE_VI = { Hardhat: "Có mũ", "NO-Hardhat": "Thiếu mũ", Gloves: "Có găng", "NO-Gloves": "Thiếu găng" };
const GRID_REFRESH_MS = 250;
const HEAT_REFRESH_MS = 30000;
const zoneMeta = { colors: {} };
const heat = { on: localStorage.getItem("live.heat") === "1", data: null, cam: null, timer: null };

const $ = (id) => document.getElementById(id);
const stage = $("stage");

function runningCams() {
  return state.cams.filter((c) => c.status && c.status !== "disabled" && c.status !== "stopped");
}

function select(id) {
  state.selected = id;
  localStorage.setItem("live.selected", id || "");
  state.grid = false;
  renderStage();
  renderList();
}

function closeStreams() {
  // Release long-lived MJPEG connections: browsers allow only ~6 per host.
  stage.querySelectorAll("img").forEach((img) => (img.src = ""));
  clearInterval(state.gridTimer);
}

function renderStage() {
  closeStreams();
  $("btnGrid").classList.toggle("on", state.grid);
  const cams = runningCams();
  if (!cams.length) {
    stage.innerHTML = `<div class="empty">Chưa có camera đang chạy. Vào trang <a href="/cameras">Cameras</a> để thêm.</div>`;
    $("viewTitle").textContent = "—";
    return;
  }
  if (state.grid) {
    const n = cams.length;
    const cols = Math.ceil(Math.sqrt(n));
    stage.innerHTML = `<div class="grid-view" style="grid-template-columns:repeat(${cols},1fr)">${cams
      .map((c) => `<div class="tile" data-id="${esc(c.id)}"><img data-id="${esc(c.id)}" /><div class="cap">${esc(c.name)}</div></div>`)
      .join("")}</div>`;
    stage.querySelectorAll(".tile").forEach((t) => t.addEventListener("click", () => select(t.dataset.id)));
    const refresh = () =>
      stage.querySelectorAll(".grid-view img").forEach((img) => {
        if (img.dataset.busy) return;
        img.dataset.busy = "1";
        const next = new Image();
        next.onload = () => { img.src = next.src; delete img.dataset.busy; };
        next.onerror = () => delete img.dataset.busy;
        next.src = `/api/cameras/${encodeURIComponent(img.dataset.id)}/frame.jpg?t=${Date.now()}`;
      });
    refresh();
    state.gridTimer = setInterval(refresh, GRID_REFRESH_MS);
    $("viewTitle").textContent = `Lưới ${n} camera`;
    return;
  }
  if (!cams.find((c) => c.id === state.selected)) state.selected = cams[0].id;
  const cam = cams.find((c) => c.id === state.selected);
  $("viewTitle").textContent = cam.name;
  const img = document.createElement("img");
  img.alt = cam.name;
  img.src = `/api/cameras/${encodeURIComponent(cam.id)}/stream.mjpg?t=${Date.now()}`;
  img.onerror = () => setTimeout(() => { if (!state.grid && state.selected === cam.id) img.src = `/api/cameras/${encodeURIComponent(cam.id)}/stream.mjpg?t=${Date.now()}`; }, 2000);
  img.onload = placeHeat;
  stage.innerHTML = "";
  stage.appendChild(img);
  const cv = document.createElement("canvas");
  cv.className = "heat";
  stage.appendChild(cv);
  loadHeat();
}

// ---------- heatmap overlay (today, selected camera) ----------
async function loadHeat() {
  clearTimeout(heat.timer);
  $("btnHeat").classList.toggle("on", heat.on);
  if (!heat.on || state.grid || !state.selected) return placeHeat();
  try {
    heat.data = await api(`/api/heatmap?camera_id=${encodeURIComponent(state.selected)}&from=${localMidnight()}`);
    heat.cam = state.selected;
  } catch { heat.data = null; }
  placeHeat();
  heat.timer = setTimeout(loadHeat, HEAT_REFRESH_MS);
}

function placeHeat() {
  const cv = stage.querySelector("canvas.heat");
  const img = stage.querySelector("img");
  if (!cv) return;
  if (!heat.on || !img || !img.clientWidth || heat.cam !== state.selected) { cv.width = cv.height = 0; return; }
  const s = stage.getBoundingClientRect(), r = img.getBoundingClientRect();
  cv.style.left = `${r.left - s.left}px`;
  cv.style.top = `${r.top - s.top}px`;
  cv.style.width = `${r.width}px`;
  cv.style.height = `${r.height}px`;
  drawHeat(cv, heat.data, Math.round(r.width), Math.round(r.height));
}

const zoneDot = (kind) => `<i style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${zoneMeta.colors[kind] || "#ccc"}"></i>`;

function renderList() {
  const list = $("camList");
  list.innerHTML = state.cams
    .map((c, i) => {
      const cr = c.crossing || {};
      const sub =
        c.status === "running"
          ? `${c.fps ?? 0} fps · ${c.persons ?? 0} người${cr.enabled ? ` · trong ${cr.inside} / ngoài ${cr.outside}` : ""}` +
            (c.ppe_alerts ? ` · <span style="color:var(--red)">${c.ppe_alerts} thiếu PPE</span>` : "")
          : esc(c.error || statusText(c.status));
      return `<div class="cam-item ${!state.grid && c.id === state.selected ? "active" : ""}" data-id="${esc(c.id)}">
        <div class="name"><span class="dot ${dotClass(c.status)}"></span>${i + 1}. ${esc(c.name)}</div>
        <div class="sub">${sub}</div></div>`;
    })
    .join("");
  list.querySelectorAll(".cam-item").forEach((el) => el.addEventListener("click", () => select(el.dataset.id)));
}

function renderSide() {
  const cams = state.grid ? runningCams() : runningCams().filter((c) => c.id === state.selected);
  const people = cams.flatMap((c) => (c.people || []).map((p) => ({ ...p, cam: c.name })));
  $("peopleList").innerHTML = people.length
    ? people
        .map(
          (p) => `<div class="it ${p.ppe_alert ? "alert" : ""}"><strong>#${p.track_id ?? "?"}</strong>
          <span class="tag ${p.side}">${p.side === "in" ? "TRONG" : p.side === "out" ? "NGOÀI" : "?"}</span>
          ${(p.zones || []).map((z) => `<span class="tag unknown">${zoneDot(z.kind)} ${esc(z.name)} ${fmtDuration(z.dwell_s)}</span>`).join("")}
          ${(p.ppe || []).map((l) => `<span class="tag ${l.startsWith("NO-") ? "alert" : "in"}">${esc(PPE_VI[l] || l)}</span>`).join("")}
          ${state.grid ? `<span class="muted small">${esc(p.cam)}</span>` : ""}</div>`
        )
        .join("")
    : `<div class="muted small">Không có ai</div>`;

  const zones = cams.flatMap((c) => ((c.crossing || {}).zones || []).map((z) => ({ ...z, cam: c.name })));
  $("zonePanel").classList.toggle("hidden", !zones.length);
  $("zoneList").innerHTML = zones
    .map((z) => `<div class="it ${z.crowded ? "alert" : ""}">${zoneDot(z.kind)}<strong>${esc(z.name)}</strong>
      <span class="grow muted small">${state.grid ? esc(z.cam) : ""}</span>
      <strong>${z.inside}</strong><span class="muted small">người</span>
      ${z.dwell_avg_s != null ? `<span class="muted small">TB ${fmtDuration(z.dwell_avg_s)}</span>` : ""}
      ${z.tasks.includes("count") ? `<span class="muted small">vào ${z.entries}/ra ${z.exits}</span>` : ""}
      ${z.crowded ? `<span class="tag alert">ĐÔNG</span>` : ""}</div>`)
    .join("");

  const events = cams
    .flatMap((c) => ((c.crossing || {}).events || []).map((e) => ({ ...e, cam: c.name })))
    .sort((a, b) => b.ts - a.ts)
    .slice(0, 12);
  $("eventList").innerHTML = events.length
    ? events
        .map(
          (e) => `<div class="it"><span class="tag ${e.direction}">${e.direction === "in" ? "VÀO" : "RA"}</span>
          <strong>#${e.track_id}</strong><span class="muted small grow">${esc(e.zone_name || e.cam)}${e.dwell_s != null ? ` · ${fmtDuration(e.dwell_s)}` : ""}</span>
          <span class="muted small">${fmtTime(e.ts)}</span></div>`
        )
        .join("")
    : `<div class="muted small">Chưa có sự kiện</div>`;

  const ppeCams = cams.filter((c) => (c.crossing || {}).ppe_active);
  $("ppePanel").classList.toggle("hidden", !ppeCams.length);
  const ppeCount = {};
  ppeCams.forEach((c) => Object.entries(c.ppe || {}).forEach(([k, v]) => (ppeCount[k] = (ppeCount[k] || 0) + v)));
  const ppe = Object.entries(ppeCount).sort((a, b) => b[1] - a[1]);
  $("ppeList").innerHTML = ppe.length
    ? ppe.map(([k, v]) => `<span class="chip on ${k.startsWith("NO-") ? "alert" : ""}">${esc(PPE_VI[k] || k)} ×${v}</span>`).join("")
    : `<span class="muted small">Chưa có ai trong vùng PPE</span>`;
}

async function poll() {
  try {
    const s = await api("/api/status");
    const before = runningCams().map((c) => c.id).join(",");
    state.cams = s.cameras;
    const after = runningCams().map((c) => c.id).join(",");
    $("kPersons").textContent = s.totals.persons;
    $("kInside").textContent = s.totals.inside;
    $("kOutside").textContent = s.totals.outside;
    $("kVisitors").textContent = s.totals.visitors_today ?? 0;
    $("kPpe").textContent = s.totals.ppe_alerts || 0;
    $("kPpeBox").classList.toggle("active", (s.totals.ppe_alerts || 0) > 0);
    const pp = s.ppe || {};
    $("ppeState").textContent = pp.enabled
      ? `PPE: ${pp.status}${pp.error ? " – " + pp.error : ""} · ${pp.mode} · ${pp.last_infer_ms} ms`
      : "PPE đang tắt";
    $("metaInfo").textContent = `device ${s.device} · ${runningCams().length}/${s.cameras.length} camera`;
    if (before !== after || !stage.querySelector("img")) renderStage();
    renderList();
    renderSide();
  } catch (e) {
    $("metaInfo").textContent = "Mất kết nối server";
  }
}

function setAuto(on) {
  state.auto = on;
  $("btnAuto").classList.toggle("on", on);
  clearInterval(state.autoTimer);
  if (on) {
    state.autoTimer = setInterval(() => {
      const cams = runningCams();
      if (cams.length < 2) return;
      const i = cams.findIndex((c) => c.id === state.selected);
      select(cams[(i + 1) % cams.length].id);
    }, AUTO_INTERVAL_MS);
  }
}

function toggleKiosk() {
  document.body.classList.toggle("kiosk");
}

$("btnGrid").onclick = () => { state.grid = !state.grid; renderStage(); renderList(); };
$("btnAuto").onclick = () => setAuto(!state.auto);
$("btnKiosk").onclick = toggleKiosk;
$("btnHeat").onclick = () => {
  heat.on = !heat.on;
  try { localStorage.setItem("live.heat", heat.on ? "1" : "0"); } catch { /* private mode */ }
  loadHeat();
};
window.addEventListener("resize", placeHeat);
api("/api/zones/meta").then((m) => Object.assign(zoneMeta, m)).catch(() => {});
$("btnFull").onclick = () => (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen());
$("btnReset").onclick = async () => {
  if (!confirm("Reset bộ đếm vào/ra của tất cả camera?")) return;
  await api("/api/crossing/reset_all", { method: "POST" });
  toast("Đã reset bộ đếm");
};

document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, textarea, select")) return;
  const cams = runningCams();
  if (/^[1-9]$/.test(e.key) && cams[+e.key - 1]) select(cams[+e.key - 1].id);
  else if (e.key === "g" || e.key === "G") $("btnGrid").click();
  else if (e.key === "a" || e.key === "A") $("btnAuto").click();
  else if (e.key === "f" || e.key === "F") $("btnFull").click();
  else if (e.key === "k" || e.key === "K" || e.key === "Escape") {
    if (e.key !== "Escape" || document.body.classList.contains("kiosk")) toggleKiosk();
  }
});

// URL options for unattended screens, e.g. /?kiosk=1&grid=1 or /?kiosk=1&auto=1&cam=gate
(function applyUrlOptions() {
  const q = new URLSearchParams(location.search);
  if (q.get("cam")) state.selected = q.get("cam");
  if (q.get("grid") === "1") state.grid = true;
  if (q.get("auto") === "1") setAuto(true);
  if (q.get("kiosk") === "1") document.body.classList.add("kiosk");
})();

poll();
setInterval(poll, 1000);
