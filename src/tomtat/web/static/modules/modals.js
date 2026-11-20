// Hộp thoại xem trước bảng .docx, xem nhật ký Gemini (Logs) thời gian thực và tự động kiểm tra cập nhật GitHub
import {
  STEP_NAMES_VI,
  VERSION_SOURCE_VI,
  computePrevRollbackTarget,
  formatVersionTime,
  hasUnsavedVersionChanges,
  registerPreviewVersionHandler,
  restoreVersion,
} from "./editor.js";
import { renderRpdStatus } from "./sidebar.js";
import { isStemBusy, store } from "./state.js";
import { $, api, escapeHtml, showToast } from "./utils.js";

let LOGS_POLL_TIMER = null;
let LOGS_TICK_TIMER = null;
let LOGS_IN_FLIGHT = false;
let LOG_VIEW_STEM = null;

export function stopLogsPolling() {
  if (LOGS_POLL_TIMER) {
    clearInterval(LOGS_POLL_TIMER);
    LOGS_POLL_TIMER = null;
  }
  if (LOGS_TICK_TIMER) {
    clearInterval(LOGS_TICK_TIMER);
    LOGS_TICK_TIMER = null;
  }
  LOGS_IN_FLIGHT = false;
}

function setPreviewModalWide(wide) {
  const box = document.querySelector("#preview-modal .modal-box");
  if (box) box.classList.toggle("modal-box-wide", Boolean(wide));
}

export function closePreviewModal() {
  stopLogsPolling();
  setPreviewModalWide(false);
  const modal = $("preview-modal");
  if (modal) modal.classList.add("hidden");
}

function formatDocUnitsText(units) {
  const paras = [];
  let curPara = null;
  let curParts = [];
  for (const u of units || []) {
    if (curPara !== null && u.para !== curPara) {
      paras.push(curParts.join(" "));
      curParts = [];
    }
    curPara = u.para;
    curParts.push(u.text);
  }
  if (curParts.length) paras.push(curParts.join(" "));
  return paras.join("\n");
}

export function openPreviewModal() {
  stopLogsPolling();
  setPreviewModalWide(false);
  if (!store.CURRENT_UNITS) return;
  if ($("preview-modal-title")) $("preview-modal-title").textContent = "Xem trước bảng .docx cuối cùng";
  const body = $("preview-modal-body");
  if (body) body.innerHTML = `<table class="docx-preview-table" id="preview-table"></table>`;
  const tbl = $("preview-table");
  tbl.innerHTML = "";
  const addRow = (tag, text, yellow = false) => {
    const tr = document.createElement("tr");
    if (yellow) tr.className = "row-yellow";
    tr.innerHTML = `<td><div class="row-tag">${escapeHtml(tag)}</div><div class="preview-body">${escapeHtml(text || "")}</div></td>`;
    tbl.appendChild(tr);
  };
  addRow("Hàng 1 — Query", store.CURRENT_UNITS.query || "");
  for (const d of store.CURRENT_UNITS.docs || []) {
    addRow(`Hàng ${d.row + 1} — Văn bản gốc ${d.n} (${d.words} từ)`, formatDocUnitsText(d.units));
  }
  addRow("Gợi ý trích chọn của AI", store.CURRENT_UNITS.ai_extract || "");
  addRow("Gợi ý biên soạn của AI", store.CURRENT_UNITS.ai_abstract || "");
  addRow("Hàng điền 1 — Phong cách chức năng", $("sel-style").value, true);
  addRow("Hàng điền 2 — Bản trích chọn", $("txt-extract").value, true);
  addRow("Hàng điền 3 — Bản biên soạn", $("txt-abstract").value, true);
  $("preview-modal").classList.remove("hidden");
}

function buildLogSummaryHtml(lg) {
  const u = lg.usage || {};
  const tok = u.total_tokens
    ? `${u.total_tokens} tokens (in:${u.total_input_tokens || 0}, out:${u.total_output_tokens || 0}, think:${u.total_thought_tokens || 0})`
    : "";
  const stepVi = STEP_NAMES_VI[lg.step] ? `${lg.step} (${STEP_NAMES_VI[lg.step]})` : lg.step;
  const keyBadge = lg.key_label
    ? ` · <span style="color:#475569;font-family:Consolas,monospace;font-size:12px">${escapeHtml(lg.key_label)}</span>`
    : "";

  let stBadge = "";
  let durHtml = `<strong>${lg.duration_s}s</strong>`;
  if (lg.in_progress) {
    durHtml = `<strong class="live-elapsed" data-started="${lg.started_epoch || 0}">${lg.duration_s}s</strong>`;
    if (lg.phase === "waiting_rpm") {
      stBadge = `<span class="log-badge-waiting">⏳ Đang chờ giãn cách RPM (${lg.wait_s}s) trước khi gọi API...</span>`;
    } else if (lg.background) {
      stBadge = `<span class="log-badge-running">⚡ Đang gọi Gemini API (chạy nền)...</span>`;
    } else {
      stBadge = `<span class="log-badge-running">⚡ Đang gọi Gemini API trực tiếp...</span>`;
    }
  } else if (lg.status === "cancelled") {
    stBadge = `<span style="color:#b45309;font-weight:700">⏹ ĐÃ DỪNG: ${escapeHtml(lg.error || "Đã dừng theo yêu cầu.")}</span>`;
  } else if (lg.error) {
    stBadge = `<span style="color:#b91c1c;font-weight:700">✕ LỖI: ${escapeHtml(lg.error)}</span>`;
  } else {
    stBadge = `<span style="color:#047857;font-weight:700">✓ Thành công</span>`;
  }

  const netTry = Number(lg.net_try || 1);
  const tryDesc =
    netTry > 1
      ? `lần tạo nháp ${lg.attempt}, <span style="color:#b45309;font-weight:600">thử lại API #${netTry}</span>`
      : `lần tạo nháp ${lg.attempt}, gọi API #1`;

  return `
    <strong>[${escapeHtml(lg.started_at)}]</strong>
    Bước: <strong>${escapeHtml(stepVi)}</strong> (${tryDesc})
    · Model: <strong style="color:#1d4ed8">${escapeHtml(lg.model)}</strong>${keyBadge}
    · Thời gian: ${durHtml}
    ${tok ? `· ${escapeHtml(tok)}` : ""} — ${stBadge}
  `;
}

function buildLogBodyHtml(lg) {
  let respContent = "";
  if (lg.in_progress) {
    if (lg.phase === "waiting_rpm") {
      respContent = `⏳ Luồng đang chờ giãn cách RPM (${lg.wait_s}s) trên ${lg.key_label || "API key"} để tránh lỗi 429, sau đó sẽ gửi request ngay...`;
    } else {
      respContent = `⏳ Đã gửi prompt tới model ${lg.model} (${lg.key_label || "API key"}), đang chờ Gemini suy nghĩ và trả kết quả JSON theo thời gian thực...`;
    }
  } else {
    respContent = lg.response_text || lg.error || "(trống)";
  }

  const promptBlock = lg.prompt
    ? `<details style="margin-top:6px">
         <summary style="cursor:pointer;font-size:12px;color:#475569;font-weight:600">📄 Xem Prompt đã gửi (${lg.prompt.length} ký tự)</summary>
         <pre class="ref-pre" style="max-height:180px;overflow-y:auto;background:#f1f5f9;border:1px solid #cbd5e1;margin-top:4px;font-size:12px">${escapeHtml(lg.prompt)}</pre>
       </details>`
    : "";

  return `
    <div style="font-weight:600;margin-bottom:4px">${lg.in_progress ? "Trạng thái cuộc gọi API:" : "Phản hồi của Gemini:"}</div>
    <pre class="ref-pre log-resp-pre" style="max-height:220px;overflow-y:auto;background:#fff;border:1px solid #e2e8f0">${escapeHtml(respContent)}</pre>
    ${promptBlock}
  `;
}

