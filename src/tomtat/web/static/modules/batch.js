// Tải lên file .docx, chạy AI hàng loạt, và theo dõi tiến độ qua SSE (kèm fallback polling)
import {
  STEP_NAMES_VI,
  applyRerunOutcome,
  registerEnsureTrackingHandler,
  selectFile,
  syncRerunButtonsState,
} from "./editor.js";
import {
  loadFileList,
  pendingFiles,
  renderFileList,
  renderRpdStatus,
  syncPoolScalingUI,
  syncRunningProgressBar,
} from "./sidebar.js";
import { isStemBusy, store } from "./state.js";
import { $, api, showToast } from "./utils.js";

let BATCH_POLL_TIMER = null; // dự phòng polling khi trình duyệt/kết nối không hỗ trợ SSE
let BATCH_EVENTS = null;     // EventSource đang mở tới /api/batch/events
let POLL_IN_FLIGHT = false;
let LAST_BATCH_DONE = -1;
let LAST_BATCH_ERR = -1;
let LAST_CURRENT_UPDATED = "";
let LAST_RUNNING_KEY = "";
let LAST_QUEUED_KEY = "";
let WAS_BATCH_ACTIVE = false;

export async function uploadFiles(fileList, overwrite = false) {
  const filesArr = Array.from(fileList || []);
  if (!filesArr.length) return;
  filesArr.sort((a, b) => {
    const cmp = (a.name || "").localeCompare(b.name || "", undefined, { numeric: true, sensitivity: "base" });
    return store.SORT_ORDER === "desc" ? -cmp : cmp;
  });
  $("file-upload-input").value = "";

  const runNow = $("chk-run-on-upload") ? $("chk-run-on-upload").checked : false;
  const useMock = $("chk-mock") ? $("chk-mock").checked : false;

  const fd = new FormData();
  for (const f of filesArr) {
    fd.append("files", f);
  }
  fd.append("run_now", "false");
  fd.append("mock", useMock ? "true" : "false");
  fd.append("overwrite", overwrite ? "true" : "false");

  showToast(`Đang thêm ${filesArr.length} file vào danh sách...`);
  try {
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error || `Lỗi HTTP ${res.status}`);
    }
    if (data.need_overwrite) {
      if (window.confirm(data.message)) {
        await uploadFiles(filesArr, true);
      } else {
        await loadFileList(true);
      }
      return;
    }

    await loadFileList(true);
    if (data.stems && data.stems.length > 0) {
      await selectFile(data.stems[0]);
    }

    if (runNow && data.stems && data.stems.length > 0) {
      const addedSet = new Set(data.stems);
      const orderedStems = pendingFiles()
        .map((r) => r.file.replace(/\.docx$/i, ""))
        .filter((s) => addedSet.has(s));
      const stemsToRun = orderedStems.length
        ? orderedStems
        : [...data.stems].sort((a, b) => {
            const cmp = a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
            return store.SORT_ORDER === "desc" ? -cmp : cmp;
          });
      await runBatchOnServer(stemsToRun, useMock);
    } else if (data.errors && data.errors.length > 0) {
      showToast(`Đã thêm (${data.added.length} file), có lưu ý: ${data.errors[0]}`, true);
    } else {
      showToast(`Đã thêm ${data.added.length} file vào cột Chờ duyệt.`);
    }
  } catch (err) {
    showToast(err.message, true);
  }
}

export function setBatchProgress(text) {
  syncRunningProgressBar(text || "");
}

export async function runBatchOnServer(stems, useMock) {
  if (!stems || !stems.length) return;
  const selEl = $("sel-concurrency");
  const rawVal = selEl ? selEl.value : "auto";
  const concurrencyVal = rawVal === "auto" ? "auto" : Number(rawVal) || "auto";

  try {
    const res = await api("/api/batch/start", {
      method: "POST",
      body: JSON.stringify({
        stems,
        concurrency: concurrencyVal,
        mock: useMock,
      }),
    });
    if (!res.ok) {
      showToast(res.message || "Không thể khởi động batch", true);
      return;
    }
    WAS_BATCH_ACTIVE = true;
    showToast(res.message);
    startBatchPolling();
  } catch (err) {
    showToast(err.message, true);
  }
}

export async function stopBatch() {
  for (const s of store.RUNNING_STEMS) {
    store.STOPPING_STEMS.add(s);
  }
  for (const s of store.QUEUED_STEMS) {
    store.STOPPING_STEMS.add(s);
  }
  if (store.BATCH_ACTIVE) {
    store.BATCH_STOPPED = true;
  }
  renderFileList();
  syncRunningProgressBar();
  syncRerunButtonsState();
  try {
    const res = await api("/api/batch/stop", { method: "POST" });
    showToast(res.message || "Đã gửi yêu cầu dừng.", "warn");
  } catch (err) {
    for (const s of Array.from(store.STOPPING_STEMS)) {
      store.STOPPING_STEMS.delete(s);
    }
    store.BATCH_STOPPED = false;
    renderFileList();
    syncRunningProgressBar();
    syncRerunButtonsState();
    showToast(err.message, true);
  }
}

