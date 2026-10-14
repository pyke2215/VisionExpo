renderNav("world");

const $ = (id) => document.getElementById(id);
let info = null;
let disabled = new Set();
let saveTimer = null;

async function load() {
  info = await api("/api/world");
  disabled = new Set((info.config.disabled_classes || []).map((s) => s.toLowerCase()));
  renderHeader(true);
  renderCats();
  renderCams();
}

function renderHeader(withControls = false) {
  const cfg = info.config;
  $("btnEnable").textContent = cfg.enabled ? "Đang BẬT – bấm để tắt" : "Đang TẮT – bấm để bật";
  $("btnEnable").classList.toggle("on", !!cfg.enabled);
  const status = cfg.enabled ? info.status : "off";
  $("wStatus").textContent = { ready: "Sẵn sàng", loading: "Đang tải model", building: "Đang build từ vựng", error: "Lỗi", idle: "Chưa tải", off: "Tắt" }[status] || status;
  $("wStatus").className = `badge ${status}`;
  $("wInfo").textContent =
    `${info.vocabulary_classes} từ trong từ vựng · ${info.vocabulary_classes - disabled.size} đang bật` +
    (info.model_classes ? ` · model đã nạp ${info.model_classes} lớp` : "") +
    (info.last_infer_ms ? ` · ${info.last_infer_ms} ms/lần` : "") +
    (info.error ? ` · ${info.error}` : "");
  if (!withControls) return;
  $("conf").value = cfg.conf;
  $("confVal").textContent = Number(cfg.conf).toFixed(2);
  $("alertConf").value = cfg.alert_conf ?? cfg.conf;
  $("alertConfVal").textContent = Number(cfg.alert_conf ?? cfg.conf).toFixed(2);
  $("showConf").value = cfg.show_conf ?? 0;
  $("showConfVal").textContent = Number(cfg.show_conf ?? 0).toFixed(2);
  $("confirmHits").value = cfg.confirm_hits ?? 2;
  $("alertHits").value = cfg.alert_hits ?? 3;
  $("dropMisses").value = cfg.drop_misses ?? 3;
  $("rejectStatic").checked = cfg.reject_static !== false;
  $("minObjPx").value = cfg.min_obj_px ?? 14;
  $("labelMargin").value = cfg.label_margin ?? 1.5;
  $("everyN").value = cfg.every_n;
  $("imgsz").value = cfg.imgsz;
  $("attach").checked = cfg.attach_to_person !== false;
  $("scope").value = cfg.scope || "person";
  $("cropMargin").value = Math.round((cfg.crop_margin ?? 0.15) * 100);
  $("maxPersons").value = cfg.max_persons ?? 8;
  syncScope();
}

function syncScope() {
  const perPerson = $("scope").value === "person";
  $("marginWrap").classList.toggle("hidden", !perPerson);
  $("maxPersonsWrap").classList.toggle("hidden", !perPerson);
  $("smoothRow").classList.toggle("hidden", !perPerson);
  $("smoothHint").classList.toggle("hidden", !perPerson);
  $("attachWrap").classList.toggle("hidden", perPerson);
}

function catConf(id) {
  const v = (info.config.category_conf || {})[id];
  return v === null || v === undefined ? null : v;
}

function defaultConf(cat) {
  const cfg = info.config;
  return Number(cat.alert ? cfg.alert_conf ?? cfg.conf : cfg.conf);
}

