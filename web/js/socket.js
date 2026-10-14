renderNav("socket");

const $ = (id) => document.getElementById(id);
const TOPIC_LABELS = { status: "status", people: "people", events: "events", tracks: "tracks" };
const MAX_LOG = 300;

let info = null;
const mon = { ws: null, paused: false, count: 0, lastCount: 0, lastTs: Date.now() };

function wsBase() {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
}

function buildUrl(topics, camera, token) {
  const q = new URLSearchParams();
  if (topics.length) q.set("topics", topics.join(","));
  if (camera) q.set("cameras", camera);
  if (token) q.set("token", token);
  const s = q.toString();
  return `${wsBase()}${s ? "?" + s.replace(/%2C/g, ",") : ""}`;
}

function topicCheckboxes(container, name, selected) {
  container.innerHTML = Object.keys(TOPIC_LABELS)
    .map((t) => `<label class="check"><input type="checkbox" name="${name}" value="${t}" ${selected.includes(t) ? "checked" : ""}/> ${t}</label>`)
    .join("");
}

const checked = (name) => [...document.querySelectorAll(`input[name=${name}]:checked`)].map((el) => el.value);

function renderConfig() {
  const c = info.config;
  $("cfgEnabled").checked = !!c.enabled;
  $("cfgStatusHz").value = c.status_hz;
  $("cfgPeopleHz").value = c.people_hz;
  $("cfgToken").value = c.token || "";
  topicCheckboxes($("cfgTopics"), "cfgTopic", c.default_topics || []);
  topicCheckboxes($("monTopics"), "monTopic", ["status", "events", "tracks"]);
  document.querySelectorAll("input[name=cfgTopic]").forEach((el) => (el.onchange = renderUrl));
  renderUrl();
}

function renderUrl() {
  const topics = checked("cfgTopic");
  const token = $("cfgToken").value.trim();
  $("wsUrl").textContent = buildUrl(topics, "", token);
  const url = buildUrl(topics, "", token);
  $("sampleJs").textContent = `const ws = new WebSocket("${url}");
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.type === "crossing") console.log(msg.camera_id, "#" + msg.track_id, msg.direction);
  if (msg.type === "alert") console.warn("ALERT", msg.camera_id, msg.items);
  if (msg.type === "status") console.log("people now:", msg.totals.persons);
};
ws.onclose = () => setTimeout(() => location.reload(), 3000); // tự kết nối lại`;
  $("samplePy").textContent = `import asyncio, json, websockets

async def main():
    url = "${url}"
    while True:
        try:
            async with websockets.connect(url) as ws:
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg["type"] == "crossing":
                        print(msg["camera_id"], msg["track_id"], msg["direction"])
                    elif msg["type"] == "alert":
                        print("ALERT", msg["camera_id"], msg["items"])
        except Exception as exc:
            print("reconnect in 3s:", exc)
            await asyncio.sleep(3)

asyncio.run(main())`;
}

function renderInfo() {
  const c = info.config;
  $("wsState").textContent = c.enabled ? "Đang bật" : "Đang tắt";
  $("wsState").className = `badge ${c.enabled ? "ready" : "error"}`;
  $("clientCount").textContent = `(${info.clients.length})`;
  $("clientRows").innerHTML = info.clients.length
    ? info.clients
        .map(
          (k) => `<tr><td>${k.id}</td><td class="mono small">${esc(k.addr)}</td><td>${esc(k.topics.join(", "))}</td>
          <td>${esc(k.cameras ? k.cameras.join(", ") : "tất cả")}</td><td>${fmtDuration(Date.now() / 1000 - k.connected_at)}</td>
          <td>${k.sent}</td><td>${k.received}</td><td class="muted small">${esc(k.agent)}</td></tr>`
        )
        .join("")
    : `<tr><td colspan="8" class="muted">Chưa có client nào</td></tr>`;
  $("sentCards").innerHTML =
    Object.entries(info.sent_by_topic)
      .map(([t, n]) => `<div class="card"><div class="v">${n}</div><div class="l">${t}</div></div>`)
      .join("") + `<div class="card"><div class="v">${info.dropped_events}</div><div class="l">sự kiện bị bỏ</div></div>`;
}

