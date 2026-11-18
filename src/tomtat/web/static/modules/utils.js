// Tiện ích DOM, HTTP API, Toast và định dạng chuỗi

export const $ = (id) => document.getElementById(id);

export function setText(id, val) {
  const el = $(id);
  if (el) el.textContent = val;
}

export function showToast(msg, isErr = false) {
  const t = $("toast");
  if (!t) return;
  t.textContent = msg;
  t.style.background = isErr ? "#b91c1c" : "#0f172a";
  t.classList.remove("hidden");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add("hidden"), 3200);
}

export async function api(url, opts = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok && !data.need_confirm) {
    throw new Error(data.error || `Lỗi HTTP ${res.status}`);
  }
  return data;
}

export function escapeHtml(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

export function todayLabel() {
  const now = new Date();
  const dd = String(now.getDate()).padStart(2, "0");
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  return { slash: `${dd}/${mm}`, file: `${dd}-${mm}.zip` };
}
