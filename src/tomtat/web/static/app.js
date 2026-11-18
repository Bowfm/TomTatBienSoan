// Điểm khởi chạy giao diện duyệt bài (ES Module Entry Point)
import { runPendingFiles, startBatchPolling, stopBatch, uploadFiles } from "./modules/batch.js";
import { approveAndExport, assembleFromSelections, rerunStep, saveDraft, scheduleCheck } from "./modules/editor.js";
import { initUpdateChecker, openLogsModal, openPreviewModal } from "./modules/modals.js";
import {
  approvedFiles,
  deleteFiles,
  downloadDoneZip,
  isApprovedStatus,
  loadFileList,
  moveFile,
  renderDoneList,
  renderFileList,
} from "./modules/sidebar.js";
import { store } from "./modules/state.js";
import { $, api } from "./modules/utils.js";

window.addEventListener("DOMContentLoaded", () => {
  $("btn-add-files").addEventListener("click", () => $("file-upload-input").click());
  $("file-upload-input").addEventListener("change", (e) => uploadFiles(e.target.files, false));
  $("btn-run-pending").addEventListener("click", runPendingFiles);

  if ($("btn-clear-all")) {
    $("btn-clear-all").addEventListener("click", () => {
      if (!store.FILES.length) return;
      deleteFiles(
        "all",
        [],
        `Bạn có chắc muốn XÓA TẤT CẢ ${store.FILES.length} file (cả Chờ duyệt và Đã duyệt) để bắt đầu ngày làm việc mới?`
      );
    });
  }
  if ($("btn-clear-pending")) {
    $("btn-clear-pending").addEventListener("click", () => {
      const n = store.FILES.filter((r) => !isApprovedStatus(r.status)).length;
      if (!n) return;
      deleteFiles("pending", [], `Bạn có chắc muốn xóa tất cả ${n} file trong cột Chờ duyệt?`);
    });
  }
  if ($("btn-clear-approved")) {
    $("btn-clear-approved").addEventListener("click", () => {
      const n = approvedFiles().length;
      if (!n) return;
      deleteFiles(
        "approved",
        [],
        `Bạn có chắc muốn xóa tất cả ${n} file trong cột Đã duyệt (hãy đảm bảo bạn đã tải file ZIP về máy)?`
      );
    });
  }
  if ($("btn-stop-batch")) {
    $("btn-stop-batch").addEventListener("click", stopBatch);
  }
  $("btn-download-zip").addEventListener("click", downloadDoneZip);
  $("chk-done-all").addEventListener("change", (e) => {
    const doneStems = store.FILES
      .filter((r) => r.status === "da_xuat" || r.status === "da_duyet")
      .map((r) => r.file.replace(/\.docx$/i, ""));
    if (e.target.checked) {
      doneStems.forEach((s) => store.DONE_SELECTED.add(s));
    } else {
      doneStems.forEach((s) => store.DONE_SELECTED.delete(s));
    }
    renderDoneList();
  });
  $("filter-status").addEventListener("change", renderFileList);
  const btnSort = $("btn-toggle-sort");
  if (btnSort) {
    btnSort.addEventListener("click", () => {
      store.SORT_ORDER = store.SORT_ORDER === "desc" ? "asc" : "desc";
      btnSort.textContent = store.SORT_ORDER === "desc" ? "🔻 Cuối lên đầu" : "🔺 Đầu lên trước";
      btnSort.title = store.SORT_ORDER === "desc"
        ? "Đang hiển thị file cuối lên đầu (nhấn để đổi sang đầu lên trước)"
        : "Đang hiển thị file đầu lên trước (nhấn để đổi sang cuối lên đầu)";
      renderFileList();
    });
  }
  $("sel-style").addEventListener("change", scheduleCheck);
  $("txt-extract").addEventListener("input", () => {
    store.CUSTOM_EDITED = true;
    scheduleCheck();
  });
  $("txt-abstract").addEventListener("input", scheduleCheck);
  $("btn-rebuild-extract").addEventListener("click", assembleFromSelections);
  if ($("btn-save")) $("btn-save").addEventListener("click", saveDraft);
  $("btn-approve").addEventListener("click", () => approveAndExport(false));
  if ($("btn-logs")) $("btn-logs").addEventListener("click", openLogsModal);

  initUpdateChecker();
  $("btn-preview").addEventListener("click", openPreviewModal);
  $("btn-close-preview").addEventListener("click", () => $("preview-modal").classList.add("hidden"));
  $("preview-close-bg").addEventListener("click", () => $("preview-modal").classList.add("hidden"));

  document.querySelectorAll("button[data-rerun]").forEach((btn) => {
    btn.addEventListener("click", () => rerunStep(btn.dataset.rerun));
  });

  window.addEventListener("keydown", (e) => {
    if (e.altKey && e.key === "ArrowDown") {
      e.preventDefault();
      moveFile(1);
    } else if (e.altKey && e.key === "ArrowUp") {
      e.preventDefault();
      moveFile(-1);
    }
  });

  loadFileList(false);
  api("/api/batch/status")
    .then((st) => {
      if (st && st.active) startBatchPolling();
    })
    .catch(() => {});
});
