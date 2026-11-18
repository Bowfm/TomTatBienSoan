// Quản lý cột bên trái: danh sách Chờ duyệt & Đã duyệt, lọc, sắp xếp, tải ZIP, xóa file
import { store } from "./state.js";
import { $, api, escapeHtml, setText, showToast, todayLabel } from "./utils.js";

let _selectFileHandler = null;

export function registerSelectFileHandler(fn) {
  _selectFileHandler = fn;
}

export function sortFiles(list) {
  return [...list].sort((a, b) => {
    const cmp = (a.file || "").localeCompare(b.file || "", undefined, { numeric: true, sensitivity: "base" });
    return store.SORT_ORDER === "desc" ? -cmp : cmp;
  });
}

export function isApprovedStatus(st) {
  return st === "da_duyet" || st === "da_xuat";
}

export function pendingFiles() {
  const f = $("filter-status") ? $("filter-status").value : "";
  const list = store.FILES.filter((r) => !isApprovedStatus(r.status) && (!f || r.status === f));
  return sortFiles(list);
}

export function approvedFiles() {
  const list = store.FILES.filter((r) => isApprovedStatus(r.status));
  return sortFiles(list);
}

export function filteredFiles() {
  // Dùng cho phím tắt Alt+↑/↓: duyệt qua cột Chờ duyệt trước, rồi tới Đã duyệt
  return [...pendingFiles(), ...approvedFiles()];
}

export async function loadFileList(keepCurrent = true) {
  const data = await api("/api/files");
  store.FILES = data.files || [];
  const badge = $("mode-badge");
  if (badge) {
    if (data.mock || !data.has_api_key) {
      badge.textContent = data.has_api_key ? "Chế độ giả lập (--mock)" : "Chưa có GEMINI_API_KEY (dùng --mock)";
    } else {
      const fb = (data.fallback_models || []).slice(0, 2).join(" → ");
      const keysCount = data.api_key_count ? ` · 🔑 ${data.api_key_count} keys` : "";
      const learned = data.learned_count ? ` · 🧠 Đã học: ${data.learned_count} bài duyệt` : "";
      badge.textContent = `Model chính: ${data.primary_model || "gemini-4-hydrogen"}${fb ? ` (dự phòng: ${fb}...)` : ""}${keysCount}${learned}`;
      badge.style.background = "#1d4ed8";
      badge.style.color = "#ffffff";
    }
  }
  renderFileList();
  if (!keepCurrent || !store.CURRENT_STEM) {
    if (store.FILES.length > 0 && _selectFileHandler) {
      await _selectFileHandler(store.FILES[0].file.replace(/\.docx$/i, ""));
    }
  }
}

export function renderFileList() {
  const allPending = store.FILES.filter((r) => !isApprovedStatus(r.status));
  const allApproved = approvedFiles();

  setText("cnt-total", store.FILES.length);
  setText("cnt-pending", allPending.length);
  setText("cnt-approved", allApproved.length);
  setText("badge-pending", allPending.length);
  setText("done-count", allApproved.length);
  if ($("btn-clear-pending")) $("btn-clear-pending").disabled = allPending.length === 0;
  if ($("btn-clear-approved")) $("btn-clear-approved").disabled = allApproved.length === 0;

  const ul = $("file-list");
  if (!ul) return;
  ul.innerHTML = "";
  const list = pendingFiles();
  if (!list.length) {
    ul.innerHTML = `<li class="muted" style="padding:10px 12px">Không có file nào trong mục này.</li>`;
  } else {
    for (const r of list) {
      const stem = r.file.replace(/\.docx$/i, "");
      const isRunning = store.RUNNING_STEMS.has(stem);
      const li = document.createElement("li");
      li.className = "file-item" + (stem === store.CURRENT_STEM ? " active" : "");
      li.dataset.stem = stem;
      const pillClass = isRunning ? "st-running" : `st-${r.status}`;
      const pillText = isRunning ? "⏳ Đang chạy AI..." : r.status_vi;
      const suspBadge = r.is_suspicious
        ? `<span class="badge-suspicious" title="${escapeHtml((r.suspicious_reasons || []).join("; ") || "File có dấu hiệu lỗi cần hủy")}">⚠️ Nghi hủy</span>`
        : "";
      li.innerHTML = `
        <div class="file-item-top">
          <span style="display:inline-flex;align-items:center;gap:6px">${escapeHtml(r.file)} ${suspBadge}</span>
          <span style="display:flex;align-items:center;gap:4px">
            <span class="status-pill ${pillClass}">${escapeHtml(pillText)}</span>
            <button class="btn-del-item" data-del-stem="${stem}" title="Xóa file ${escapeHtml(r.file)}">✕</button>
          </span>
        </div>
        <div class="file-item-sub">
          <span>${escapeHtml(r.label || "(chưa gán nhãn)")}${r.model_used ? ` <strong style="color:#1d4ed8">[${escapeHtml(r.model_used)}]</strong>` : ""}</span>
          <span>${r.n_errors ? `Lỗi: ${r.n_errors}` : `CB: ${r.n_warnings || 0}`}</span>
        </div>
      `;
      li.querySelector(".btn-del-item").addEventListener("click", (e) => {
        e.stopPropagation();
        deleteFiles("selected", [stem], `Bạn có chắc muốn xóa file '${r.file}'?`);
      });
      li.addEventListener("click", () => {
        if (_selectFileHandler) _selectFileHandler(stem);
      });
      ul.appendChild(li);
    }
  }
  renderDoneList();
}

