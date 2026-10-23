renderNav("ppe");

const $ = (id) => document.getElementById(id);
let info = null;
let saveTimer = null;
let pending = {};

const STATUS_VI = { ready: "Sẵn sàng", loading: "Đang tải model", error: "Lỗi", idle: "Chưa tải", off: "Tắt" };

async function load() {
  info = await api("/api/ppe");
  renderHeader(true);
  renderCams();
}

function renderHeader(withControls = false) {
  const cfg = info.config || {};
  $("btnEnable").textContent = info.enabled ? "Đang BẬT – bấm để tắt" : "Đang TẮT – bấm để bật";
  $("btnEnable").classList.toggle("on", !!info.enabled);
  const status = info.enabled ? info.status : "off";
  $("pStatus").textContent = STATUS_VI[status] || status;
  $("pStatus").className = `badge ${status}`;
  $("pInfo").textContent =
    `Model ${info.model}` +
    (info.classes?.length ? ` · lớp ${info.classes.join(", ")}` : "") +
    (info.last_infer_ms ? ` · ${info.last_infer_ms} ms/lần` : "") +
    (info.error ? ` · ${info.error}` : "");
  $("maskInfo").textContent = !cfg.person_mask
    ? "Đang tắt: cả vòng tròn quanh bàn tay được đo, ghế / bàn màu da có thể bị tính là tay trần."
    : info.person_mask
      ? `Đang dùng ${info.mask_model}${info.last_mask_ms ? ` · ${info.last_mask_ms} ms/lần` : ""}`
      : `Chưa nạp được ${info.mask_model} (thiếu file trong models/?): đang đo cả vòng tròn quanh tay.`;
  if (!withControls) return;
  const req = cfg.require ?? ["hardhat", "gloves"];
  $("reqHardhat").checked = req.includes("hardhat");
  $("reqGloves").checked = req.includes("gloves");
  $("inferMissing").checked = cfg.infer_missing !== false;
  $("confirmRuns").value = cfg.confirm_runs ?? 3;
  $("mode").value = cfg.mode || "crop";
  $("conf").value = cfg.conf ?? 0.25;
  $("confVal").textContent = Number(cfg.conf ?? 0.25).toFixed(2);
  $("imgsz").value = cfg.imgsz ?? 640;
  $("everyN").value = cfg.every_n ?? 2;
  $("personMask").checked = cfg.person_mask !== false;
  $("maskImgsz").value = cfg.mask_imgsz ?? 640;
  syncControls();
}

function syncControls() {
  $("confirmWrap").classList.toggle("hidden", !$("inferMissing").checked);
  $("maskImgszWrap").classList.toggle("hidden", !$("personMask").checked);
}

function putPpe(patch, delay = 0) {
  Object.assign(pending, patch);
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushPpe, delay);
}

async function flushPpe() {
  clearTimeout(saveTimer);
  const patch = pending;
  pending = {};
  if (!Object.keys(patch).length) return;
  try {
    info = await api("/api/ppe", { method: "PUT", body: patch });
    renderHeader();
    const ignored = Object.keys(patch).filter((k) => !(k in (info.config || {})));
    if (ignored.length) toast(`Server chưa hỗ trợ ${ignored.join(", ")} – hãy khởi động lại app`, true);
    else toast("Đã lưu");
  } catch (e) {
    toast(e.message, true);
  }
}

// Leaving the page must not drop a change that is still waiting for its debounce.
window.addEventListener("pagehide", () => {
  if (!Object.keys(pending).length) return;
  fetch("/api/ppe", { method: "PUT", keepalive: true, headers: { "Content-Type": "application/json" }, body: JSON.stringify(pending) });
  pending = {};
});

function bindNumber(id, key) {
  const send = (delay) => {
    const v = $(id).value.trim();
    if (v !== "" && !Number.isNaN(Number(v))) putPpe({ [key]: Number(v) }, delay);
  };
  $(id).addEventListener("input", () => send(600));
  $(id).addEventListener("change", () => send(0));
}

function sendRequire() {
  const req = [];
  if ($("reqHardhat").checked) req.push("hardhat");
  if ($("reqGloves").checked) req.push("gloves");
  putPpe({ require: req });
}

async function renderCams() {
  const cams = await api("/api/cameras");
  $("camRows").innerHTML =
    cams
      .map((c) => {
        const cr = c.crossing || {};
        const zone = cr.mode === "zone" && (cr.zone || []).length >= 3;
        const on = zone && (cr.tasks || []).includes("ppe");
        const note = !c.enabled ? "camera đang tắt" : zone ? `vùng ${cr.zone.length} điểm` : "chưa có vùng polygon";
        return `<div class="it row">
          <label class="check grow"><input type="checkbox" data-id="${esc(c.id)}" ${on ? "checked" : ""} ${zone ? "" : "disabled"} />
            <strong>${esc(c.name)}</strong> <span class="muted small">${esc(note)}</span></label>
          <a class="btn" href="/calibrate/${encodeURIComponent(c.id)}">${zone ? "Sửa vùng" : "Vẽ vùng"}</a>
        </div>`;
      })
      .join("") || `<span class="muted">Chưa có camera</span>`;
  $("camRows").querySelectorAll("input[data-id]").forEach((el) =>
    el.addEventListener("change", () => setCameraPpe(cams.find((c) => c.id === el.dataset.id), el.checked))
  );
}

async function setCameraPpe(cam, on) {
  const cr = cam.crossing || {};
  const tasks = (cr.tasks || []).filter((t) => t !== "ppe");
  if (on) tasks.push("ppe");
  try {
    await api(`/api/cameras/${encodeURIComponent(cam.id)}/crossing`, {
      method: "PUT",
      body: { mode: cr.mode, points: cr.points || [], inward_point: cr.inward_point, zone: cr.zone || [], tasks },
    });
    toast(on ? `Đã bật PPE cho ${cam.name}` : `Đã tắt PPE cho ${cam.name}`);
  } catch (e) {
    toast(e.message, true);
  }
  renderCams();
}

$("btnEnable").onclick = () => {
  putPpe({ enabled: !info.enabled });
};
$("reqHardhat").onchange = sendRequire;
$("reqGloves").onchange = sendRequire;
$("inferMissing").onchange = () => {
  syncControls();
  putPpe({ infer_missing: $("inferMissing").checked });
};
$("personMask").onchange = () => {
  syncControls();
  putPpe({ person_mask: $("personMask").checked });
};
$("mode").onchange = () => putPpe({ mode: $("mode").value });
$("imgsz").onchange = () => putPpe({ imgsz: Number($("imgsz").value) });
$("maskImgsz").onchange = () => putPpe({ mask_imgsz: Number($("maskImgsz").value) });
$("conf").addEventListener("input", () => {
  $("confVal").textContent = Number($("conf").value).toFixed(2);
  putPpe({ conf: Number($("conf").value) }, 400);
});
bindNumber("everyN", "every_n");
bindNumber("confirmRuns", "confirm_runs");

load().catch((e) => toast(e.message, true));
setInterval(() => {
  if (!info || Object.keys(pending).length) return;
  api("/api/ppe").then((r) => { info = r; renderHeader(); }).catch(() => {});
}, 3000);
