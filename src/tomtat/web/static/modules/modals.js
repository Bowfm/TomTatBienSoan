// Hộp thoại xem trước bảng .docx, xem nhật ký Gemini (Logs) và tự động kiểm tra cập nhật GitHub
import { store } from "./state.js";
import { $, api, escapeHtml, showToast } from "./utils.js";

export function openPreviewModal() {
  if (!store.CURRENT_UNITS) return;
  if ($("preview-modal-title")) $("preview-modal-title").textContent = "Xem trước bảng .docx cuối cùng";
  const body = $("preview-modal-body");
  if (body) body.innerHTML = `<table class="docx-preview-table" id="preview-table"></table>`;
  const tbl = $("preview-table");
  tbl.innerHTML = "";
  const addRow = (tag, text, yellow = false) => {
    const tr = document.createElement("tr");
    if (yellow) tr.className = "row-yellow";
    tr.innerHTML = `<td><div class="row-tag">${escapeHtml(tag)}</div><div>${escapeHtml(text || "")}</div></td>`;
    tbl.appendChild(tr);
  };
  addRow("Hàng 1 — Query", store.CURRENT_UNITS.query || "");
  for (const d of store.CURRENT_UNITS.docs || []) {
    const txt = d.units.map((u) => u.text).join(" ");
    addRow(`Hàng ${d.row + 1} — Văn bản gốc ${d.n} (${d.words} từ)`, txt);
  }
  addRow("Gợi ý trích chọn của AI", store.CURRENT_UNITS.ai_extract || "");
  addRow("Gợi ý biên soạn của AI", store.CURRENT_UNITS.ai_abstract || "");
  addRow("Hàng điền 1 — Phong cách chức năng", $("sel-style").value, true);
  addRow("Hàng điền 2 — Bản trích chọn", $("txt-extract").value, true);
  addRow("Hàng điền 3 — Bản biên soạn", $("txt-abstract").value, true);
  $("preview-modal").classList.remove("hidden");
}

export async function openLogsModal() {
  if (!store.CURRENT_STEM) return;
  try {
    const data = await api(`/api/file/${encodeURIComponent(store.CURRENT_STEM)}/logs`);
    if ($("preview-modal-title")) {
      $("preview-modal-title").textContent = `Nhật ký gọi Gemini (Logs) — ${store.CURRENT_STEM}.docx`;
    }
    const body = $("preview-modal-body");
    const logs = data.logs || [];
    if (!logs.length) {
      body.innerHTML = `<div class="muted">Chưa có log gọi Gemini nào cho file này (Thư mục: ${escapeHtml(data.log_dir || "")}).</div>`;
    } else {
      body.innerHTML =
        `<div class="muted" style="margin-bottom:8px">Thư mục trên ổ đĩa: <code>${escapeHtml(data.log_dir || "")}</code></div>` +
        logs
          .map((lg) => {
            const u = lg.usage || {};
            const tok = u.total_tokens ? `${u.total_tokens} tokens (in:${u.total_input_tokens || 0}, out:${u.total_output_tokens || 0}, think:${u.total_thought_tokens || 0})` : "";
            const stBadge = lg.error
              ? `<span style="color:#b91c1c;font-weight:700">LỖI: ${escapeHtml(lg.error)}</span>`
              : `<span style="color:#047857;font-weight:700">✓ Thành công</span>`;
            return `
              <details style="border:1px solid #cbd5e1;border-radius:6px;padding:8px 10px;margin-bottom:8px;background:#f8fafc">
                <summary style="cursor:pointer;font-size:13px">
                  <strong>[${escapeHtml(lg.started_at)}]</strong> Bước: <strong>${escapeHtml(lg.step)}</strong> (lần ${lg.attempt}, retry ${lg.net_try})
                  · Model: <strong style="color:#1d4ed8">${escapeHtml(lg.model)}</strong>
                  · Thời gian: <strong>${lg.duration_s}s</strong>
                  ${tok ? `· ${tok}` : ""} — ${stBadge}
                </summary>
                <div style="margin-top:8px">
                  <div style="font-weight:600;margin-bottom:4px">Phản hồi của Gemini:</div>
                  <pre class="ref-pre" style="max-height:220px;overflow-y:auto;background:#fff;border:1px solid #e2e8f0">${escapeHtml(lg.response_text || lg.error || "(trống)")}</pre>
                </div>
              </details>
            `;
          })
          .join("");
    }
    $("preview-modal").classList.remove("hidden");
  } catch (err) {
    showToast(err.message, true);
  }
}

export async function initUpdateChecker() {
  const btn = $("btn-self-update");
  const banner = $("update-notification-banner");
  const bannerMsg = $("update-banner-msg");
  const btnBannerUpdate = $("btn-banner-update");
  const btnBannerDismiss = $("btn-banner-dismiss");

  const doUpdate = async () => {
    showToast("Đang tải bản cập nhật mới nhất từ GitHub...");
    if (btn) btn.disabled = true;
    if (btnBannerUpdate) btnBannerUpdate.disabled = true;
    try {
      await api("/api/self-update", { method: "POST", body: "{}" });
      showToast("Đã cập nhật xong! Đang tải lại giao diện...");
      setTimeout(() => window.location.reload(), 900);
    } catch (err) {
      showToast(err.message, true);
      if (btn) btn.disabled = false;
      if (btnBannerUpdate) btnBannerUpdate.disabled = false;
    }
  };

  if (btn) {
    btn.addEventListener("click", async () => {
      if (btn.classList.contains("btn-update-available")) {
        await doUpdate();
        return;
      }
      showToast("Đang kiểm tra phiên bản trên GitHub...");
      try {
        const info = await api("/api/check-update");
        if (info.has_update) {
          btn.classList.add("btn-update-available");
          btn.textContent = "🔔 Có update mới!";
          if (banner && bannerMsg) {
            bannerMsg.textContent = `(Bản mới: ${info.latest_commit}${info.current_commit ? `, hiện tại: ${info.current_commit}` : ""})`;
            banner.classList.remove("hidden");
          }
          await doUpdate();
        } else {
          showToast(`✓ Bạn đang ở phiên bản mới nhất (${info.current_commit || "mới nhất"})!`);
        }
      } catch (e) {
        await doUpdate();
      }
    });
  }

  if (btnBannerUpdate) btnBannerUpdate.addEventListener("click", doUpdate);
  if (btnBannerDismiss) {
    btnBannerDismiss.addEventListener("click", () => {
      if (banner) banner.classList.add("hidden");
    });
  }

  try {
    const info = await api("/api/check-update");
    if (info.has_update) {
      if (btn) {
        btn.classList.add("btn-update-available");
        btn.textContent = "🔔 Có update mới!";
        btn.title = info.message || "Có bản cập nhật mới trên GitHub (bấm để tải)";
      }
      if (banner && bannerMsg) {
        bannerMsg.textContent = `(Bản mới: ${info.latest_commit}${info.current_commit ? `, hiện tại: ${info.current_commit}` : ""})`;
        banner.classList.remove("hidden");
      }
    } else if (info.status === "up_to_date") {
      if (btn) {
        btn.title = `Đã là phiên bản mới nhất (${info.current_commit})`;
      }
    }
  } catch (err) {
    // Bỏ qua nếu offline
  }
}