function ensureLogsModalShell() {
  const body = $("preview-modal-body");
  if (!body) return;
  if ($("logs-items-list")) return;
  body.innerHTML = `
    <div id="logs-toolbar" class="logs-toolbar">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <label for="logs-file-select" style="font-weight:600;font-size:13px">Xem log của file:</label>
        <select id="logs-file-select" class="logs-file-select"></select>
        <span id="logs-live-indicator" class="logs-live-indicator"></span>
      </div>
      <div id="logs-running-bar" class="logs-running-bar hidden"></div>
    </div>
    <div id="logs-dir-info" class="muted" style="margin-bottom:8px"></div>
    <div id="logs-items-list"></div>
  `;
  const sel = $("logs-file-select");
  if (sel) {
    sel.addEventListener("change", () => {
      if (sel.value) {
        switchLogsViewStem(sel.value);
      }
    });
  }
}

function renderLogsToolbar(data) {
  const runningSet = new Set([...(data.running_stems || []), ...store.RUNNING_STEMS]);
  const runningSteps = { ...store.RUNNING_STEPS, ...(data.running_steps || {}) };
  if (data.current_step && LOG_VIEW_STEM) {
    runningSteps[LOG_VIEW_STEM] = data.current_step;
  }
  const queuedSet = new Set(
    [...(data.queued_rerun_stems || []), ...store.QUEUED_STEMS].filter((s) => !runningSet.has(s))
  );
  const queuedSteps = { ...store.QUEUED_STEPS, ...(data.queued_rerun_steps || {}) };
  const queuedPositions = { ...store.QUEUED_POSITIONS, ...(data.queued_rerun_positions || {}) };

  const sel = $("logs-file-select");
  if (sel) {
    const allStems = [];
    const seen = new Set();
    for (const r of store.FILES || []) {
      const s = r.file.replace(/\.docx$/i, "");
      if (!seen.has(s)) {
        seen.add(s);
        allStems.push(s);
      }
    }
    if (LOG_VIEW_STEM && !seen.has(LOG_VIEW_STEM)) {
      allStems.unshift(LOG_VIEW_STEM);
    }
    const optsSig = allStems
      .map((s) => `${s}:${runningSet.has(s) ? 1 : queuedSet.has(s) ? 2 : 0}:${runningSteps[s] || queuedSteps[s] || ""}:${queuedPositions[s] || 0}`)
      .join("|");
    if (sel.dataset.sig !== optsSig) {
      sel.dataset.sig = optsSig;
      sel.innerHTML = allStems
        .map((s) => {
          const isRun = runningSet.has(s);
          const isQ = !isRun && queuedSet.has(s);
          const stp = STEP_NAMES_VI[isQ ? queuedSteps[s] : runningSteps[s]] || (isQ ? queuedSteps[s] : runningSteps[s]) || "";
          const prefix = isRun || isQ ? "⏳ " : "";
          const qPos = queuedPositions[s];
          const suffix = isRun && stp
            ? ` (đang chạy: ${stp})`
            : isQ
              ? ` (chờ lượt${qPos ? ` #${qPos}` : ""}${stp ? `: ${stp}` : ""})`
              : "";
          return `<option value="${escapeHtml(s)}">${prefix}${escapeHtml(s)}.docx${escapeHtml(suffix)}</option>`;
        })
        .join("");
    }
    if (LOG_VIEW_STEM && sel.value !== LOG_VIEW_STEM) {
      sel.value = LOG_VIEW_STEM;
    }
  }

  const hasInProgressCall = (data.logs || []).some((lg) => lg.in_progress);
  const isThisRunning = Boolean(
    (LOG_VIEW_STEM && runningSet.has(LOG_VIEW_STEM)) || data.current_step || hasInProgressCall
  );
  const isThisQueued = Boolean(!isThisRunning && LOG_VIEW_STEM && queuedSet.has(LOG_VIEW_STEM));
  const ind = $("logs-live-indicator");
  if (ind) {
    const curStep = (LOG_VIEW_STEM && runningSteps[LOG_VIEW_STEM]) || data.current_step || "";
    const stepVi = STEP_NAMES_VI[curStep] || curStep || "";
    if (isThisRunning) {
      ind.className = "logs-live-indicator is-live";
      ind.innerHTML = `<span class="live-dot"></span> Đang chạy trực tiếp${stepVi ? ` · Bước: <strong>${escapeHtml(stepVi)}</strong>` : ""}`;
    } else if (isThisQueued) {
      const qStep = (LOG_VIEW_STEM && queuedSteps[LOG_VIEW_STEM]) || "";
      const qStepVi = STEP_NAMES_VI[qStep] || qStep || "";
      const qPos = (LOG_VIEW_STEM && queuedPositions[LOG_VIEW_STEM]) || 0;
      ind.className = "logs-live-indicator is-queued";
      ind.innerHTML = `⏳ Đang chờ lượt${qPos ? ` (#${qPos})` : ""}${qStepVi ? ` · Bước: <strong>${escapeHtml(qStepVi)}</strong>` : ""}`;
    } else {
      ind.className = "logs-live-indicator";
      ind.innerHTML = `🟢 Tự động cập nhật thời gian thực (${escapeHtml(data.status_vi || "đã dừng")})`;
    }
  }

  const runBar = $("logs-running-bar");
  if (runBar) {
    const runList = Array.from(runningSet);
    const qList = Array.from(queuedSet).sort(
      (a, b) => Number(queuedPositions[a] || 999) - Number(queuedPositions[b] || 999)
    );
    if (runList.length > 0 || qList.length > 0) {
      runBar.classList.remove("hidden");
      const runHtml = runList.length
        ? `<span style="font-weight:600;font-size:12px;color:#1e40af">⚡ Các luồng đang chạy song song (${runList.length}):</span>` +
          runList
            .map((s) => {
              const stp = STEP_NAMES_VI[runningSteps[s]] || runningSteps[s] || "Đang xử lý";
              const activeCls = s === LOG_VIEW_STEM ? " active" : "";
              return `<button type="button" class="log-run-pill${activeCls}" data-log-stem="${escapeHtml(s)}">⏳ ${escapeHtml(s)}.docx <small>(${escapeHtml(stp)})</small></button>`;
            })
            .join("")
        : "";
      const qHtml = qList.length
        ? `<span style="font-weight:600;font-size:12px;color:#0369a1;margin-left:4px">🕒 Hàng chờ (${qList.length}):</span>` +
          qList
            .map((s) => {
              const stp = STEP_NAMES_VI[queuedSteps[s]] || queuedSteps[s] || "Đang chờ";
              const qPos = queuedPositions[s];
              const activeCls = s === LOG_VIEW_STEM ? " active" : "";
              return `<button type="button" class="log-run-pill log-queue-pill${activeCls}" data-log-stem="${escapeHtml(s)}">🕒 ${qPos ? `#${qPos} ` : ""}${escapeHtml(s)}.docx <small>(${escapeHtml(stp)})</small></button>`;
            })
            .join("")
        : "";
      runBar.innerHTML = runHtml + qHtml;
      runBar.querySelectorAll("button[data-log-stem]").forEach((btn) => {
        btn.addEventListener("click", () => switchLogsViewStem(btn.dataset.logStem));
      });
    } else {
      runBar.classList.add("hidden");
      runBar.innerHTML = "";
    }
  }
}

function renderLogsItems(data) {
  const dirInfo = $("logs-dir-info");
  if (dirInfo) {
    dirInfo.innerHTML = `Thư mục log trên ổ đĩa: <code>${escapeHtml(data.log_dir || "")}</code>`;
  }

  const container = $("logs-items-list");
  if (!container) return;

  const logs = data.logs || [];
  if (container.dataset.viewStem !== LOG_VIEW_STEM) {
    container.dataset.viewStem = LOG_VIEW_STEM || "";
    container.innerHTML = "";
  }

  const runningSet = new Set([...(data.running_stems || []), ...store.RUNNING_STEMS]);
  const queuedSet = new Set(
    [...(data.queued_rerun_stems || []), ...store.QUEUED_STEMS].filter((s) => !runningSet.has(s))
  );
  const queuedSteps = { ...store.QUEUED_STEPS, ...(data.queued_rerun_steps || {}) };
  const queuedPositions = { ...store.QUEUED_POSITIONS, ...(data.queued_rerun_positions || {}) };
  const isThisQueued = Boolean(LOG_VIEW_STEM && queuedSet.has(LOG_VIEW_STEM));

  if (!logs.length) {
    if (isThisQueued) {
      const qStep = queuedSteps[LOG_VIEW_STEM] || "";
      const qStepVi = STEP_NAMES_VI[qStep] || qStep || "Chạy AI";
      const qPos = queuedPositions[LOG_VIEW_STEM] || 1;
      const maxW = Number(data.allowed_workers || store.ALLOWED_WORKERS || 3);
      container.innerHTML = `<div id="logs-queue-banner" class="logs-queue-banner">⏳ File <strong>${escapeHtml(LOG_VIEW_STEM)}.docx</strong> đang chờ lượt (vị trí <strong>#${qPos}</strong> · Bước: <strong>${escapeHtml(qStepVi)}</strong>) do hiện đã chạy đủ ${runningSet.size}/${maxW} luồng AI cho phép. File sẽ tự động chạy ngay khi 1 luồng đang chạy hoàn thành.</div>`;
    } else {
      container.innerHTML = `<div class="muted" id="logs-empty-msg">Chưa có lượt gọi Gemini nào cho file <strong>${escapeHtml(LOG_VIEW_STEM || "")}.docx</strong>. Khi AI bắt đầu gọi API, log sẽ hiện ngay tại đây theo thời gian thực.</div>`;
    }
    return;
  }

  const emptyMsg = $("logs-empty-msg");
  if (emptyMsg) emptyMsg.remove();
  const existingQBanner = $("logs-queue-banner");
  if (existingQBanner) existingQBanner.remove();
  if (isThisQueued) {
    const qStep = queuedSteps[LOG_VIEW_STEM] || "";
    const qStepVi = STEP_NAMES_VI[qStep] || qStep || "Chạy AI";
    const qPos = queuedPositions[LOG_VIEW_STEM] || 1;
    const maxW = Number(data.allowed_workers || store.ALLOWED_WORKERS || 3);
    const qb = document.createElement("div");
    qb.id = "logs-queue-banner";
    qb.className = "logs-queue-banner";
    qb.innerHTML = `⏳ Yêu cầu mới (Bước: <strong>${escapeHtml(qStepVi)}</strong>) đang chờ lượt ở vị trí <strong>#${qPos}</strong> do đã chạy đủ ${runningSet.size}/${maxW} luồng AI cho phép. Sẽ tự chạy ngay khi 1 luồng hoàn thành.`;
    container.prepend(qb);
  }

  const existingMap = new Map();
  container.querySelectorAll("details[data-log-fn]").forEach((el) => {
    existingMap.set(el.dataset.logFn, el);
  });

  const seenFns = new Set();
  logs.forEach((lg, idx) => {
    const fn = lg.filename || `idx_${idx}`;
    seenFns.add(fn);
    const sig = [
      lg.in_progress ? "1" : "0",
      lg.phase || "",
      lg.wait_s || 0,
      lg.net_try || 1,
      lg.key_label || "",
      lg.error || "",
      (lg.response_text || "").length,
      (lg.usage || {}).total_tokens || 0,
      lg.in_progress ? "" : lg.duration_s,
    ].join("|");

    let det = existingMap.get(fn);
    if (!det) {
      det = document.createElement("details");
      det.dataset.logFn = fn;
      det.dataset.sig = sig;
      det.className = "log-item-details" + (lg.in_progress ? " log-item-running" : "");
      if (lg.in_progress) {
        det.open = true;
      }
      det.innerHTML = `
        <summary style="cursor:pointer;font-size:13px">${buildLogSummaryHtml(lg)}</summary>
        <div class="log-detail-body" style="margin-top:8px">${buildLogBodyHtml(lg)}</div>
      `;
      const refNode = container.children[idx] || null;
      container.insertBefore(det, refNode);
    } else {
      if (det.dataset.sig !== sig) {
        det.dataset.sig = sig;
        det.className = "log-item-details" + (lg.in_progress ? " log-item-running" : "");
        const sumEl = det.querySelector("summary");
        const bodyEl = det.querySelector(".log-detail-body");
        if (sumEl) sumEl.innerHTML = buildLogSummaryHtml(lg);
        if (bodyEl) bodyEl.innerHTML = buildLogBodyHtml(lg);
      }
      const curAtIdx = container.children[idx];
      if (curAtIdx !== det) {
        container.insertBefore(det, curAtIdx || null);
      }
    }
  });

  existingMap.forEach((el, fn) => {
    if (!seenFns.has(fn)) {
      el.remove();
    }
  });
}

async function fetchAndRenderLiveLogs() {
  if (!LOG_VIEW_STEM || LOGS_IN_FLIGHT) return;
  const modal = $("preview-modal");
  if (!modal || modal.classList.contains("hidden")) {
    stopLogsPolling();
    return;
  }
  LOGS_IN_FLIGHT = true;
  try {
    const data = await api(`/api/file/${encodeURIComponent(LOG_VIEW_STEM)}/logs`);
    if (data && data.rpd_status) {
      renderRpdStatus(data.rpd_status);
    }
    if ($("preview-modal-title")) {
      $("preview-modal-title").textContent = `Nhật ký gọi Gemini thời gian thực (Logs) — ${LOG_VIEW_STEM}.docx`;
    }
    ensureLogsModalShell();
    renderLogsToolbar(data);
    renderLogsItems(data);
  } catch (err) {
    // Không đóng modal khi mạng chập chờn
    console.warn("Lỗi tải log AI:", err);
  } finally {
    LOGS_IN_FLIGHT = false;
  }
}

export async function switchLogsViewStem(stem) {
  if (!stem) return;
  LOG_VIEW_STEM = stem;
  LOGS_IN_FLIGHT = false;
  await fetchAndRenderLiveLogs();
}

export async function openLogsModal(targetStem = null) {
  stopLogsPolling();
  setPreviewModalWide(false);
  const fallbackStem = store.FILES && store.FILES.length ? store.FILES[0].file.replace(/\.docx$/i, "") : null;
  LOG_VIEW_STEM = (typeof targetStem === "string" && targetStem) || store.CURRENT_STEM || fallbackStem;
  if (!LOG_VIEW_STEM) {
    showToast("Chưa có file nào để xem log.", true);
    return;
  }

  const body = $("preview-modal-body");
  if (body) body.innerHTML = "";
  ensureLogsModalShell();
  $("preview-modal").classList.remove("hidden");

  await fetchAndRenderLiveLogs();

  // Poll dữ liệu log mới mỗi 600ms khi hộp thoại đang mở
  LOGS_POLL_TIMER = setInterval(fetchAndRenderLiveLogs, 600);

  // Bộ đếm thời gian thực mỗi 200ms cho các cuộc gọi API đang diễn ra (in_progress)
  LOGS_TICK_TIMER = setInterval(() => {
    const modal = $("preview-modal");
    if (!modal || modal.classList.contains("hidden")) {
      stopLogsPolling();
      return;
    }
    const nowSec = Date.now() / 1000;
    document.querySelectorAll(".live-elapsed[data-started]").forEach((el) => {
      const started = Number(el.dataset.started || 0);
      if (started > 0) {
        const elapsed = Math.max(0, nowSec - started).toFixed(1);
        el.textContent = `${elapsed}s`;
      }
    });
  }, 200);
}

export async function initUpdateChecker() {
  const btn = $("btn-self-update");
  const banner = $("update-notification-banner");
  const bannerMsg = $("update-banner-msg");
  const btnBannerUpdate = $("btn-banner-update");
  const btnBannerDismiss = $("btn-banner-dismiss");

  const formatUpdateSummary = (info) => {
    const parts = [`Bản mới: ${info.latest_commit}`];
    if (info.current_commit) parts.push(`hiện tại: ${info.current_commit}`);
    if (info.ahead_by && Number(info.ahead_by) > 1) parts.push(`+${info.ahead_by} commit`);
    let text = `(${parts.join(", ")})`;
    if (info.latest_message) text += ` — "${info.latest_message}"`;
    return text;
  };

  const waitForServerAndReload = async () => {
    const deadline = Date.now() + 18000;
    await new Promise((r) => setTimeout(r, 700));
    while (Date.now() < deadline) {
      try {
        const resp = await fetch("/api/check-update", { cache: "no-store" });
        if (resp.ok) {
          window.location.reload();
          return;
        }
      } catch (_) {
        // Máy chủ đang khởi động lại, chờ tiếp
      }
      await new Promise((r) => setTimeout(r, 400));
    }
    window.location.reload();
  };

  const doUpdate = async () => {
    showToast("Đang tải bản cập nhật mới nhất từ GitHub...");
    if (btn) btn.disabled = true;
    if (btnBannerUpdate) btnBannerUpdate.disabled = true;
    try {
      const res = await api("/api/self-update", { method: "POST", body: "{}" });
      if (!res.ok) {
        throw new Error(res.output || "Cập nhật thất bại.");
      }
      if (banner) banner.classList.add("hidden");
      if (btn) {
        btn.classList.remove("btn-update-available");
        btn.textContent = "🔄 Cập nhật";
      }
      if (res.restarting) {
        showToast("Đã tải bản mới! Đang khởi động lại máy chủ để nạp code Python mới...");
        await waitForServerAndReload();
        return;
      }
      if (res.restart_required) {
        showToast("Đã tải xong! Hãy tắt (tat_server.bat) và mở lại khoi_dong.bat để áp dụng code Python mới.");
        setTimeout(() => window.location.reload(), 1800);
        return;
      }
      showToast(res.output ? res.output.split("\n")[0] : "Đã cập nhật xong! Đang tải lại giao diện...");
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
        const info = await api("/api/check-update?live=1");
        if (info.has_update) {
          btn.classList.add("btn-update-available");
          btn.textContent = "🔔 Có update mới!";
          if (banner && bannerMsg) {
            bannerMsg.textContent = formatUpdateSummary(info);
            banner.classList.remove("hidden");
          }
          await doUpdate();
        } else if (info.status === "offline") {
          showToast("Không thể kết nối đến GitHub để kiểm tra phiên bản.", true);
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
        btn.title = info.latest_message
          ? `Có bản cập nhật mới (${info.latest_commit}): ${info.latest_message}`
          : (info.message || "Có bản cập nhật mới trên GitHub (bấm để tải)");
      }
      if (banner && bannerMsg) {
        bannerMsg.textContent = formatUpdateSummary(info);
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

export async function openVersionsModal(focusVersion = null) {
  stopLogsPolling();
  setPreviewModalWide(false);
  if (!store.CURRENT_STEM) {
    showToast("Hãy chọn một file để xem lịch sử phiên bản.", true);
    return;
  }
  const stem = store.CURRENT_STEM;
  let versions = store.CURRENT_VERSIONS || [];
  try {
    const fresh = await api(`/api/file/${encodeURIComponent(stem)}/versions`);
    if (fresh && Array.isArray(fresh.versions)) {
      versions = fresh.versions;
      store.CURRENT_VERSIONS = versions;
    }
  } catch (_) {
    // Dùng cache hiện tại nếu lỗi mạng tạm thời
  }

  if ($("preview-modal-title")) {
    $("preview-modal-title").textContent = `Lịch sử phiên bản & So sánh — ${stem}.docx`;
  }
  const body = $("preview-modal-body");
  if (!body) return;

  if (!versions.length) {
    body.innerHTML = `<div class="muted" style="padding:16px">Chưa có phiên bản nào được lưu cho file <strong>${escapeHtml(stem)}.docx</strong>. Hãy bấm <strong>💾 Lưu version</strong> hoặc chạy AI để tạo mốc đầu tiên.</div>`;
    $("preview-modal").classList.remove("hidden");
    return;
  }

  const prevTarget = computePrevRollbackTarget();
  let selectedVer =
    focusVersion && versions.some((x) => Number(x.version) === Number(focusVersion))
      ? Number(focusVersion)
      : prevTarget
        ? Number(prevTarget.version)
        : Number(versions[0].version);

  const renderModalContent = () => {
    const v = versions.find((x) => Number(x.version) === selectedVer) || versions[0];
    const vNum = Number(v.version || 0);
    const vTag = `v${String(vNum).padStart(3, "0")}`;
    const curVer = Number((store.CURRENT_STATE || {}).current_version || 0);
    const dirty = hasUnsavedVersionChanges();
    const isExactCurrent = !dirty && curVer > 0 && vNum === curVer;
    const isRunning = isStemBusy(stem);

    const curStyle = ($("sel-style") ? $("sel-style").value : "").trim();
    const curExt = $("txt-extract") ? $("txt-extract").value : "";
    const curAbs = $("txt-abstract") ? $("txt-abstract").value : "";

    const vStyle = ((v.label || {}).value || "").trim();
    const vExt = (v.extract || {}).text || "";
    const vAbs = (v.abstract || {}).text || "";
    const vExtW = (v.extract || {}).words ?? 0;
    const vAbsW = (v.abstract || {}).words ?? 0;
    const chk = v.checks_summary || {};
    const chkLabel = chk.ok === true ? "✓ Đạt chuẩn" : chk.ok === false ? `✕ ${chk.n_errors || 0} lỗi` : "—";
    const srcLabel = VERSION_SOURCE_VI[v.source] || v.source || "Nháp";
    const extraApproveBadge =
      v.approved && v.source !== "approve"
        ? ' <span class="badge ver-badge-src ver-badge-src-approve">✅ Đã duyệt</span>'
        : "";
    const modelTxt = v.model_used || (v.abstract || {}).model_used || (v.extract || {}).model_used || "";

    const pillsHtml = versions
      .map((item) => {
        const iv = Number(item.version || 0);
        const tag = `v${String(iv).padStart(3, "0")}`;
        const activeCls = iv === vNum ? " active" : "";
        const isCur = !dirty && curVer > 0 && iv === curVer;
        const srcShort = VERSION_SOURCE_VI[item.source] || item.source || "";
        const appMark = item.approved && item.source !== "approve" ? " · ✅ Đã duyệt" : "";
        return `<button type="button" class="ver-modal-pill${activeCls}" data-modal-ver="${iv}">
          <strong>${escapeHtml(tag)}</strong>${isCur ? " (đang dùng)" : ""} · <small>${escapeHtml(srcShort)}${appMark} · ${escapeHtml(formatVersionTime(item.saved_at))}</small>
        </button>`;
      })
      .join("");

    const styleSame = vStyle === curStyle;
    const extSame = vExt.trim() === curExt.trim();
    const absSame = vAbs.trim() === curAbs.trim();

    const buildDiffSection = (title, same, oldMeta, oldText, curMeta, curText) => {
      const badge = same
        ? `<span class="badge" style="background:#d1fae5;color:#065f46">Giống bản hiện tại</span>`
        : `<span class="badge" style="background:#fef3c7;color:#92400e">Khác bản hiện tại</span>`;
      if (same) {
        return `
          <div class="ver-diff-section">
            <div class="ver-diff-head"><span>${escapeHtml(title)}</span>${badge}</div>
            <div class="ver-diff-col-label">${escapeHtml(oldMeta)}</div>
            <pre class="ref-pre" style="max-height:170px;overflow-y:auto;margin:0">${escapeHtml(oldText || "(trống)")}</pre>
          </div>
        `;
      }
      return `
        <div class="ver-diff-section">
          <div class="ver-diff-head"><span>${escapeHtml(title)}</span>${badge}</div>
          <div class="ver-diff-grid">
            <div>
              <div class="ver-diff-col-label">${escapeHtml(oldMeta)}</div>
              <pre class="ref-pre" style="max-height:200px;overflow-y:auto;margin:0;background:#fffbeb;border:1px solid #fde68a">${escapeHtml(oldText || "(trống)")}</pre>
            </div>
            <div>
              <div class="ver-diff-col-label">${escapeHtml(curMeta)}</div>
              <pre class="ref-pre" style="max-height:200px;overflow-y:auto;margin:0;background:#f8fafc;border:1px solid #cbd5e1">${escapeHtml(curText || "(trống)")}</pre>
            </div>
          </div>
        </div>
      `;
    };

    const judgeHtml =
      v.judge && v.judge.average !== undefined
        ? `<div class="ver-diff-section">
             <div class="ver-diff-head"><span>Điểm G-Eval của ${escapeHtml(vTag)}</span><span><strong>${v.judge.average} / 5.0</strong></span></div>
             <div class="muted">${escapeHtml(v.judge.summary || "")}</div>
           </div>`
        : "";

    body.innerHTML = `
      <div class="ver-modal-pills">${pillsHtml}</div>
      <div class="ver-modal-toolbar">
        <div>
          <strong>${escapeHtml(vTag)}</strong>
          ${isExactCurrent ? '<span class="badge ver-badge-cur">Đang dùng</span>' : ""}
          · <span class="badge ver-badge-src">${escapeHtml(srcLabel)}</span>${extraApproveBadge}
          · <span>${escapeHtml(formatVersionTime(v.saved_at))}</span>
          · <span>${escapeHtml(chkLabel)}</span>
          ${modelTxt ? `· <span>🤖 ${escapeHtml(modelTxt)}</span>` : ""}
          ${v.note ? `<div class="muted small-mt">Ghi chú: ${escapeHtml(v.note)}</div>` : ""}
        </div>
        <button type="button" id="btn-modal-restore-ver" class="btn btn-primary btn-sm" ${isRunning || isExactCurrent ? "disabled" : ""}>
          ${isExactCurrent ? "Đang ở bản này" : `↩ Rollback về ${escapeHtml(vTag)}`}
        </button>
      </div>
      <div class="ver-diff-section">
        <div class="ver-diff-head">
          <span>1. Phong cách chức năng</span>
          ${styleSame ? '<span class="badge" style="background:#d1fae5;color:#065f46">Giống hiện tại</span>' : '<span class="badge" style="background:#fef3c7;color:#92400e">Khác hiện tại</span>'}
        </div>
        <div>
          <strong>${escapeHtml(vTag)}:</strong> ${escapeHtml(vStyle || "(chưa gán nhãn)")}
          ${styleSame ? "" : ` &nbsp;|&nbsp; <strong>Hiện tại:</strong> ${escapeHtml(curStyle || "(chưa gán nhãn)")}`}
        </div>
      </div>
      ${buildDiffSection("2. Bản trích chọn", extSame, `${vTag} (${vExtW} từ)`, vExt, "Hiện tại trên màn hình", curExt)}
      ${buildDiffSection("3. Bản biên soạn", absSame, `${vTag} (${vAbsW} từ)`, vAbs, "Hiện tại trên màn hình", curAbs)}
      ${judgeHtml}
    `;

    body.querySelectorAll("button[data-modal-ver]").forEach((btn) => {
      btn.addEventListener("click", () => {
        selectedVer = Number(btn.dataset.modalVer);
        renderModalContent();
      });
    });
    const btnRestore = $("btn-modal-restore-ver");
    if (btnRestore) {
      btnRestore.addEventListener("click", async () => {
        btnRestore.disabled = true;
        const ok = await restoreVersion(vNum);
        if (ok) {
          closePreviewModal();
        } else {
          btnRestore.disabled = false;
        }
      });
    }
  };

  renderModalContent();
  $("preview-modal").classList.remove("hidden");
}

registerPreviewVersionHandler((ver) => openVersionsModal(ver));

function rpdStatusIcon(st) {
  if (st === "available") return "🟢";
  if (st === "cooldown") return "⏳";
  if (st === "warning_429") return "🟡";
  if (st === "invalid_key") return "⛔";
  return "🔴";
}

function buildRpdProbeBannerHtml(banner) {
  if (!banner) return "";
  const cls = banner.tone === "ok" ? "is-ok" : banner.tone === "err" ? "is-err" : "is-warn";
  const errDetail = banner.error
    ? `<div style="margin-top:4px;font-family:Consolas,monospace;font-size:11.5px;white-space:pre-wrap">${escapeHtml(banner.error)}</div>`
    : "";
  return `<div class="rpd-probe-banner ${cls}">
    <strong>${escapeHtml(banner.title || "Kết quả kiểm tra")}</strong>: ${escapeHtml(banner.message || "")}${errDetail}
  </div>`;
}

export async function openRpdModal(initialModel = "ALL") {
  stopLogsPolling();
  setPreviewModalWide(true);
  if ($("preview-modal-title")) {
    $("preview-modal-title").textContent = "Trạng thái RPD từng Model & từng API Key (Đối chiếu Google AI Studio)";
  }
  const body = $("preview-modal-body");
  if (!body) return;
  body.innerHTML = `<div class="muted" style="padding:18px">Đang tải dữ liệu trạng thái RPD từng key...</div>`;
  $("preview-modal").classList.remove("hidden");

  let selectedModel = initialModel || "ALL";
  let filterMode = "all";
  let searchQuery = "";
  let rpdData = null;
  let probeBanner = null;
  let busyOp = false;

  const matchesFilter = (cell) => {
    if (!cell) return false;
    if (filterMode === "exhausted") return Boolean(cell.exhausted);
    if (filterMode === "warn") return cell.status === "warning_429" || cell.status === "cooldown";
    if (filterMode === "available") return !cell.exhausted;
    if (filterMode === "used") return Number(cell.used || 0) > 0;
    return true;
  };

  const renderContent = () => {
    if (!rpdData) return;
    const models = rpdData.models || [];
    const keys = rpdData.keys || [];
    if (selectedModel !== "ALL" && !models.some((m) => m.model === selectedModel)) {
      selectedModel = "ALL";
    }

    const qLow = searchQuery.trim().toLowerCase();
    const filteredKeys = keys.filter((kRow) => {
      if (qLow) {
        const hay = `${kRow.index} ${kRow.key_tail} ${kRow.key_label}`.toLowerCase();
        if (!hay.includes(qLow)) return false;
      }
      if (selectedModel === "ALL") {
        if (filterMode === "all") return true;
        return models.some((m) => matchesFilter(kRow.models[m.model]));
      }
      return matchesFilter(kRow.models[selectedModel]);
    });

    const totalExhAll = keys.reduce(
      (acc, kRow) => acc + models.filter((m) => (kRow.models[m.model] || {}).exhausted).length,
      0
    );
    const selModelInfo = models.find((m) => m.model === selectedModel) || null;
    const selExhCount = selModelInfo ? Number(selModelInfo.exhausted_keys || 0) : totalExhAll;

    const tabsHtml = [
      `<button type="button" class="rpd-tab-btn${selectedModel === "ALL" ? " active" : ""}" data-rpd-tab="ALL">
        <strong>📋 Tất cả model (Ma trận)</strong>
        <small>${keys.length} key · ${totalExhAll} cặp đang khóa</small>
      </button>`,
      ...models.map((m) => {
        const activeCls = selectedModel === m.model ? " active" : "";
        const qpsTabTxt = m.qps_429_used > 0 ? ` (+${m.qps_429_used} QPS)` : "";
        return `<button type="button" class="rpd-tab-btn${activeCls}" data-rpd-tab="${escapeHtml(m.model)}">
          <strong>${escapeHtml(m.model)}</strong>
          <small>Còn ${m.available_keys}/${keys.length} key · OK:${m.ok_used} · RPD:${m.used}/${m.limit}${escapeHtml(qpsTabTxt)}</small>
        </button>`;
      }),
    ].join("");

    let tableHtml = "";
    if (!keys.length) {
      tableHtml = `<div class="muted" style="padding:14px">Chưa cấu hình API key nào trong <code>api_keys.txt</code> hoặc biến môi trường.</div>`;
    } else if (!filteredKeys.length) {
      tableHtml = `<div class="muted" style="padding:14px">Không có key nào khớp bộ lọc hiện tại.</div>`;
    } else if (selectedModel === "ALL") {
      const headCols = models
        .map(
          (m) =>
            `<th>${escapeHtml(m.model)}<br><small style="font-weight:500;color:#64748b">${m.rpd_per_key}/key · còn ${m.available_keys}/${keys.length}</small></th>`
        )
        .join("");
      const rowsHtml = filteredKeys
        .map((kRow) => {
          const cells = models
            .map((m) => {
              const c = kRow.models[m.model] || {};
              const icon = rpdStatusIcon(c.status);
              const trTxt = c.transient_used > 0 ? ` (+${c.transient_used} lỗi)` : "";
              const qpsTxt = c.qps_429_count > 0 ? ` · +${c.qps_429_count} QPS` : "";
              const strikeTxt = c.strikes_429 > 0 && !c.exhausted ? ` · 429:${c.strikes_429}/3` : "";
              const reasonLine = c.exhausted && c.reason_vi ? `<div style="font-size:10.5px;color:#991b1b">${escapeHtml(c.reason_vi)}</div>` : "";
              const totalGoogleLine =
                c.qps_429_count > 0
                  ? `<div style="font-size:10.5px;color:#64748b">Gửi Google (gồm QPS): <strong>${c.total_sent_with_qps || c.used || 0}</strong></div>`
                  : "";
              return `<td>
                <div class="rpd-matrix-cell" data-drill-model="${escapeHtml(m.model)}" title="Bấm để xem chi tiết ${escapeHtml(m.model)}">
                  <div><span class="rpd-st-pill rpd-st-${escapeHtml(c.status || "available")}">${icon} ${escapeHtml(c.status_vi || "Còn RPD")}</span></div>
                  <div style="margin-top:3px;font-family:Consolas,monospace;font-size:11.5px">
                    OK:<strong>${c.ok_used || 0}</strong> · RPD:<strong>${c.used || 0}/${c.rpd_limit || m.rpd_per_key}</strong>${escapeHtml(trTxt)}${escapeHtml(qpsTxt)}${escapeHtml(strikeTxt)}
                  </div>
                  ${totalGoogleLine}
                  ${reasonLine}
                </div>
              </td>`;
            })
            .join("");
          const keyQpsNote =
            kRow.total_qps_429 > 0
              ? ` (+${kRow.total_qps_429} QPS = ${kRow.total_sent_with_qps} gửi)`
              : "";
          return `<tr>
            <td class="rpd-key-code">${escapeHtml(kRow.key_label)}<div style="font-weight:400;font-size:11px;color:#64748b">Chu kỳ PT: ${kRow.total_ok_used} OK / ${kRow.total_used} RPD${escapeHtml(keyQpsNote)}</div></td>
            ${cells}
          </tr>`;
        })
        .join("");
      tableHtml = `<div class="rpd-table-wrap"><table class="rpd-table">
        <thead><tr><th>API Key</th>${headCols}</tr></thead>
        <tbody>${rowsHtml}</tbody>
      </table></div>`;
    } else {
      const rowsHtml = filteredKeys
        .map((kRow) => {
          const c = kRow.models[selectedModel] || {};
          const icon = rpdStatusIcon(c.status);
          const lim = Number(c.rpd_limit || 20);
          const pct = lim > 0 ? Math.min(100, Math.round((Number(c.used || 0) / lim) * 100)) : 0;
          const barCls = pct >= 100 ? " is-full" : pct >= 80 ? " is-warn" : "";
          const trParts = [];
          if (c.err_503_used) trParts.push(`503:${c.err_503_used}`);
          if (c.err_timeout_used) trParts.push(`TO:${c.err_timeout_used}`);
          if (c.cancelled_used) trParts.push(`Hủy:${c.cancelled_used}`);
          const trDetail = trParts.length ? ` (${trParts.join(", ")})` : "";
          const errInfo = c.reason_vi
            ? `<div><strong style="color:#991b1b">${escapeHtml(c.reason_vi)}</strong></div>`
            : "";
          const rawErr = c.last_error
            ? `<div class="rpd-err-snippet">${escapeHtml(c.last_error)}</div>`
            : `<span class="muted">—</span>`;
          const timeLine = c.last_call_at
            ? `<div class="muted" style="font-size:11px">Gần nhất: ${escapeHtml(c.last_call_at)}</div>`
            : "";
          const unlockBtn =
            c.exhausted || c.strikes_429 > 0 || c.cooldown_remaining_s > 0
              ? `<button type="button" class="btn btn-sm" data-unlock-key="${escapeHtml(kRow.key_id)}" data-unlock-model="${escapeHtml(selectedModel)}" title="Mở khóa trạng thái hết RPD/cooldown cho key này (không tốn request)" ${busyOp ? "disabled" : ""}>🔓 Mở</button>`
              : "";
          const probeBtn = `<button type="button" class="btn btn-sm" data-probe-key="${escapeHtml(kRow.key_id)}" data-probe-model="${escapeHtml(selectedModel)}" title="Gửi 1 request nhỏ kiểm tra thực tế với Google API (tốn 1 RPD nếu key còn hạn mức)" ${busyOp || rpdData.mock ? "disabled" : ""}>🧪 Test</button>`;

          return `<tr>
            <td class="rpd-key-code">${escapeHtml(kRow.key_label)}</td>
            <td><span class="rpd-st-pill rpd-st-${escapeHtml(c.status || "available")}">${icon} ${escapeHtml(c.status_vi || "Còn RPD")}</span></td>
            <td><strong style="color:#047857">${c.ok_used || 0}</strong> <span class="muted">(ước còn ${c.remaining_ok_est ?? lim})</span></td>
            <td>${c.transient_used || 0}${escapeHtml(trDetail)}</td>
            <td>
              <strong>${c.used || 0}</strong> / ${lim}
              <div class="rpd-mini-bar${barCls}"><span style="width:${pct}%"></span></div>
              ${c.qps_429_count > 0 ? `<div class="muted" style="font-size:11px">Gửi Google (gồm QPS): <strong>${c.total_sent_with_qps || 0}</strong></div>` : ""}
            </td>
            <td>Strike: <strong>${c.strikes_429 || 0}/3</strong><br><span class="muted">QPS: ${c.qps_429_count || 0}</span></td>
            <td>${errInfo}${rawErr}${timeLine}</td>
            <td><div class="rpd-row-actions">${probeBtn}${unlockBtn}</div></td>
          </tr>`;
        })
        .join("");

      tableHtml = `<div class="rpd-table-wrap"><table class="rpd-table">
        <thead>
          <tr>
            <th>API Key</th>
            <th>Trạng thái trong code</th>
            <th title="Số lượt phản hồi thành công (200 OK) — sát nhất với RPD trên Google AI Studio">Thành công (200)</th>
            <th title="Các lỗi 503 high demand / Timeout / Hủy giữa chừng mà Google AI Studio có thể không trừ RPD">503 / Timeout / Hủy</th>
            <th>Tổng gửi / Cấu hình</th>
            <th>429 (Strike / QPS)</th>
            <th>Lý do khóa / Lỗi gần nhất</th>
            <th>Thao tác</th>
          </tr>
        </thead>
        <tbody>${rowsHtml}</tbody>
      </table></div>`;
    }

    const probeExhaustedBtn =
      selectedModel !== "ALL" && selExhCount > 0 && !rpdData.mock
        ? `<button type="button" id="btn-rpd-probe-exh" class="btn btn-sm" ${busyOp ? "disabled" : ""}>🧪 Test ${selExhCount} key đang khóa</button>`
        : "";
    const unlockBulkBtn =
      selExhCount > 0
        ? `<button type="button" id="btn-rpd-unlock-bulk" class="btn btn-sm" ${busyOp ? "disabled" : ""}>🔓 Mở khóa ${selectedModel === "ALL" ? `tất cả (${selExhCount})` : `${selExhCount} key`}</button>`
        : "";

    body.innerHTML = `
      <div class="rpd-modal-banner">
        <div style="display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;align-items:center">
          <div>
            <strong>Chu kỳ ngày (Pacific Time):</strong> <code>${escapeHtml(rpdData.cycle_date || "")}</code>
            · <strong>Reset tiếp theo:</strong> ${escapeHtml(rpdData.next_reset_local || "15:00 giờ VN")}
            · <strong>Tổng key:</strong> ${keys.length}
            ${rpdData.mock ? ' · <span class="badge">Đang chạy --mock</span>' : ""}
          </div>
          <div class="muted">Hiển thị ${filteredKeys.length}/${keys.length} key</div>
        </div>
        <details class="rpd-modal-guide">
          <summary style="cursor:pointer;font-weight:600;color:#1e40af">ℹ️ Vì sao số RPD/RPM trong code có thể khác Google AI Studio Rate Limit?</summary>
          <ul>
            <li><strong>Cửa sổ thời gian khác nhau:</strong> Google AI Studio hiển thị <em>"Peak usage ... over the last 1 day"</em> (<strong>24 giờ trượt gần nhất</strong>, gồm cả lượt trước mốc <code>15:00 VN</code> hoặc log cũ đã xóa), còn Web đếm theo <strong>chu kỳ ngày <code>00:00 Pacific Time</code></strong>.</li>
            <li><strong>Lỗi <code>429 QPS</code> &amp; <code>503</code>:</strong> Google AI Studio vẫn có thể cộng cả <code>429 QPS</code> (nếu gửi trùng payload) và một phần <code>503</code> vào biểu đồ RPM/RPD. Web tách riêng <strong>Thành công (200)</strong>, <strong>503/Timeout/Hủy</strong> và <strong>+QPS / Gửi Google (gồm QPS)</strong> để dễ đối chiếu.</li>
            <li><strong>Kiểm tra trùng Google Cloud Project:</strong> Nếu nhiều API key trong <code>api_keys.txt</code> vô tình tạo chung <strong>1 Project</strong> trên AI Studio (xem cột <em>Project</em> ở trang API Keys), Google sẽ <strong>cộng gộp RPM và RPD của mọi key thuộc Project đó</strong> — dễ làm RPM vượt 5 và RPD tăng gấp bội dù mỗi key chỉ gửi 1–2 lượt.</li>
            <li><strong>Kiểm tra / Mở khóa:</strong> Bấm <strong>🔓 Mở</strong> để bỏ đánh dấu khóa (miễn phí RPD), hoặc bấm <strong>🧪 Test</strong> để gọi thử 1 request nhỏ tới Google (tuân thủ pacing RPM) xác nhận trạng thái thật.</li>
          </ul>
        </details>
      </div>
      <div class="rpd-model-tabs">${tabsHtml}</div>
      ${buildRpdProbeBannerHtml(probeBanner)}
      <div class="rpd-modal-toolbar">
        <div class="rpd-modal-filters">
          <input id="rpd-search-input" type="text" value="${escapeHtml(searchQuery)}" placeholder="Tìm Key # hoặc 4 ký tự cuối...">
          <select id="rpd-filter-select">
            <option value="all"${filterMode === "all" ? " selected" : ""}>Tất cả trạng thái</option>
            <option value="exhausted"${filterMode === "exhausted" ? " selected" : ""}>🔴 Đang bị khóa / Hết RPD</option>
            <option value="warn"${filterMode === "warn" ? " selected" : ""}>🟡 Cảnh báo 429 / Cooldown</option>
            <option value="available"${filterMode === "available" ? " selected" : ""}>🟢 Còn RPD</option>
            <option value="used"${filterMode === "used" ? " selected" : ""}>Đã gọi &ge; 1 lượt</option>
          </select>
        </div>
        <div class="rpd-modal-actions">
          <button type="button" id="btn-rpd-refresh" class="btn btn-sm" ${busyOp ? "disabled" : ""}>🔄 Làm mới</button>
          ${probeExhaustedBtn}
          ${unlockBulkBtn}
        </div>
      </div>
      ${tableHtml}
    `;

    body.querySelectorAll("button[data-rpd-tab]").forEach((btn) => {
      btn.addEventListener("click", () => {
        selectedModel = btn.dataset.rpdTab;
        renderContent();
      });
    });
    body.querySelectorAll("[data-drill-model]").forEach((el) => {
      el.addEventListener("click", () => {
        selectedModel = el.dataset.drillModel;
        renderContent();
      });
    });
    const searchEl = $("rpd-search-input");
    if (searchEl) {
      searchEl.addEventListener("input", () => {
        searchQuery = searchEl.value;
        const pos = searchEl.selectionStart || searchQuery.length;
        renderContent();
        const nextInput = $("rpd-search-input");
        if (nextInput) {
          nextInput.focus();
          nextInput.setSelectionRange(pos, pos);
        }
      });
    }
    const filterEl = $("rpd-filter-select");
    if (filterEl) {
      filterEl.addEventListener("change", () => {
        filterMode = filterEl.value;
        renderContent();
      });
    }
    const refreshBtn = $("btn-rpd-refresh");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => refreshData());
    }
    const unlockBulk = $("btn-rpd-unlock-bulk");
    if (unlockBulk) {
      unlockBulk.addEventListener("click", async () => {
        const targetScope = selectedModel === "ALL" ? "tất cả model" : `model ${selectedModel}`;
        if (!window.confirm(`Mở khóa trạng thái hết RPD/cooldown cho ${targetScope}?`)) return;
        await doUnlock(null, selectedModel === "ALL" ? null : selectedModel);
      });
    }
    const probeExhBtn = $("btn-rpd-probe-exh");
    if (probeExhBtn && selModelInfo) {
      probeExhBtn.addEventListener("click", async () => {
        const targets = keys.filter((kRow) => (kRow.models[selectedModel] || {}).exhausted);
        if (!targets.length) return;
        if (
          !window.confirm(
            `Sẽ gửi lần lượt ${targets.length} request nhỏ để kiểm tra các key đang bị khóa của ${selectedModel}.\n` +
              `- Key nào thật sự hết RPD -> Google trả 429 (không tốn thêm RPD).\n` +
              `- Key nào thực chất vẫn còn -> tốn 1 RPD để xác nhận và tự động mở khóa.\nTiếp tục?`
          )
        ) {
          return;
        }
        busyOp = true;
        let okCount = 0;
        let failCount = 0;
        for (let i = 0; i < targets.length; i += 1) {
          const kRow = targets[i];
          probeBanner = {
            tone: "warn",
            title: `Đang kiểm tra (${i + 1}/${targets.length})`,
            message: `Đang test ${kRow.key_label} trên ${selectedModel}...`,
          };
          renderContent();
          try {
            const res = await api("/api/rpd/probe", {
              method: "POST",
              body: JSON.stringify({ key_id: kRow.key_id, model: selectedModel }),
            });
            if (res.details) rpdData = { ...rpdData, ...res.details };
            if (res.rpd_status) renderRpdStatus(res.rpd_status);
            if (res.unlocked) okCount += 1;
            else failCount += 1;
          } catch (err) {
            failCount += 1;
          }
        }
        busyOp = false;
        probeBanner = {
          tone: okCount > 0 ? "ok" : "warn",
          title: `Hoàn tất kiểm tra ${targets.length} key trên ${selectedModel}`,
          message: `${okCount} key vẫn còn RPD và đã được mở khóa; ${failCount} key vẫn bị từ chối/429.`,
        };
        renderContent();
      });
    }

    body.querySelectorAll("button[data-unlock-key]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await doUnlock(btn.dataset.unlockKey, btn.dataset.unlockModel);
      });
    });
    body.querySelectorAll("button[data-probe-key]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await doProbeOne(btn.dataset.probeKey, btn.dataset.probeModel);
      });
    });
  };

  const refreshData = async () => {
    busyOp = true;
    try {
      const data = await api("/api/rpd/keys");
      rpdData = data;
      if (data && data.rpd_status) {
        renderRpdStatus(data.rpd_status);
      }
    } catch (err) {
      showToast(err.message, true);
    } finally {
      busyOp = false;
      renderContent();
    }
  };

  const doUnlock = async (keyId, modelName) => {
    busyOp = true;
    renderContent();
    try {
      const res = await api("/api/rpd/unlock", {
        method: "POST",
        body: JSON.stringify({ key_id: keyId || "", model: modelName || "" }),
      });
      if (res.details) rpdData = { ...rpdData, ...res.details };
      if (res.rpd_status) renderRpdStatus(res.rpd_status);
      probeBanner = {
        tone: "ok",
        title: "Đã mở khóa",
        message: `Đã xóa đánh dấu khóa/cooldown (${res.unlocked || 0} mục).`,
      };
      showToast("Đã mở khóa trạng thái RPD.");
    } catch (err) {
      showToast(err.message, true);
    } finally {
      busyOp = false;
      renderContent();
    }
  };

  const doProbeOne = async (keyId, modelName) => {
    busyOp = true;
    probeBanner = {
      tone: "warn",
      title: "Đang kiểm tra API thật...",
      message: `Đang gửi request thử tới Google cho ${modelName}...`,
    };
    renderContent();
    try {
      const res = await api("/api/rpd/probe", {
        method: "POST",
        body: JSON.stringify({ key_id: keyId, model: modelName }),
      });
      if (res.details) rpdData = { ...rpdData, ...res.details };
      if (res.rpd_status) renderRpdStatus(res.rpd_status);
      const tone = res.outcome === "available" ? "ok" : res.outcome === "exhausted_rpd" || res.outcome === "invalid_key" ? "err" : "warn";
      probeBanner = {
        tone,
        title: res.status_vi || "Kết quả kiểm tra",
        message: res.message || "",
        error: res.error || "",
      };
    } catch (err) {
      probeBanner = {
        tone: "err",
        title: "Không thể kiểm tra",
        message: err.message,
      };
    } finally {
      busyOp = false;
      renderContent();
    }
  };

  await refreshData();
}
