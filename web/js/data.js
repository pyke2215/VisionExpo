renderNav("data");

const $ = (id) => document.getElementById(id);
const PAGE = 50;
const EVENT_TYPES = { crossing: "Vào / ra", alert: "Cảnh báo", camera: "Camera", reset: "Reset đếm" };
const st = { labels: { vi: {}, alerts: new Set() }, cams: {}, tab: "tracks", tPage: 0, ePage: 0 };

// ---------- filters ----------
function toLocalInput(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

function setRange(fromDate, toDate) {
  $("fFrom").value = toLocalInput(fromDate);
  $("fTo").value = toDate ? toLocalInput(toDate) : "";
}

function filters() {
  const q = new URLSearchParams();
  if ($("fCamera").value) q.set("camera_id", $("fCamera").value);
  if ($("fFrom").value) q.set("from", new Date($("fFrom").value).getTime() / 1000);
  if ($("fTo").value) q.set("to", new Date($("fTo").value).getTime() / 1000);
  return q;
}

function withParams(path, extra = {}) {
  const q = filters();
  Object.entries(extra).forEach(([k, v]) => v !== "" && v !== null && v !== undefined && q.set(k, v));
  return `${path}?${q.toString()}`;
}

const camName = (id) => esc(st.cams[id] || id);
const itemLabel = (en) => `${esc(en)}${st.labels.vi[en] ? ` <span class="vi">${esc(st.labels.vi[en])}</span>` : ""}`;

// ---------- summary ----------
async function loadSummary() {
  const s = await api("/api/db");
  $("dbPath").textContent = s.path;
  const ev = s.events || {};
  const cards = [
    [fmtBytes(s.size_bytes), "Dung lượng"],
    [s.tracks, "Lượt người đã lưu"],
    [s.alert_tracks, "Lượt có cảnh báo"],
    [ev.crossing || 0, "Sự kiện vào / ra"],
    [ev.alert || 0, "Sự kiện cảnh báo"],
    [s.samples, "Mẫu số liệu"],
    [fmtDateTime(s.first_ts), "Bản ghi đầu tiên"],
    [s.error ? "Lỗi" : s.queue, s.error ? esc(s.error) : "Đang chờ ghi"],
  ];
  $("summaryCards").innerHTML = cards.map(([v, l]) => `<div class="card"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");
  const c = s.config || {};
  $("cEnabled").checked = c.enabled !== false;
  $("cTracks").checked = c.save_tracks !== false;
  $("cRestore").checked = c.restore_counts !== false;
  $("cSample").value = c.sample_every_s ?? 10;
  $("cRetention").value = c.retention_days ?? 30;
}

// ---------- chart ----------
async function loadHourly() {
  const rows = await api(withParams("/api/db/hourly"));
  const svg = $("chart");
  const W = 800, H = 220, PADL = 34, PADB = 22, PADT = 10;
  const tot = rows.reduce((a, r) => ({ in: a.in + r.in, out: a.out + r.out, alerts: a.alerts + r.alerts, tracks: a.tracks + r.tracks }), { in: 0, out: 0, alerts: 0, tracks: 0 });
  $("rangeCards").innerHTML = [
    [tot.in, "Vào"], [tot.out, "Ra"], [tot.tracks, "Lượt người"], [tot.alerts, "Cảnh báo"],
  ].map(([v, l]) => `<div class="card"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");
  if (!rows.length) {
    svg.innerHTML = `<text x="${W / 2}" y="${H / 2}" fill="#8b95a7" text-anchor="middle" font-size="14">Chưa có dữ liệu trong khoảng này</text>`;
    return;
  }
  const max = Math.max(1, ...rows.map((r) => Math.max(r.in, r.out, r.tracks)));
  const n = rows.length;
  const slot = (W - PADL) / n;
  const bw = Math.max(2, Math.min(18, slot / 3));
  const y = (v) => H - PADB - (v / max) * (H - PADB - PADT);
  let html = "";
  for (let i = 0; i <= 4; i++) {
    const v = Math.round((max * i) / 4);
    html += `<line x1="${PADL}" x2="${W}" y1="${y(v)}" y2="${y(v)}" stroke="#2a3242" stroke-width="1"/>
             <text x="${PADL - 6}" y="${y(v) + 4}" fill="#8b95a7" font-size="10" text-anchor="end">${v}</text>`;
  }
  const pts = [];
  rows.forEach((r, i) => {
    const cx = PADL + slot * i + slot / 2;
    html += `<rect x="${cx - bw}" y="${y(r.in)}" width="${bw}" height="${H - PADB - y(r.in)}" fill="#3ccf6e"><title>${r.in} vào</title></rect>`;
    html += `<rect x="${cx}" y="${y(r.out)}" width="${bw}" height="${H - PADB - y(r.out)}" fill="#ff9f2e"><title>${r.out} ra</title></rect>`;
    if (r.alerts) html += `<path d="M${cx - 5},${PADT + 10} L${cx + 5},${PADT + 10} L${cx},${PADT} Z" fill="#ff5a5f"><title>${r.alerts} cảnh báo</title></path>`;
    pts.push(`${cx},${y(r.tracks)}`);
    const d = new Date(r.hour_ts * 1000);
    const every = Math.ceil(n / 16);
    if (i % every === 0) {
      const label = n > 24 ? `${d.getDate()}/${d.getMonth() + 1} ${d.getHours()}h` : `${d.getHours()}h`;
      html += `<text x="${cx}" y="${H - 6}" fill="#8b95a7" font-size="10" text-anchor="middle">${label}</text>`;
    }
  });
  html += `<polyline points="${pts.join(" ")}" fill="none" stroke="#3fa7ff" stroke-width="2"/>`;
  pts.forEach((p) => {
    const [cx, cy] = p.split(",");
    html += `<circle cx="${cx}" cy="${cy}" r="3" fill="#3fa7ff"/>`;
  });
  svg.innerHTML = html;
}

async function loadTopItems() {
  const rows = await api(withParams("/api/db/top_items"));
  $("topItems").innerHTML = rows.length
    ? rows.map((r) => `<span class="chip on ${st.labels.alerts.has(r.label) ? "alert" : ""}" data-item="${esc(r.label)}">${itemLabel(r.label)} ×${r.tracks}</span>`).join("")
    : `<span class="muted small">Chưa có</span>`;
  $("topItems").querySelectorAll(".chip").forEach((el) => (el.onclick = () => {
    $("tItem").value = el.dataset.item;
    switchTab("tracks");
    st.tPage = 0;
    loadTracks();
  }));
  const current = $("tItem").value;
  $("tItem").innerHTML = `<option value="">bất kỳ</option>` + rows.map((r) => `<option value="${esc(r.label)}">${esc(r.label)}${st.labels.vi[r.label] ? " – " + esc(st.labels.vi[r.label]) : ""}</option>`).join("");
  $("tItem").value = current;
}

// ---------- tables ----------
async function loadTracks() {
  const r = await api(withParams("/api/db/tracks", {
    limit: PAGE, offset: st.tPage * PAGE, alert_only: $("tAlert").checked ? "true" : "", item: $("tItem").value,
  }));
  const side = (s) => (s ? `<span class="tag ${s}">${s === "in" ? "TRONG" : "NGOÀI"}</span>` : "");
  $("trackRows").innerHTML = r.rows.length
    ? r.rows.map((t) => `<tr>
        <td>${fmtDateTime(t.first_ts)}</td><td>${camName(t.camera_id)}</td><td>${t.track_id ?? ""}</td>
        <td>${fmtDuration(t.duration)}</td>
        <td>${side(t.first_side)}${t.first_side !== t.last_side ? " → " + side(t.last_side) : ""}</td>
        <td class="items"><div class="chips">${Object.entries(t.items)
          .map(([k, v]) => `<span class="chip ${st.labels.alerts.has(k) ? "alert" : ""}" title="${v} khung">${itemLabel(k)}</span>`)
          .join("") || '<span class="muted small">–</span>'}${t.alert ? '<span class="tag alert">VŨ KHÍ</span>' : ""}</div></td></tr>`).join("")
    : `<tr><td colspan="6" class="muted">Không có dữ liệu</td></tr>`;
  $("trackInfo").textContent = `${r.total ? st.tPage * PAGE + 1 : 0}–${Math.min(r.total, (st.tPage + 1) * PAGE)} / ${r.total}`;
  $("tPrev").disabled = st.tPage === 0;
  $("tNext").disabled = (st.tPage + 1) * PAGE >= r.total;
}

function eventDetail(e) {
  if (e.type === "crossing")
    return `<span class="tag ${e.direction}">${e.direction === "in" ? "VÀO" : "RA"}</span>` +
      (e.data?.mode === "zone" ? ` <span class="muted small">vùng${e.data.dwell_s != null ? ` · ở ${fmtDuration(e.data.dwell_s)}` : ""}</span>` : "");
  if (e.type === "alert") return `<span class="tag alert">${esc((e.data?.items || []).join(", "))}</span> <span class="muted small">${esc((e.data?.all_items || []).join(", "))}</span>`;
  if (e.type === "camera") return `${esc(e.data?.status || "")} <span class="muted small">${esc(e.data?.error || "")}</span>`;
  return "";
}

async function loadEvents() {
  const types = [...document.querySelectorAll("input[name=eType]:checked")].map((el) => el.value).join(",");
  const r = await api(withParams("/api/db/events", { limit: PAGE, offset: st.ePage * PAGE, types }));
  $("eventRows").innerHTML = r.rows.length
    ? r.rows.map((e) => `<tr><td>${fmtDateTime(e.ts)}</td><td>${camName(e.camera_id)}</td>
        <td>${esc(EVENT_TYPES[e.type] || e.type)}</td><td>${e.track_id ?? ""}</td><td>${eventDetail(e)}</td></tr>`).join("")
    : `<tr><td colspan="5" class="muted">Không có dữ liệu</td></tr>`;
  $("eventInfo").textContent = `${r.total ? st.ePage * PAGE + 1 : 0}–${Math.min(r.total, (st.ePage + 1) * PAGE)} / ${r.total}`;
  $("ePrev").disabled = st.ePage === 0;
  $("eNext").disabled = (st.ePage + 1) * PAGE >= r.total;
}

function switchTab(tab) {
  st.tab = tab;
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $("tabTracks").classList.toggle("hidden", tab !== "tracks");
  $("tabEvents").classList.toggle("hidden", tab !== "events");
}

function updateCsvLinks() {
  $("lnkEventsCsv").href = withParams("/api/db/export/events.csv");
  $("lnkTracksCsv").href = withParams("/api/db/export/tracks.csv");
}

async function applyAll() {
  st.tPage = 0;
  st.ePage = 0;
  updateCsvLinks();
  try {
    await Promise.all([loadSummary(), loadHourly(), loadTopItems(), loadTracks(), loadEvents()]);
  } catch (e) {
    toast(e.message, true);
  }
}

// ---------- wiring ----------
document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => switchTab(b.dataset.tab)));
$("btnApply").onclick = applyAll;
$("fCamera").onchange = applyAll;
$("btnToday").onclick = () => { const d = new Date(); d.setHours(0, 0, 0, 0); setRange(d, null); applyAll(); };
$("btn24h").onclick = () => { setRange(new Date(Date.now() - 86400000), null); applyAll(); };
$("btn7d").onclick = () => { const d = new Date(Date.now() - 6 * 86400000); d.setHours(0, 0, 0, 0); setRange(d, null); applyAll(); };
$("tAlert").onchange = () => { st.tPage = 0; loadTracks(); };
$("tItem").onchange = () => { st.tPage = 0; loadTracks(); };
$("tPrev").onclick = () => { st.tPage--; loadTracks(); };
$("tNext").onclick = () => { st.tPage++; loadTracks(); };
$("ePrev").onclick = () => { st.ePage--; loadEvents(); };
$("eNext").onclick = () => { st.ePage++; loadEvents(); };

