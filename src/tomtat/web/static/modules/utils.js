// Tiện ích DOM, HTTP API, Toast và định dạng chuỗi

export const $ = (id) => document.getElementById(id);

export function setText(id, val) {
  const el = $(id);
  if (el) el.textContent = val;
}

export function showToast(msg, tone = false) {
  const t = $("toast");
  if (!t) return;
  t.textContent = msg;
  const isWarn = tone === "warn";
  const isErr = tone === true || tone === "error";
  t.style.background = isErr ? "#b91c1c" : isWarn ? "#b45309" : "#0f172a";
  t.classList.remove("hidden");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add("hidden"), isWarn ? 5500 : 3200);
}

export async function api(url, opts = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok && !data.need_confirm) {
    const err = new Error(data.error || `Lỗi HTTP ${res.status}`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

export function escapeHtml(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

export function withPreservedScroll(el, fn, opts = {}) {
  if (!el) return;
  const reset = Boolean(opts.reset);
  const prevTop = reset ? 0 : el.scrollTop;
  const prevLeft = reset ? 0 : el.scrollLeft;
  fn();
  el.scrollTop = prevTop;
  el.scrollLeft = prevLeft;
}
