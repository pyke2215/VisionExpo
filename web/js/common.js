// Shared helpers for all pages.

async function api(path, { method = "GET", body } = {}) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const msg = (data && data.detail) || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

let toastTimer = null;
function toast(msg, isError = false) {
  let el = document.querySelector(".toast");
  if (!el) {
    el = document.createElement("div");
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.toggle("err", isError);
  el.style.opacity = "1";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.style.opacity = "0"), 3500);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function maskSource(src) {
  return String(src || "").replace(/(\/\/[^:/@]+:)[^@]+@/, "$1****@");
}

function statusText(s) {
  return {
    running: "Đang chạy", online: "Online", connecting: "Đang kết nối", reconnecting: "Kết nối lại",
    offline: "Mất kết nối", error: "Lỗi", disabled: "Tắt", stopped: "Dừng", starting: "Khởi động",
    "loading model": "Tải model",
  }[s] || s || "?";
}

function dotClass(s) {
  return String(s || "").split(" ")[0];
}

function renderNav(active) {
  const links = [
    ["/", "live", "Live"],
    ["/cameras", "cameras", "Cameras"],
    ["/calibrate", "calibrate", "Line / Task"],
    ["/ppe", "ppe", "PPE"],
    ["/data", "data", "Dữ liệu"],
    ["/socket", "socket", "Socket"],
  ];
  const bar = document.createElement("header");
  bar.className = "topbar";
  bar.innerHTML = `
    <div class="brand">Vision<span>Expo</span></div>
    <nav class="nav">${links
      .map(([href, key, label]) => `<a href="${href}" class="${key === active ? "active" : ""}">${label}</a>`)
      .join("")}</nav>
    <div class="spacer"></div>
    <div class="meta" id="metaInfo"></div>`;
  document.body.prepend(bar);
}

function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString("vi-VN", { hour12: false });
}

function fmtDateTime(ts) {
  if (!ts) return "–";
  return new Date(ts * 1000).toLocaleString("vi-VN", { hour12: false });
}

function fmtDuration(s) {
  if (s === null || s === undefined) return "–";
  s = Math.round(Number(s));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

function fmtBytes(n) {
  n = Number(n) || 0;
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

// Heat grid {cols, rows, grid (row-major seconds), max} drawn over a w x h canvas: transparent where nobody stood,
// blue -> yellow -> red with density (square-root scale so a few busy spots do not hide the rest).
function drawHeat(canvas, heat, w, h) {
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, w, h);
  if (!heat || !heat.max || !heat.cols) return;
  const cw = w / heat.cols, ch = h / heat.rows;
  for (let r = 0; r < heat.rows; r++) {
    for (let c = 0; c < heat.cols; c++) {
      const v = heat.grid[r * heat.cols + c];
      if (!v) continue;
      const t = Math.sqrt(v / heat.max);
      const hue = 240 - 240 * t; // 240 blue .. 0 red
      ctx.fillStyle = `hsla(${hue}, 100%, 50%, ${0.25 + 0.5 * t})`;
      ctx.fillRect(c * cw, r * ch, Math.ceil(cw), Math.ceil(ch));
    }
  }
}

function localMidnight() {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d.getTime() / 1000;
}