$("btnSaveDb").onclick = async () => {
  try {
    await api("/api/db", {
      method: "PUT",
      body: {
        enabled: $("cEnabled").checked,
        save_tracks: $("cTracks").checked,
        restore_counts: $("cRestore").checked,
        sample_every_s: Number($("cSample").value),
        retention_days: Number($("cRetention").value),
      },
    });
    toast("Đã lưu cấu hình lưu trữ");
  } catch (e) {
    toast(e.message, true);
  }
};
$("btnClearDb").onclick = async () => {
  const ok = prompt('Xóa TOÀN BỘ dữ liệu đã lưu (sự kiện, lượt người, số liệu). Gõ DELETE để xác nhận:');
  if (ok !== "DELETE") return;
  try {
    await api("/api/db/clear", { method: "POST", body: { confirm: "DELETE" } });
    toast("Đã xóa dữ liệu");
    applyAll();
  } catch (e) {
    toast(e.message, true);
  }
};

(async function init() {
  $("eTypes").innerHTML = Object.entries(EVENT_TYPES)
    .map(([k, v]) => `<label class="check"><input type="checkbox" name="eType" value="${k}" ${k !== "camera" ? "checked" : ""}/> ${v}</label>`)
    .join("");
  document.querySelectorAll("input[name=eType]").forEach((el) => (el.onchange = () => { st.ePage = 0; loadEvents(); }));
  const [cams, labels] = await Promise.all([api("/api/cameras"), loadViLabels()]);
  st.labels = labels;
  cams.forEach((c) => (st.cams[c.id] = c.name));
  $("fCamera").innerHTML += cams.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("");
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  setRange(today, null);
  applyAll();
  setInterval(() => loadSummary().catch(() => {}), 5000);
})();