async function refresh(first = false) {
  try {
    info = await api("/api/ws");
    if (first) renderConfig();
    renderInfo();
  } catch (e) {
    $("metaInfo").textContent = "Mất kết nối server";
  }
}

$("btnSaveCfg").onclick = async () => {
  try {
    info = await api("/api/ws", {
      method: "PUT",
      body: {
        enabled: $("cfgEnabled").checked,
        status_hz: Number($("cfgStatusHz").value),
        people_hz: Number($("cfgPeopleHz").value),
        default_topics: checked("cfgTopic"),
        token: $("cfgToken").value.trim(),
      },
    });
    renderInfo();
    renderUrl();
    toast("Đã lưu cấu hình WebSocket (áp dụng ngay)");
  } catch (e) {
    toast(e.message, true);
  }
};
$("btnGenToken").onclick = () => {
  const a = new Uint8Array(12);
  crypto.getRandomValues(a);
  $("cfgToken").value = [...a].map((b) => b.toString(16).padStart(2, "0")).join("");
  renderUrl();
};
$("cfgToken").oninput = renderUrl;
$("btnCopyUrl").onclick = async () => {
  try {
    await navigator.clipboard.writeText($("wsUrl").textContent);
    toast("Đã copy");
  } catch {
    toast("Trình duyệt không cho copy, hãy bôi đen và copy tay", true);
  }
};

// ---------- monitor ----------
function logLine(msg, raw) {
  if (mon.paused) return;
  const el = document.createElement("div");
  el.className = "m";
  const type = msg.type || "?";
  el.innerHTML = `<span class="t">${new Date().toLocaleTimeString("vi-VN", { hour12: false })}</span><span class="k k-${esc(type)}">${esc(type)}</span>${esc(raw)}`;
  el.onclick = () => {
    el.classList.toggle("open");
    el.lastChild.textContent = el.classList.contains("open") ? JSON.stringify(msg, null, 2) : raw;
  };
  const log = $("log");
  log.prepend(el);
  while (log.children.length > MAX_LOG) log.lastChild.remove();
}

function setConnected(on) {
  $("btnConnect").textContent = on ? "Ngắt kết nối" : "Kết nối";
  $("btnConnect").classList.toggle("primary", !on);
}

$("btnConnect").onclick = () => {
  if (mon.ws) {
    mon.ws.close();
    return;
  }
  const url = buildUrl(checked("monTopic"), $("monCamera").value, (info.config.token || "").trim());
  const ws = new WebSocket(url);
  mon.ws = ws;
  ws.onopen = () => { setConnected(true); toast("Đã kết nối " + url); };
  ws.onmessage = (e) => {
    mon.count++;
    let msg = {};
    try { msg = JSON.parse(e.data); } catch { /* keep raw */ }
    logLine(msg, e.data);
  };
  ws.onclose = (e) => {
    mon.ws = null;
    setConnected(false);
    if (e.code !== 1000 && e.code !== 1005) toast(`Socket đóng (${e.code}) ${e.reason || ""}`, e.code !== 1001);
  };
};
$("btnPause").onclick = () => {
  mon.paused = !mon.paused;
  $("btnPause").classList.toggle("on", mon.paused);
  $("btnPause").textContent = mon.paused ? "Tiếp tục" : "Tạm dừng";
};
$("btnClear").onclick = () => ($("log").innerHTML = "");

setInterval(() => {
  const now = Date.now();
  const rate = ((mon.count - mon.lastCount) * 1000) / (now - mon.lastTs);
  mon.lastCount = mon.count;
  mon.lastTs = now;
  $("monRate").textContent = mon.ws ? `${rate.toFixed(1)} msg/s · tổng ${mon.count}` : "";
}, 1000);

(async function init() {
  await refresh(true);
  const cams = await api("/api/cameras");
  $("monCamera").innerHTML += cams.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("");
  setInterval(refresh, 2000);
})();