function renderCats() {
  const q = $("search").value.trim().toLowerCase();
  const cats = info.vocabulary.categories || [];
  let total = 0;
  $("cats").innerHTML = cats
    .map((cat, ci) => {
      const items = cat.items.filter((it) => !q || it.en.toLowerCase().includes(q) || (it.vi || "").toLowerCase().includes(q));
      if (!items.length) return "";
      total += items.length;
      const onCount = cat.items.filter((it) => !disabled.has(it.en.toLowerCase())).length;
      const custom = cat.id === "custom";
      return `<div class="panel" style="background:var(--panel-2)${cat.suppress ? "; opacity:.7" : ""}">
        <div class="row" style="margin-bottom:8px">
          <label class="check grow"><input type="checkbox" data-cat="${ci}" ${onCount === cat.items.length ? "checked" : ""} />
            <strong style="${cat.alert ? "color:var(--red)" : ""}">${esc(cat.label || cat.id)}</strong></label>
          ${cat.suppress ? "" : `<label class="small muted" title="Để trống = dùng ngưỡng chung">ngưỡng
            <input type="number" data-catconf="${esc(cat.id)}" min="0.05" max="0.95" step="0.05" style="width:70px"
              value="${catConf(cat.id) ?? ""}" placeholder="${defaultConf(cat).toFixed(2)}" /></label>`}
          <span class="muted small">${onCount}/${cat.items.length}</span>
        </div>
        <div class="chips">${items
          .map((it) => {
            const on = !disabled.has(it.en.toLowerCase());
            return `<span class="chip ${on ? "on" : ""}" data-en="${esc(it.en)}" title="${esc(it.en)}">${esc(it.en)}<span class="vi">${esc(it.vi || "")}</span>${
              custom ? `<span class="x" data-del="${esc(it.en)}">×</span>` : ""
            }</span>`;
          })
          .join("")}</div></div>`;
    })
    .join("");
  $("vocabCount").textContent = q ? `(${total} kết quả)` : "";

  $("cats").querySelectorAll(".chip").forEach((el) =>
    el.addEventListener("click", (e) => {
      if (e.target.dataset.del) return removeWord(e.target.dataset.del);
      const en = el.dataset.en.toLowerCase();
      disabled.has(en) ? disabled.delete(en) : disabled.add(en);
      renderCats();
      queueSave();
    })
  );
  $("cats").querySelectorAll("input[data-catconf]").forEach((el) =>
    ["input", "change"].forEach((type) =>
      el.addEventListener(type, () => {
        const v = el.value.trim();
        if (v !== "" && Number.isNaN(Number(v))) return;
        putWorld({ category_conf: { [el.dataset.catconf]: v === "" ? null : Number(v) } }, type === "input" ? 600 : 0);
      })
    )
  );
  $("cats").querySelectorAll("input[data-cat]").forEach((el) =>
    el.addEventListener("change", () => {
      const cat = cats[+el.dataset.cat];
      cat.items.forEach((it) => (el.checked ? disabled.delete(it.en.toLowerCase()) : disabled.add(it.en.toLowerCase())));
      renderCats();
      queueSave();
    })
  );
}

async function renderCams() {
  const cams = await api("/api/cameras");
  $("camToggles").innerHTML = cams
    .map((c) => `<label class="check"><input type="checkbox" data-id="${esc(c.id)}" ${c.world_enabled ? "checked" : ""} ${c.enabled ? "" : "disabled"} /> ${esc(c.name)}</label>`)
    .join("") || `<span class="muted">Chưa có camera</span>`;
  $("camToggles").querySelectorAll("input").forEach((el) =>
    el.addEventListener("change", async () => {
      try {
        await api(`/api/cameras/${encodeURIComponent(el.dataset.id)}`, { method: "PUT", body: { world_enabled: el.checked } });
        toast("Đã cập nhật camera");
      } catch (e) { toast(e.message, true); }
    })
  );
}

let pending = {};

function putWorld(patch, delay = 0) {
  for (const [k, v] of Object.entries(patch)) {
    pending[k] = k === "category_conf" ? { ...pending[k], ...v } : v;
  }
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushWorld, delay);
}

async function flushWorld() {
  clearTimeout(saveTimer);
  const patch = pending;
  pending = {};
  if (!Object.keys(patch).length) return;
  try {
    const r = await api("/api/world", { method: "PUT", body: patch });
    info = { ...info, ...r };
    renderHeader();
    const ignored = Object.keys(patch).filter((k) => !(k in r.config));
    if (ignored.length) toast(`Server chưa hỗ trợ ${ignored.join(", ")} – hãy khởi động lại app`, true);
    else toast("Đã lưu");
  } catch (e) {
    toast(e.message, true);
  }
}

// Leaving the page must not drop a change that is still waiting for its debounce.
window.addEventListener("pagehide", () => {
  if (!Object.keys(pending).length) return;
  fetch("/api/world", { method: "PUT", keepalive: true, headers: { "Content-Type": "application/json" }, body: JSON.stringify(pending) });
  pending = {};
});

function queueSave() {
  putWorld({ disabled_classes: [...disabled] }, 400);
  renderHeader();
}