export function renderDoneList() {
  const doneUl = $("done-list");
  if (!doneUl) return;
  const doneFiles = approvedFiles();

  if (!store.DONE_SELECTED._initializedStems) store.DONE_SELECTED._initializedStems = new Set();
  for (const r of doneFiles) {
    const stem = r.file.replace(/\.docx$/i, "");
    if (!store.DONE_SELECTED._initializedStems.has(stem)) {
      store.DONE_SELECTED.add(stem);
      store.DONE_SELECTED._initializedStems.add(stem);
    }
  }

  doneUl.innerHTML = "";
  if (!doneFiles.length) {
    doneUl.innerHTML = `<li class="muted" style="padding:10px 12px">Chưa có file nào đã duyệt.</li>`;
  } else {
    for (const r of doneFiles) {
      const stem = r.file.replace(/\.docx$/i, "");
      const checked = store.DONE_SELECTED.has(stem);
      const suspBadge = r.is_suspicious
        ? `<span class="badge-suspicious" title="${escapeHtml((r.suspicious_reasons || []).join("; ") || "File có dấu hiệu lỗi cần hủy")}">⚠️ Nghi hủy</span>`
        : "";
      const li = document.createElement("li");
      li.className = "file-item" + (stem === store.CURRENT_STEM ? " active" : "");
      li.dataset.stem = stem;
      li.innerHTML = `
        <div class="file-item-top">
          <label class="chk-inline" title="Tick để đưa vào file ZIP" onclick="event.stopPropagation()">
            <input type="checkbox" data-done-stem="${stem}" ${checked ? "checked" : ""}>
            <strong>${escapeHtml(r.file)}</strong>
            ${suspBadge}
          </label>
          <span style="display:flex;align-items:center;gap:4px">
            <span class="status-pill st-${r.status}">Đã duyệt</span>
            <button class="btn-del-item" data-del-stem="${stem}" title="Xóa file ${escapeHtml(r.file)}">✕</button>
          </span>
        </div>
        <div class="done-item-actions">
          <span class="muted">${escapeHtml(r.label || "")}</span>
          <a href="/api/download-file/${encodeURIComponent(stem)}" onclick="event.stopPropagation()" title="Tải lẻ file .docx này">⬇ Tải lẻ</a>
        </div>
      `;
      li.querySelector(".btn-del-item").addEventListener("click", (e) => {
        e.stopPropagation();
        deleteFiles("selected", [stem], `Bạn có chắc muốn xóa file '${r.file}'?`);
      });
      const cb = li.querySelector("input");
      cb.addEventListener("change", (e) => {
        e.stopPropagation();
        if (cb.checked) store.DONE_SELECTED.add(stem);
        else store.DONE_SELECTED.delete(stem);
        updateZipButtonState(doneFiles.length);
      });
      li.addEventListener("click", () => {
        if (_selectFileHandler) _selectFileHandler(stem);
      });
      doneUl.appendChild(li);
    }
  }
  updateZipButtonState(doneFiles.length);
}

export function updateZipButtonState(totalDone) {
  const btn = $("btn-download-zip");
  if (!btn) return;
  const t = todayLabel();
  const count = Array.from(store.DONE_SELECTED).filter((s) =>
    store.FILES.some((r) => (r.status === "da_xuat" || r.status === "da_duyet") && r.file.replace(/\.docx$/i, "") === s)
  ).length;
  btn.textContent = `⬇ Tải ZIP ngày ${t.slash} (${count} file)`;
  btn.disabled = count === 0;
  const chkAll = $("chk-done-all");
  if (chkAll) chkAll.checked = totalDone > 0 && count === totalDone;
}

export function downloadDoneZip() {
  const activeStems = store.FILES
    .filter((r) => r.status === "da_xuat" || r.status === "da_duyet")
    .map((r) => r.file.replace(/\.docx$/i, ""))
    .filter((s) => store.DONE_SELECTED.has(s));
  if (!activeStems.length) {
    showToast("Hãy tick chọn ít nhất 1 file đã xong để tải ZIP.", true);
    return;
  }
  const url = `/api/download-zip?stems=${encodeURIComponent(activeStems.join(","))}`;
  window.location.href = url;
}

export function moveFile(delta) {
  const list = filteredFiles();
  if (!list.length) return;
  const idx = list.findIndex((r) => r.file.replace(/\.docx$/i, "") === store.CURRENT_STEM);
  const nextIdx = Math.max(0, Math.min(list.length - 1, (idx < 0 ? 0 : idx) + delta));
  const stem = list[nextIdx].file.replace(/\.docx$/i, "");
  if (stem !== store.CURRENT_STEM && _selectFileHandler) _selectFileHandler(stem);
}

export async function deleteFiles(mode, stems = [], confirmMsg = "") {
  if (confirmMsg && !window.confirm(confirmMsg)) return;
  try {
    const data = await api("/api/delete-files", {
      method: "POST",
      body: JSON.stringify({ mode, stems }),
    });
    for (const f of data.deleted || []) {
      const st = f.replace(/\.docx$/i, "");
      store.DONE_SELECTED.delete(st);
      if (store.CURRENT_STEM === st) {
        store.CURRENT_STEM = null;
        store.CURRENT_STATE = null;
        store.CURRENT_UNITS = null;
      }
    }
    await loadFileList(false);
    if (!store.FILES.length) {
      $("editor-pane").classList.add("hidden");
      $("empty-state").classList.remove("hidden");
    }
    showToast(`Đã xóa ${data.count} file.`);
  } catch (err) {
    showToast(err.message, true);
  }
}
