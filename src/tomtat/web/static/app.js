// Điểm khởi chạy giao diện duyệt bài (ES Module Entry Point)
import { runPendingFiles, startBatchPolling, startBatchPollingFallback, stopBatch, uploadFiles } from "./modules/batch.js";
import {
  approveAndExport,
  assembleFromSelections,
  onStyleChange,
  rerunStep,
  rollbackPreviousVersion,
  saveDraft,
  scheduleCheck,
  stopFileAI,
  syncVersionControls,
} from "./modules/editor.js";
import {
  closePreviewModal,
  initUpdateChecker,
  openLogsModal,
  openPreviewModal,
  openVersionsModal,
} from "./modules/modals.js";
import {
  approvedFiles,
  deleteFiles,
  isApprovedStatus,
  loadFileList,
  moveFile,
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
        `Bạn có chắc muốn xóa tất cả ${n} file trong cột Đã duyệt (lưu ý: sẽ xóa cả file trong thư mục output/)?`
      );
    });
  }
  if ($("btn-stop-batch")) {
    $("btn-stop-batch").addEventListener("click", stopBatch);
  }
  if ($("btn-stop-file-ai")) {
    $("btn-stop-file-ai").addEventListener("click", () => stopFileAI());
  }
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
  $("sel-style").addEventListener("change", onStyleChange);
  $("txt-extract").addEventListener("input", () => {
    store.CUSTOM_EDITED = true;
    syncVersionControls();
    scheduleCheck();
  });
  $("txt-abstract").addEventListener("input", () => {
    syncVersionControls();
    scheduleCheck();
  });
  $("btn-rebuild-extract").addEventListener("click", assembleFromSelections);
  if ($("btn-save")) $("btn-save").addEventListener("click", saveDraft);
  if ($("btn-rollback-prev")) $("btn-rollback-prev").addEventListener("click", rollbackPreviousVersion);
  if ($("btn-versions-modal")) $("btn-versions-modal").addEventListener("click", () => openVersionsModal(null));
  if ($("version-note")) {
    $("version-note").addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        saveDraft();
      }
    });
  }
  $("btn-approve").addEventListener("click", () => approveAndExport(false));
  if ($("btn-logs")) $("btn-logs").addEventListener("click", () => openLogsModal());

  initUpdateChecker();
  $("btn-preview").addEventListener("click", openPreviewModal);
  $("btn-close-preview").addEventListener("click", closePreviewModal);
  $("preview-close-bg").addEventListener("click", closePreviewModal);

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
      if (st && st.active) {
        startBatchPolling();
      } else if (
        st &&
        ((Array.isArray(st.all_running) && st.all_running.length > 0) ||
          (Array.isArray(st.queued_rerun_stems) && st.queued_rerun_stems.length > 0))
      ) {
        startBatchPollingFallback();
      }
    })
    .catch(() => {});
});