function bindNumber(id, key, scale = 1) {
  const send = (delay) => {
    const v = $(id).value.trim();
    if (v !== "" && !Number.isNaN(Number(v))) putWorld({ [key]: Number(v) * scale }, delay);
  };
  $(id).addEventListener("input", () => send(600));
  $(id).addEventListener("change", () => send(0));
}

async function saveVocabulary(categories, msg) {
  try {
    await api("/api/world/vocabulary", { method: "PUT", body: { categories } });
    toast(msg);
    await load();
  } catch (e) {
    toast(e.message, true);
  }
}

function removeWord(en) {
  const cats = structuredClone(info.vocabulary.categories);
  const custom = cats.find((c) => c.id === "custom");
  if (!custom || !confirm(`Xóa "${en}" khỏi từ vựng?`)) return;
  custom.items = custom.items.filter((it) => it.en !== en);
  saveVocabulary(cats.filter((c) => c.items.length), "Đang build lại từ vựng…");
}

$("btnAddWords").onclick = () => {
  const lines = $("newWords").value.split("\n").map((l) => l.trim()).filter(Boolean);
  if (!lines.length) return toast("Nhập ít nhất một từ", true);
  const cats = structuredClone(info.vocabulary.categories);
  let custom = cats.find((c) => c.id === "custom");
  if (!custom) cats.push((custom = { id: "custom", label: "Tự thêm", items: [] }));
  const existing = new Set(cats.flatMap((c) => c.items.map((it) => it.en.toLowerCase())));
  let added = 0;
  for (const line of lines) {
    const [en, vi] = line.split("|").map((s) => (s || "").trim());
    if (!en || existing.has(en.toLowerCase())) continue;
    custom.items.push({ en: en.toLowerCase(), vi: vi || en });
    existing.add(en.toLowerCase());
    added++;
  }
  if (!added) return toast("Các từ này đã có trong từ vựng", true);
  $("newWords").value = "";
  saveVocabulary(cats, `Đã thêm ${added} từ, đang build lại (vài giây)…`);
};

$("btnEnable").onclick = () => putWorld({ enabled: !info.config.enabled });
$("conf").oninput = () => ($("confVal").textContent = Number($("conf").value).toFixed(2));
$("conf").onchange = () => putWorld({ conf: Number($("conf").value) });
$("alertConf").oninput = () => ($("alertConfVal").textContent = Number($("alertConf").value).toFixed(2));
$("alertConf").onchange = () => putWorld({ alert_conf: Number($("alertConf").value) });
$("showConf").oninput = () => ($("showConfVal").textContent = Number($("showConf").value).toFixed(2));
$("showConf").onchange = () => putWorld({ show_conf: Number($("showConf").value) });
bindNumber("confirmHits", "confirm_hits");
bindNumber("alertHits", "alert_hits");
bindNumber("dropMisses", "drop_misses");
$("rejectStatic").onchange = () => putWorld({ reject_static: $("rejectStatic").checked });
bindNumber("minObjPx", "min_obj_px");
bindNumber("labelMargin", "label_margin");
bindNumber("everyN", "every_n");
$("imgsz").onchange = () => putWorld({ imgsz: Number($("imgsz").value) });
$("attach").onchange = () => putWorld({ attach_to_person: $("attach").checked });
$("scope").onchange = () => { syncScope(); putWorld({ scope: $("scope").value }); };
bindNumber("cropMargin", "crop_margin", 0.01);
bindNumber("maxPersons", "max_persons");
$("search").oninput = renderCats;
$("btnAllOn").onclick = () => { disabled.clear(); renderCats(); queueSave(); };
$("btnAllOff").onclick = () => {
  (info.vocabulary.categories || []).forEach((c) => c.items.forEach((it) => disabled.add(it.en.toLowerCase())));
  renderCats();
  queueSave();
};

load();
setInterval(async () => {
  try {
    const s = await api("/api/status");
    const w = s.world;
    info = { ...info, status: w.status, error: w.error, model_classes: w.model_classes, last_infer_ms: w.last_infer_ms, vocabulary_classes: w.vocabulary_classes };
    renderHeader();
    $("metaInfo").textContent = `device ${s.device}`;
  } catch { /* ignore */ }
}, 2000);