export function stopBatchTracking() {
  if (BATCH_EVENTS) {
    BATCH_EVENTS.close();
    BATCH_EVENTS = null;
  }
  if (BATCH_POLL_TIMER) {
    clearInterval(BATCH_POLL_TIMER);
    BATCH_POLL_TIMER = null;
  }
  POLL_IN_FLIGHT = false;
}

export function ensureBatchStatusTracking() {
  if (BATCH_EVENTS || BATCH_POLL_TIMER) return;
  startBatchPollingFallback();
}

registerEnsureTrackingHandler(ensureBatchStatusTracking);

export async function handleBatchStatus(st) {
  if (!st) return;

  if (st.pool_scaling) {
    syncPoolScalingUI(st.pool_scaling);
  }
  if (st.allowed_workers) {
    store.ALLOWED_WORKERS = Number(st.allowed_workers);
  }
  if (st.rpd_status) {
    renderRpdStatus(st.rpd_status);
  }

  const prevQueued = new Set(store.QUEUED_STEMS);
  const wasCurrentRunning = Boolean(store.CURRENT_STEM && store.RUNNING_STEMS.has(store.CURRENT_STEM));
  const wasCurrentBusy = Boolean(store.CURRENT_STEM && isStemBusy(store.CURRENT_STEM));

  const serverRunning = [...(st.running || []), ...(st.rerun_running || [])];
  const serverSteps = { ...(st.rerun_steps || {}), ...(st.running_steps || {}) };
  store.RUNNING_STEMS = new Set([...serverRunning, ...store.LOCAL_RERUN_STEMS]);
  const srvQueued = Array.isArray(st.queued_rerun_stems) ? st.queued_rerun_stems : [];
  const srvQueuedSteps = st.queued_rerun_steps || {};
  const srvQueuedPos = st.queued_rerun_positions || {};
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

  const srvStopping = new Set(st.stopping_stems || []);
  for (const s of Array.from(store.STOPPING_STEMS)) {
    if (!store.RUNNING_STEMS.has(s) && !store.QUEUED_STEMS.has(s)) store.STOPPING_STEMS.delete(s);
  }
  for (const s of srvStopping) {
    if (store.RUNNING_STEMS.has(s) || store.QUEUED_STEMS.has(s)) store.STOPPING_STEMS.add(s);
  }
  const nextSteps = {};
  for (const s of store.RUNNING_STEMS) {
    nextSteps[s] = serverSteps[s] || store.RUNNING_STEPS[s] || "";
  }
  store.RUNNING_STEPS = nextSteps;
  store.BATCH_ACTIVE = Boolean(st.active);
  store.BATCH_STOPPED = Boolean(st.active && st.stopped);

  // Thông báo khi một file đang chờ lượt bắt đầu được đẩy vào luồng chạy
  for (const [rStem, pend] of store.PENDING_RERUN_JOBS.entries()) {
    if (
      pend &&
      !pend.notifiedStart &&
      prevQueued.has(rStem) &&
      store.RUNNING_STEMS.has(rStem)
    ) {
      pend.notifiedStart = true;
      showToast(`▶ Đến lượt: Đang chạy '${pend.labelVi}' cho ${rStem}.docx...`);
    }
  }

  // Thu nhận kết quả các lượt chạy lại AI đơn lẻ vừa hoàn tất / bị hủy
  const recentResults = st.recent_rerun_results || {};
  const completedReruns = [];
  for (const [rStem, res] of Object.entries(recentResults)) {
    if (!res || !res.job_id || store.SEEN_RERUN_JOB_IDS.has(res.job_id)) continue;
    if (store.RUNNING_STEMS.has(rStem) || store.QUEUED_STEMS.has(rStem)) continue;
    const pend = store.PENDING_RERUN_JOBS.get(rStem);
    if (pend && pend.jobId === res.job_id) {
      store.SEEN_RERUN_JOB_IDS.add(res.job_id);
      store.PENDING_RERUN_JOBS.delete(rStem);
      completedReruns.push({ stem: rStem, pend, res });
    }
  }

  const runningKey = Array.from(store.RUNNING_STEMS)
    .map((s) => `${s}:${store.RUNNING_STEPS[s] || ""}`)
    .sort()
    .join("|");
  const queuedKey = Array.from(store.QUEUED_STEMS)
    .map((s) => `${s}:${store.QUEUED_POSITIONS[s] || ""}:${store.QUEUED_STEPS[s] || ""}`)
    .sort()
    .join("|");

  if (st.active) {
    WAS_BATCH_ACTIVE = true;
    const activeAll = st.all_running && st.all_running.length ? st.all_running : (st.running || []);
    const activeSteps = st.all_running_steps || st.running_steps || {};
    const activeList = activeAll
      .map((s) => {
        const sc = activeSteps[s];
        const sv = STEP_NAMES_VI[sc] || sc || "";
        return sv ? `${s}.docx (${sv})` : `${s}.docx`;
      })
      .join(", ");
    const queuedList = (st.queued_reruns || [])
      .map((q) => {
        const sv = STEP_NAMES_VI[q.step] || q.step || "";
        return sv ? `${q.stem}.docx (${sv})` : `${q.stem}.docx`;
      })
      .join(", ");
    const maxW = Number(st.allowed_workers || store.ALLOWED_WORKERS || 0);
    const slotText = maxW > 0 ? `${store.RUNNING_STEMS.size}/${maxW}` : `${store.RUNNING_STEMS.size}`;
    const queuedSuffix = queuedList ? ` · Chờ lượt: ${queuedList}` : "";
    setBatchProgress(
      `⏳ Đang chạy song song ${slotText} luồng (${st.done_count + st.err_count}/${st.total} xong) — Đang xử lý: ${activeList || "..."}${queuedSuffix}`
    );
    syncRerunButtonsState();

    const curUpdated = store.CURRENT_STEM ? ((st.running_updated || {})[store.CURRENT_STEM] || "") : "";
    const isCurrentRunning = Boolean(store.CURRENT_STEM && store.RUNNING_STEMS.has(store.CURRENT_STEM));
    const isCurrentBusy = Boolean(store.CURRENT_STEM && isStemBusy(store.CURRENT_STEM));
    const countsChanged = st.done_count !== LAST_BATCH_DONE || st.err_count !== LAST_BATCH_ERR;
    const currentStepUpdated = Boolean(curUpdated && curUpdated !== LAST_CURRENT_UPDATED);
    const currentFinishedJustNow = (wasCurrentRunning && !isCurrentRunning) || (wasCurrentBusy && !isCurrentBusy);
    const currentRerunFinished = Boolean(
      store.CURRENT_STEM && completedReruns.some((item) => item.stem === store.CURRENT_STEM)
    );

    if (curUpdated) {
      LAST_CURRENT_UPDATED = curUpdated;
    }

    if (countsChanged || currentStepUpdated || currentFinishedJustNow || completedReruns.length > 0) {
      LAST_BATCH_DONE = st.done_count;
      LAST_BATCH_ERR = st.err_count;
      LAST_RUNNING_KEY = runningKey;
      LAST_QUEUED_KEY = queuedKey;
      await loadFileList(true);
      if (store.CURRENT_STEM && (currentStepUpdated || currentFinishedJustNow || currentRerunFinished)) {
        await selectFile(store.CURRENT_STEM, {
          silent: true,
          onlyIfCurrent: true,
          skipSidebarRender: true,
        });
      }
    } else if (runningKey !== LAST_RUNNING_KEY || queuedKey !== LAST_QUEUED_KEY) {
      LAST_RUNNING_KEY = runningKey;
      LAST_QUEUED_KEY = queuedKey;
      renderFileList();
    }
    for (const item of completedReruns) {
      applyRerunOutcome(item.stem, item.pend.step, item.pend.labelVi, item.res);
    }
    return;
  }

  const batchJustFinished = WAS_BATCH_ACTIVE;
  if (batchJustFinished) {
    WAS_BATCH_ACTIVE = false;
  }
  const hasServerRunning =
    (st.all_running || []).length > 0 ||
    (st.queued_rerun_stems || []).length > 0 ||
    store.PENDING_RERUN_JOBS.size > 0;
  if (!hasServerRunning) {
    stopBatchTracking();
  } else if (BATCH_EVENTS) {
    BATCH_EVENTS.close();
    BATCH_EVENTS = null;
  }
  setBatchProgress("");

  const curUpdated = store.CURRENT_STEM ? ((st.running_updated || {})[store.CURRENT_STEM] || "") : "";
  const isCurrentBusy = Boolean(store.CURRENT_STEM && isStemBusy(store.CURRENT_STEM));
  const currentStepUpdated = Boolean(curUpdated && curUpdated !== LAST_CURRENT_UPDATED);
  const currentFinishedJustNow = wasCurrentBusy && !isCurrentBusy;
  const currentRerunFinished = Boolean(
    store.CURRENT_STEM && completedReruns.some((item) => item.stem === store.CURRENT_STEM)
  );
  if (curUpdated) {
    LAST_CURRENT_UPDATED = curUpdated;
  }

  if (
    batchJustFinished ||
    !hasServerRunning ||
    completedReruns.length > 0 ||
    currentStepUpdated ||
    currentFinishedJustNow ||
    runningKey !== LAST_RUNNING_KEY ||
    queuedKey !== LAST_QUEUED_KEY
  ) {
    LAST_RUNNING_KEY = runningKey;
    LAST_QUEUED_KEY = queuedKey;
    await loadFileList(true);
  }
  if (
    store.CURRENT_STEM &&
    (currentStepUpdated ||
      currentFinishedJustNow ||
      currentRerunFinished ||
      wasCurrentRunning ||
      (batchJustFinished &&
        ((st.stems || []).includes(store.CURRENT_STEM) ||
          (st.completed || []).includes(store.CURRENT_STEM) ||
          (st.cancelled || []).includes(store.CURRENT_STEM) ||
          Boolean((st.errors || {})[store.CURRENT_STEM]))))
  ) {
    await selectFile(store.CURRENT_STEM, {
      silent: true,
      onlyIfCurrent: true,
      skipSidebarRender: true,
    });
  }
  syncRerunButtonsState();
  for (const item of completedReruns) {
    applyRerunOutcome(item.stem, item.pend.step, item.pend.labelVi, item.res);
  }
  if (batchJustFinished) {
    if (st.stopped) {
      showToast(`⏹ Đã dừng theo yêu cầu (đã xong ${st.done_count}/${st.total} file).`, "warn");
    } else if (st.total > 0) {
      showToast(
        `Hoàn tất chạy AI: thành công ${st.done_count}/${st.total} file` +
          (st.err_count ? ` (${st.err_count} file lỗi).` : "!")
      );
    }
  }
  if (hasServerRunning && !BATCH_POLL_TIMER && !BATCH_EVENTS) {
    startBatchPollingFallback();
  }
}

