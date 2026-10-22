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
    ["/world", "world", "Đồ vật"],
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
