renderNav("cameras");

const $ = (id) => document.getElementById(id);
let cameras = [];
let statusById = {};
let editing = null;

const PLACEHOLDERS = {
  rtsp: "rtsp://user:pass@192.168.1.10:554/Streaming/Channels/101",
  webcam: "0",
  file: "D:\\videos\\demo.mp4",
};

async function load() {
  const [cams, st] = await Promise.all([api("/api/cameras"), api("/api/status")]);
  cameras = cams;
  statusById = Object.fromEntries(st.cameras.map((c) => [c.id, c]));
  $("metaInfo").textContent = `device ${st.device}`;
  render();
}

function render() {
  $("camRows").innerHTML = cameras.length
    ? cameras
        .map((c) => {
          const s = statusById[c.id] || {};
          const cr = s.crossing || {};
          return `<tr>
          <td><span class="row"><span class="dot ${dotClass(s.status)}"></span>${esc(statusText(s.status))}</span>
              ${s.error ? `<div class="small" style="color:var(--red)">${esc(s.error)}</div>` : ""}</td>
          <td><strong>${esc(c.name)}</strong><div class="muted small mono">${esc(c.id)}</div></td>
          <td class="mono small">${esc(maskSource(c.source))}</td>
          <td>${s.fps ?? "–"}</td>
          <td>${s.persons ?? "–"}</td>
          <td>${cr.enabled ? `${cr.entries} / ${cr.exits}` : `<span class="muted small">chưa có line / vùng</span>`}</td>
          <td><div class="actions">
            <button data-act="toggle" data-id="${esc(c.id)}" class="${c.enabled ? "on" : ""}">${c.enabled ? "Đang bật" : "Đang tắt"}</button>
            <button data-act="edit" data-id="${esc(c.id)}">Sửa</button>
            <a class="btn" href="/calibrate/${encodeURIComponent(c.id)}">Line</a>
            <button data-act="restart" data-id="${esc(c.id)}">Khởi động lại</button>
            <button data-act="delete" data-id="${esc(c.id)}" class="danger">Xóa</button>
          </div></td></tr>`;
        })
        .join("")
    : `<tr><td colspan="7" class="muted">Chưa có camera nào.</td></tr>`;
  $("camRows").querySelectorAll("button[data-act]").forEach((b) => (b.onclick = () => action(b.dataset.act, b.dataset.id)));
}

async function action(act, id) {
  const cam = cameras.find((c) => c.id === id);
  try {
    if (act === "toggle") {
      await api(`/api/cameras/${encodeURIComponent(id)}`, { method: "PUT", body: { enabled: !cam.enabled } });
      toast(cam.enabled ? "Đã tắt camera" : "Đã bật camera");
    } else if (act === "edit") {
      openEdit(cam);
      return;
    } else if (act === "restart") {
      await api(`/api/cameras/${encodeURIComponent(id)}/restart`, { method: "POST" });
      toast("Đang khởi động lại");
    } else if (act === "delete") {
      if (!confirm(`Xóa camera "${cam.name}"?`)) return;
      await api(`/api/cameras/${encodeURIComponent(id)}`, { method: "DELETE" });
      toast("Đã xóa");
    }
    await load();
  } catch (e) {
    toast(e.message, true);
  }
}

function previewHtml(r) {
  if (!r.ok) return `<div style="color:var(--red)">Không kết nối được: ${esc(r.error)}</div>`;
  return `<div class="col"><span style="color:var(--green)">Kết nối OK – ${r.width}×${r.height}</span>
          <img class="preview" src="${r.image}" style="max-width:480px" /></div>`;
}

async function testSource(source, target, btn) {
  if (!source) return toast("Nhập nguồn trước", true);
  btn.disabled = true;
  target.innerHTML = `<span class="muted">Đang kết nối… (tối đa ~10 giây)</span>`;
  try {
    target.innerHTML = previewHtml(await api("/api/cameras/test", { method: "POST", body: { source } }));
  } catch (e) {
    target.innerHTML = `<div style="color:var(--red)">${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}

$("addKind").onchange = () => {
  $("addSource").placeholder = PLACEHOLDERS[$("addKind").value];
  if ($("addKind").value === "webcam" && !$("addSource").value) $("addSource").value = "0";
};
$("btnTest").onclick = () => testSource($("addSource").value.trim(), $("testResult"), $("btnTest"));
$("btnAdd").onclick = async () => {
  const source = $("addSource").value.trim();
  const name = $("addName").value.trim() || `Camera ${cameras.length + 1}`;
  if (!source) return toast("Nhập nguồn camera", true);
  try {
    const cam = await api("/api/cameras", { method: "POST", body: { name, source } });
    toast(`Đã thêm ${cam.name}. Vẽ line để đếm người qua lại.`);
    $("addName").value = "";
    $("addSource").value = "";
    $("testResult").innerHTML = "";
    await load();
  } catch (e) {
    toast(e.message, true);
  }
};
$("btnRefresh").onclick = load;

// ---------- edit dialog ----------
const form = $("editForm");
const NUM_FIELDS = ["conf", "process_fps", "stream_width", "jpeg_quality", "imgsz"];
const BOOL_FIELDS = ["enabled", "detect_enabled", "show_boxes", "show_skeleton"];

const F = (name) => form.elements.namedItem(name);

function openEdit(cam) {
  editing = cam;
  $("editTitle").textContent = `Sửa: ${cam.name}`;
  $("editId").textContent = cam.id;
  F("name").value = cam.name;
  F("source").value = cam.source;
  NUM_FIELDS.forEach((f) => (F(f).value = cam[f]));
  BOOL_FIELDS.forEach((f) => (F(f).checked = !!cam[f]));
  F("crossing_enabled").checked = !!(cam.crossing || {}).enabled;
  $("editTestResult").innerHTML = "";
  $("editDlg").showModal();
}

$("editTest").onclick = () => testSource(F("source").value.trim(), $("editTestResult"), $("editTest"));
$("editSave").onclick = async () => {
  const patch = { name: F("name").value.trim(), source: F("source").value.trim() };
  NUM_FIELDS.forEach((f) => (patch[f] = Number(F(f).value)));
  BOOL_FIELDS.forEach((f) => (patch[f] = F(f).checked));
  patch.crossing = { ...(editing.crossing || {}), enabled: F("crossing_enabled").checked };
  try {
    await api(`/api/cameras/${encodeURIComponent(editing.id)}`, { method: "PUT", body: patch });
    $("editDlg").close();
    toast("Đã lưu cấu hình");
    await load();
  } catch (e) {
    toast(e.message, true);
  }
};

load();
setInterval(() => { if (!$("editDlg").open) load().catch(() => {}); }, 3000);
