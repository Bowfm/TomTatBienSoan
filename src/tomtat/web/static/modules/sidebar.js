// Quản lý cột bên trái: danh sách Chờ duyệt & Đã duyệt, lọc, sắp xếp, xóa file
import { isStemBusy, isStemQueued, isStemRunning, store } from "./state.js";
import { $, api, escapeHtml, setText, showToast, withPreservedScroll } from "./utils.js";

let _selectFileHandler = null;
let _syncRerunButtonsHandler = null;
let _stopFileHandler = null;

export const STEP_SHORT_VI = {
  inspect: "Đọc cấu trúc",
  label: "Gán nhãn",
  extract: "Trích chọn",
  extract_abstract: "Trích & Biên soạn",
  abstract: "Biên soạn",
  judge: "Chấm G-Eval",
  all: "Chạy lại cả bài",
};

export function registerSelectFileHandler(fn) {
  _selectFileHandler = fn;
}

export function registerSyncRerunButtonsHandler(fn) {
  _syncRerunButtonsHandler = fn;
}

export function registerStopFileHandler(fn) {
  _stopFileHandler = fn;
}

export function syncRunningProgressBar(batchProgressText = null) {
  if (batchProgressText !== null) {
    store.BATCH_PROGRESS_TEXT = batchProgressText || "";
  }
  const box = $("batch-progress");
  const stopBtn = $("btn-stop-batch");
  const runBtn = $("btn-run-pending");
  if (!box) return;

  if (store.BATCH_ACTIVE && store.BATCH_PROGRESS_TEXT) {
    box.textContent = store.BATCH_PROGRESS_TEXT;
    box.classList.remove("hidden");
    if (stopBtn) {
      stopBtn.classList.remove("hidden");
      stopBtn.disabled = Boolean(store.BATCH_STOPPED);
      stopBtn.textContent = store.BATCH_STOPPED ? "⏳ Đang dừng..." : "⏹ Dừng";
      stopBtn.title = "Dừng toàn bộ tiến trình AI đang chạy";
    }
    if (runBtn) runBtn.disabled = true;
    return;
  }

  const runningSet = new Set([...store.RUNNING_STEMS, ...store.LOCAL_RERUN_STEMS]);
  const queuedSet = new Set(
    [...store.QUEUED_STEMS, ...store.LOCAL_QUEUED_STEMS].filter((s) => !runningSet.has(s))
  );

  if (runningSet.size > 0 || queuedSet.size > 0) {
    const runningList = Array.from(runningSet).map((s) => {
      const sc = (store.RUNNING_STEPS || {})[s] || "";
      const sv = STEP_SHORT_VI[sc] || sc || "";
      return sv ? `${s}.docx (${sv})` : `${s}.docx`;
    });
    const queuedList = Array.from(queuedSet)
      .sort((a, b) => Number((store.QUEUED_POSITIONS || {})[a] || 999) - Number((store.QUEUED_POSITIONS || {})[b] || 999))
      .map((s) => {
        const sc = (store.QUEUED_STEPS || {})[s] || "";
        const sv = STEP_SHORT_VI[sc] || sc || "";
        const pos = (store.QUEUED_POSITIONS || {})[s];
        const meta = [sv, pos ? `#${pos}` : ""].filter(Boolean).join(", ");
        return meta ? `${s}.docx (${meta})` : `${s}.docx`;
      });
    const busyList = [...runningSet, ...queuedSet];
    const allStopping = busyList.length > 0 && busyList.every((s) => store.STOPPING_STEMS.has(s));
    const maxW = Number(store.ALLOWED_WORKERS || 0);
    const slotDesc = maxW > 0 ? `${runningList.length}/${maxW} luồng` : `${runningList.length} file`;
    if (allStopping) {
      box.textContent = `⏳ Đang dừng AI: ${runningList.concat(queuedList).join(", ")}...`;
    } else if (runningList.length > 0 && queuedList.length > 0) {
      box.textContent = `⏳ Đang chạy AI (${slotDesc}): ${runningList.join(", ")} · Chờ lượt: ${queuedList.join(", ")}`;
    } else if (runningList.length > 0) {
      box.textContent = `⏳ Đang chạy AI (${slotDesc}): ${runningList.join(", ")}`;
    } else {
      box.textContent = `⏳ Đang chờ lượt AI: ${queuedList.join(", ")}`;
    }
    box.classList.remove("hidden");
    if (stopBtn) {
      stopBtn.classList.remove("hidden");
      stopBtn.disabled = allStopping;
      stopBtn.textContent = allStopping ? "⏳ Đang dừng..." : "⏹ Dừng";
      stopBtn.title = "Dừng tiến trình AI đang chạy";
    }
    if (runBtn) runBtn.disabled = false;
    return;
  }

  box.classList.add("hidden");
  if (stopBtn) {
    stopBtn.classList.add("hidden");
    stopBtn.disabled = false;
    stopBtn.textContent = "⏹ Dừng";
  }
  if (runBtn) runBtn.disabled = false;
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

export function renderRpdStatus(rpdList) {
  const container = $("rpd-models-list");
  if (!container || !Array.isArray(rpdList)) return;
  store.RPD_STATUS = rpdList;
  container.innerHTML = "";
  for (const item of rpdList) {
    const model = item.model || "";
    const used = Number(item.used ?? 0);
    const okUsed = Number(item.ok_used ?? 0);
    const trUsed = Number(item.transient_used ?? 0);
    const limit = Number(item.limit ?? 0);
    const perKey = Number(item.rpd_per_key ?? limit);
    const keyCount = Number(item.key_count ?? 1);
    const exhKeys = Number(item.exhausted_keys ?? 0);
    const availKeys = item.available_keys != null ? Number(item.available_keys) : Math.max(0, keyCount - exhKeys);
    const text = exhKeys > 0 ? `${model}: ${used}/${limit} (hết ${exhKeys} key)` : `${model}: ${used}/${limit}`;

    let stateCls = "rpd-ok";
    if ((limit > 0 && used >= limit) || (keyCount > 0 && exhKeys >= keyCount)) {
      stateCls = "rpd-exhausted";
    } else if ((limit > 0 && used / limit >= 0.8) || exhKeys > 0) {
      stateCls = "rpd-warn";
    } else if (used > 0) {
      stateCls = "rpd-active";
    }

    const span = document.createElement("span");
    span.className = `rpd-pill ${stateCls}`;
    span.dataset.model = model;
    span.setAttribute("role", "button");
    span.setAttribute("tabindex", "0");
    span.textContent = text;
    span.title = `${model}: Đã gửi ${used}/${limit} requests (Thành công: ${okUsed} · Lỗi 503/Timeout/Hủy: ${trUsed} · Còn ${availKeys}/${keyCount} key · ${perKey} RPD/key · Bấm để xem chi tiết từng key)`;
    container.appendChild(span);
  }
}

export function syncPoolScalingUI(scaling) {
  if (!scaling) return;
  if (scaling.recommended_workers) {
    store.ALLOWED_WORKERS = Number(scaling.recommended_workers);
  }
  const sel = $("sel-concurrency");
  if (sel) {
    const rec = Number(scaling.recommended_workers || 4);
    const kCount = Number(scaling.key_count || 0);
    const autoOpt = sel.querySelector('option[value="auto"]');
    if (autoOpt) {
      autoOpt.textContent = kCount > 0 ? `Tự động: ${rec} luồng (${kCount} keys)` : `Tự động (${rec} luồng)`;
    }
    // Bổ sung các mức luồng được gợi ý vào dropdown nếu chưa có
    const opts = Array.isArray(scaling.concurrency_options) ? scaling.concurrency_options : [];
    const cap = Number(scaling.max_workers || 0);
    // Trần cứng max_workers: gỡ mọi mức luồng vượt trần (server cũng tự kẹp lại)
    if (cap > 0) {
      for (const o of Array.from(sel.options)) {
        if (o.value !== "auto" && Number(o.value) > cap) {
          if (o.selected) sel.value = "auto";
          o.remove();
        }
      }
    }
    const existingVals = new Set(Array.from(sel.options).map((o) => o.value));
    for (const val of opts) {
      const sVal = String(val);
      if (cap > 0 && Number(val) > cap) continue;
      if (!existingVals.has(sVal)) {
        const opt = document.createElement("option");
        opt.value = sVal;
        opt.textContent = `${val} luồng`;
        sel.appendChild(opt);
        existingVals.add(sVal);
      }
    }
  }
}

export async function loadFileList(keepCurrent = true) {
  const data = await api("/api/files");
  store.FILES = data.files || [];
  if (data.allowed_workers) {
    store.ALLOWED_WORKERS = Number(data.allowed_workers);
  }
  if (Array.isArray(data.running_stems)) {
    const srvSteps = data.running_steps || {};
    store.RUNNING_STEMS = new Set([...data.running_stems, ...store.LOCAL_RERUN_STEMS]);
    const srvQueued = Array.isArray(data.queued_rerun_stems) ? data.queued_rerun_stems : [];
    const srvQueuedSteps = data.queued_rerun_steps || {};
    const srvQueuedPos = data.queued_rerun_positions || {};
    store.QUEUED_STEMS = new Set(
      [...srvQueued, ...store.LOCAL_QUEUED_STEMS].filter((s) => !store.RUNNING_STEMS.has(s))
    );
    const nextQueuedSteps = {};
    const nextQueuedPos = {};
    for (const s of store.QUEUED_STEMS) {
      nextQueuedSteps[s] = srvQueuedSteps[s] || store.QUEUED_STEPS[s] || "";
      if (srvQueuedPos[s]) nextQueuedPos[s] = Number(srvQueuedPos[s]);
    }
    store.QUEUED_STEPS = nextQueuedSteps;
    store.QUEUED_POSITIONS = nextQueuedPos;
    const srvStopping = new Set(data.stopping_stems || []);
    for (const s of Array.from(store.STOPPING_STEMS)) {
      if (!store.RUNNING_STEMS.has(s) && !store.QUEUED_STEMS.has(s)) store.STOPPING_STEMS.delete(s);
    }
    for (const s of srvStopping) {
      if (store.RUNNING_STEMS.has(s) || store.QUEUED_STEMS.has(s)) store.STOPPING_STEMS.add(s);
    }
    const nextSteps = {};
    for (const s of store.RUNNING_STEMS) {
      nextSteps[s] = srvSteps[s] || store.RUNNING_STEPS[s] || "";
    }
    store.RUNNING_STEPS = nextSteps;
  }
  if (data.pool_scaling) {
    syncPoolScalingUI(data.pool_scaling);
  }
  if (Array.isArray(data.rpd_status) && data.rpd_status.length > 0) {
    renderRpdStatus(data.rpd_status);
  } else {
    const primary = data.primary_model || "gemini-4-hydrogen";
    const fallbacks = (data.fallback_models || []).filter((m) => m && m !== primary);
    const chain = [primary, ...fallbacks];
    const kCount = Math.max(1, Number(data.api_key_count || 1));
    const fallbackRpd = chain.map((m) => {
      const perKey = m.toLowerCase().includes("lite") ? 500 : 20;
      return {
        model: m,
        used: 0,
        limit: perKey * kCount,
        rpd_per_key: perKey,
        key_count: kCount,
        exhausted_keys: 0,
      };
    });
    renderRpdStatus(fallbackRpd);
  }
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
  syncRunningProgressBar();
  if (_syncRerunButtonsHandler) {
    _syncRerunButtonsHandler();
  }
  if (!keepCurrent || !store.CURRENT_STEM) {
    if (store.FILES.length > 0 && _selectFileHandler) {
      await _selectFileHandler(store.FILES[0].file.replace(/\.docx$/i, ""));
    }
  } else if (store.CURRENT_STEM && _selectFileHandler) {
    const curRow = store.FILES.find((r) => r.file.replace(/\.docx$/i, "") === store.CURRENT_STEM);
    const st = store.CURRENT_STATE;
    if (curRow && st) {
      const serverChanged =
        (curRow.updated_at && st.updated_at && curRow.updated_at !== st.updated_at) ||
        curRow.status !== st.status ||
        curRow.abstract_words !== ((st.abstract || {}).words || 0) ||
        curRow.extract_words !== ((st.extract || {}).words || 0) ||
        curRow.label !== ((st.label || {}).value || "") ||
        curRow.n_errors !== ((st.checks || {}).n_errors || 0) ||
        curRow.n_warnings !== ((st.checks || {}).n_warnings || 0) ||
        Number(curRow.current_version || 0) !== Number(st.current_version || 0);
      const activeId = document.activeElement ? document.activeElement.id : "";
      const isActivelyEditing =
        !isStemBusy(store.CURRENT_STEM) &&
        (activeId === "txt-extract" ||
          activeId === "txt-abstract" ||
          activeId === "sel-style" ||
          activeId === "version-note");
      if (serverChanged && !isActivelyEditing) {
        await _selectFileHandler(store.CURRENT_STEM, {
          silent: true,
          onlyIfCurrent: true,
          skipSidebarRender: true,
        });
      }
    }
  }
}

function buildFileItemState(r, defaultPillText) {
  const stem = r.file.replace(/\.docx$/i, "");
  const isRunning = isStemRunning(stem);
  const isQueued = isStemQueued(stem);
  const isBusy = isRunning || isQueued;
  const isStopping = isBusy && store.STOPPING_STEMS.has(stem);
  const stepCode = isQueued
    ? ((store.QUEUED_STEPS && store.QUEUED_STEPS[stem]) || "")
    : ((store.RUNNING_STEPS && store.RUNNING_STEPS[stem]) || r.current_step || "");
  const stepVi = STEP_SHORT_VI[stepCode] || "";
  const qPos = store.QUEUED_POSITIONS ? store.QUEUED_POSITIONS[stem] : 0;
  const pillClass = isRunning ? "st-running" : isQueued ? "st-queued" : `st-${r.status}`;
  let pillText = defaultPillText;
  if (isRunning) {
    pillText = isStopping ? "⏳ Đang dừng..." : (stepVi ? `⏳ Đang chạy (${stepVi})...` : "⏳ Đang chạy AI...");
  } else if (isQueued) {
    pillText = isStopping
      ? "⏳ Đang dừng..."
      : stepVi
        ? `⏳ Chờ lượt${qPos ? ` #${qPos}` : ""} (${stepVi})`
        : `⏳ Chờ lượt${qPos ? ` #${qPos}` : ""}...`;
  }
  const stopItemHtml = isBusy
    ? `<button type="button" class="btn-stop-item" data-stop-stem="${escapeHtml(stem)}" title="${isStopping ? "Đang dừng..." : isQueued ? `Hủy chờ lượt AI cho ${escapeHtml(r.file)}` : `Dừng chạy AI cho ${escapeHtml(r.file)}`}" ${isStopping ? "disabled" : ""}>${isStopping ? "⏳" : "⏹"}</button>`
    : "";
  const suspBadge = r.is_suspicious
    ? `<span class="badge-suspicious" title="${escapeHtml((r.suspicious_reasons || []).join("; ") || "File có dấu hiệu lỗi cần hủy")}">⚠️ Nghi hủy</span>`
    : "";
  return { stem, isBusy, pillClass, pillText, stopItemHtml, suspBadge };
}

export function syncActiveFileItem() {
  const cur = store.CURRENT_STEM;
  let matched = false;
  for (const listId of ["file-list", "done-list"]) {
    const ul = $(listId);
    if (!ul) continue;
    ul.querySelectorAll(".file-item[data-stem]").forEach((li) => {
      const isCur = Boolean(cur && li.dataset.stem === cur);
      li.classList.toggle("active", isCur);
      if (isCur) matched = true;
    });
  }
  return matched;
}

export function renderFileList() {
  const allPending = store.FILES.filter((r) => !isApprovedStatus(r.status));
  const allApproved = approvedFiles();
  const draftCount = allPending.filter((r) => r.status === "da_co_nhap").length;
  const unprocessedCount = allPending.filter(
    (r) => r.status === "chua_xu_ly" || r.status === "dang_lam_do" || r.status === "can_xu_ly_tay"
  ).length;
  const errorCount = allPending.filter((r) => r.status === "loi").length;

  setText("cnt-total", store.FILES.length);
  setText("cnt-draft", draftCount);
  setText("cnt-unprocessed", unprocessedCount);
  setText("cnt-approved", allApproved.length);
  setText("badge-pending", allPending.length);
  setText("done-count", allApproved.length);
  const unprocWrap = $("cnt-unprocessed-wrap");
  if (unprocWrap) {
    unprocWrap.title = errorCount > 0
      ? `Số file chưa xử lý (ngoài ra còn ${errorCount} file lỗi)`
      : "Số file chưa xử lý";
  }
  if ($("btn-clear-pending")) $("btn-clear-pending").disabled = allPending.length === 0;
  if ($("btn-clear-approved")) $("btn-clear-approved").disabled = allApproved.length === 0;

  const ul = $("file-list");
  if (!ul) return;
  withPreservedScroll(ul, () => {
    ul.innerHTML = "";
    const list = pendingFiles();
    if (!list.length) {
      ul.innerHTML = `<li class="muted" style="padding:10px 12px">Không có file nào trong mục này.</li>`;
    } else {
      for (const r of list) {
        const { stem, isBusy, pillClass, pillText, stopItemHtml, suspBadge } = buildFileItemState(r, r.status_vi);
        const li = document.createElement("li");
        li.className = "file-item" + (stem === store.CURRENT_STEM ? " active" : "");
        li.dataset.stem = stem;
        li.innerHTML = `
          <div class="file-item-top">
            <span style="display:inline-flex;align-items:center;gap:6px">${escapeHtml(r.file)} ${suspBadge}</span>
            <span style="display:flex;align-items:center;gap:4px">
              <span class="status-pill ${pillClass}">${escapeHtml(pillText)}</span>
              ${stopItemHtml}
              <button class="btn-del-item" data-del-stem="${stem}" title="${isBusy ? "Hãy dừng AI trước khi xóa file" : `Xóa file ${escapeHtml(r.file)}`}" ${isBusy ? "disabled" : ""}>✕</button>
            </span>
          </div>
          <div class="file-item-sub">
            <span>${escapeHtml(r.label || "(chưa gán nhãn)")}${r.model_used ? ` <strong style="color:#1d4ed8">[${escapeHtml(r.model_used)}]</strong>` : ""}</span>
            <span>${r.n_errors ? `Lỗi: ${r.n_errors}` : `CB: ${r.n_warnings || 0}`}</span>
          </div>
        `;
        const stopBtn = li.querySelector(".btn-stop-item");
        if (stopBtn) {
          stopBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            if (_stopFileHandler) _stopFileHandler(stem);
          });
        }
        li.querySelector(".btn-del-item").addEventListener("click", (e) => {
          e.stopPropagation();
          if (isStemBusy(stem)) return;
          deleteFiles("selected", [stem], `Bạn có chắc muốn xóa file '${r.file}'?`);
        });
        li.addEventListener("click", () => {
          if (_selectFileHandler) _selectFileHandler(stem);
        });
        ul.appendChild(li);
      }
    }
  });
  renderDoneList();
}