export function startBatchPollingFallback() {
  stopBatchTracking();
  const poll = async () => {
    if (POLL_IN_FLIGHT) return;
    POLL_IN_FLIGHT = true;
    try {
      await handleBatchStatus(await api("/api/batch/status"));
    } catch (err) {
      console.warn("Lỗi poll batch status:", err);
    } finally {
      POLL_IN_FLIGHT = false;
    }
  };
  BATCH_POLL_TIMER = setInterval(poll, 600);
  poll();
}

export function startBatchPolling() {
  stopBatchTracking();
  LAST_BATCH_DONE = -1;
  LAST_BATCH_ERR = -1;
  LAST_CURRENT_UPDATED = "";
  LAST_RUNNING_KEY = "";
  LAST_QUEUED_KEY = "";

  if (typeof EventSource === "undefined") {
    startBatchPollingFallback();
    return;
  }

  const es = new EventSource("/api/batch/events");
  BATCH_EVENTS = es;
  let latest = null;
  let busy = false;

  const drain = async () => {
    if (busy) return;
    busy = true;
    try {
      while (latest) {
        const st = latest;
        latest = null;
        await handleBatchStatus(st);
      }
    } catch (err) {
      console.warn("Lỗi xử lý SSE batch:", err);
    } finally {
      busy = false;
    }
  };

  es.onmessage = (ev) => {
    try {
      latest = JSON.parse(ev.data);
    } catch (err) {
      console.warn("SSE batch: dữ liệu không hợp lệ", err);
      return;
    }
    if (!latest.active) {
      es.close();
      if (BATCH_EVENTS === es) BATCH_EVENTS = null;
    }
    drain();
  };

  es.onerror = () => {
    if (BATCH_EVENTS !== es) return;
    console.warn("SSE batch bị ngắt, chuyển sang polling.");
    startBatchPollingFallback();
  };
}

export async function runPendingFiles() {
  const useMock = $("chk-mock") ? $("chk-mock").checked : false;
  await loadFileList(true);
  const candidates = pendingFiles()
    .filter((r) => r.status === "chua_xu_ly" || r.status === "loi" || r.status === "dang_lam_do" || r.status === "can_xu_ly_tay")
    .map((r) => r.file.replace(/\.docx$/i, ""));
  if (!candidates.length) {
    showToast("Tất cả các file trong danh sách đều đã ở trạng thái 'đã có nháp' hoặc 'đã duyệt'!");
    return;
  }
  const todo = candidates.filter((s) => !isStemBusy(s));
  if (!todo.length) {
    showToast("Các file chưa xử lý đều đang được AI xử lý!");
    return;
  }
  await runBatchOnServer(todo, useMock);
}
