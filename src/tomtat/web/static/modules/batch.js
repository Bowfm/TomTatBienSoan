// Tải lên file .docx, chạy AI hàng loạt, và theo dõi tiến độ qua SSE (kèm fallback polling)
import { selectFile } from "./editor.js";
import { loadFileList, pendingFiles, renderFileList } from "./sidebar.js";
import { store } from "./state.js";
import { $, api, showToast } from "./utils.js";

let BATCH_POLL_TIMER = null; // dự phòng polling khi trình duyệt/kết nối không hỗ trợ SSE
let BATCH_EVENTS = null;     // EventSource đang mở tới /api/batch/events
let LAST_BATCH_DONE = -1;

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
  const box = $("batch-progress");
  const stopBtn = $("btn-stop-batch");
  const runBtn = $("btn-run-pending");
  if (!box) return;
  if (text) {
    box.textContent = text;
    box.classList.remove("hidden");
    if (stopBtn) stopBtn.classList.remove("hidden");
    if (runBtn) runBtn.disabled = true;
  } else {
    box.classList.add("hidden");
    if (stopBtn) stopBtn.classList.add("hidden");
    if (runBtn) runBtn.disabled = false;
  }
}

export async function runBatchOnServer(stems, useMock) {
  if (!stems || !stems.length) return;
  const concurrencyVal = $("sel-concurrency") ? Number($("sel-concurrency").value) || 8 : 8;

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
    showToast(res.message);
    startBatchPolling();
  } catch (err) {
    showToast(err.message, true);
  }
}

export async function stopBatch() {
  try {
    const res = await api("/api/batch/stop", { method: "POST" });
    showToast(res.message || "Đã gửi yêu cầu dừng.");
  } catch (err) {
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
}

export async function handleBatchStatus(st) {
  if (!st) return;

  store.RUNNING_STEMS.clear();
  (st.running || []).forEach((s) => store.RUNNING_STEMS.add(s));

  if (st.active) {
    const activeList = (st.running || []).map((s) => `${s}.docx`).join(", ");
    setBatchProgress(
      `⏳ Đang chạy song song ${store.RUNNING_STEMS.size} luồng (${st.done_count + st.err_count}/${st.total} xong) — Đang xử lý: ${activeList || "..."}`
    );
    if (st.done_count !== LAST_BATCH_DONE) {
      LAST_BATCH_DONE = st.done_count;
      await loadFileList(true);
    } else {
      renderFileList();
    }
    return;
  }

  stopBatchTracking();
  store.RUNNING_STEMS.clear();
  setBatchProgress("");
  await loadFileList(true);
  if (st.stopped) {
    showToast(`Đã dừng theo yêu cầu (đã xong ${st.done_count}/${st.total} file).`);
  } else if (st.total > 0) {
    showToast(
      `Hoàn tất chạy AI: thành công ${st.done_count}/${st.total} file` +
        (st.err_count ? ` (${st.err_count} file lỗi).` : "!")
    );
  }
}

export function startBatchPollingFallback() {
  stopBatchTracking();
  const poll = async () => {
    try {
      await handleBatchStatus(await api("/api/batch/status"));
    } catch (err) {
      console.warn("Lỗi poll batch status:", err);
    }
  };
  BATCH_POLL_TIMER = setInterval(poll, 1000);
  poll();
}

export function startBatchPolling() {
  stopBatchTracking();
  LAST_BATCH_DONE = -1;

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
  const todo = pendingFiles()
    .filter((r) => r.status === "chua_xu_ly" || r.status === "loi" || r.status === "dang_lam_do" || r.status === "can_xu_ly_tay")
    .map((r) => r.file.replace(/\.docx$/i, ""));
  if (!todo.length) {
    showToast("Tất cả các file trong danh sách đều đã ở trạng thái 'đã có nháp' hoặc 'đã duyệt'!");
    return;
  }
  await runBatchOnServer(todo, useMock);
}