export function renderDoneList() {
  const doneUl = $("done-list");
  if (!doneUl) return;
  const doneFiles = approvedFiles();
  withPreservedScroll(doneUl, () => {
    doneUl.innerHTML = "";
    if (!doneFiles.length) {
      doneUl.innerHTML = `<li class="muted" style="padding:10px 12px">Chưa có file nào đã duyệt.</li>`;
    } else {
      for (const r of doneFiles) {
        const { stem, isBusy, pillClass, pillText, stopItemHtml, suspBadge } = buildFileItemState(r, "Đã duyệt");
        const li = document.createElement("li");
        li.className = "file-item" + (stem === store.CURRENT_STEM ? " active" : "");
        li.dataset.stem = stem;
        li.innerHTML = `
          <div class="file-item-top">
            <span style="display:inline-flex;align-items:center;gap:6px">
              <strong>${escapeHtml(r.file)}</strong>
              ${suspBadge}
            </span>
            <span style="display:flex;align-items:center;gap:4px">
              <span class="status-pill ${pillClass}">${escapeHtml(pillText)}</span>
              ${stopItemHtml}
              <button class="btn-del-item" data-del-stem="${stem}" title="${isBusy ? "Hãy dừng AI trước khi xóa file" : `Xóa file ${escapeHtml(r.file)}`}" ${isBusy ? "disabled" : ""}>✕</button>
            </span>
          </div>
          <div class="done-item-actions">
            <span class="muted">${escapeHtml(r.label || "")}</span>
          </div>
        `;
        const stopBtn = li.querySelector(".btn-stop-item");
        if (stopBtn) {
          stopBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            if (_stopFileHandler) _stopFileHandler(stem);
          });
        }
        li.querySelector(".btn-del-item").addEventListener("click", (e) => {
          e.stopPropagation();
          if (isStemBusy(stem)) return;
          deleteFiles("selected", [stem], `Bạn có chắc muốn xóa file '${r.file}'?`);
        });
        li.addEventListener("click", () => {
          if (_selectFileHandler) _selectFileHandler(stem);
        });
        doneUl.appendChild(li);
      }
    }
  });
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
