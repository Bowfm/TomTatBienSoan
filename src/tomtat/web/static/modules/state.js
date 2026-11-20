// Quản lý trạng thái dùng chung toàn ứng dụng (shared state store)

export const store = {
  FILES: [],
  RPD_STATUS: [],
  CURRENT_STEM: null,
  CURRENT_STATE: null,
  CURRENT_UNITS: null,
  CURRENT_VERSIONS: [],
  SELECTIONS: {}, // {doc_n: Set(id)}
  RUNNING_STEMS: new Set(), // tập các stem đang được AI xử lý (batch hoặc rerun)
  LOCAL_RERUN_STEMS: new Set(), // tập các stem đang được tab này chạy rerunStep
  QUEUED_STEMS: new Set(), // tập các stem đang chờ lượt khi đã chạy đủ số luồng cho phép
  LOCAL_QUEUED_STEMS: new Set(), // khóa lạc quan trong lúc chờ API /rerun phản hồi
  QUEUED_STEPS: {}, // {stem: "label" | "extract" | "abstract" | "judge" | "all"}
  QUEUED_POSITIONS: {}, // {stem: 1, 2, ...}
  PENDING_RERUN_JOBS: new Map(), // stem -> { jobId, step, labelVi, notifiedStart }
  SEEN_RERUN_JOB_IDS: new Set(), // các jobId đã xử lý hoàn tất/toast trên tab này
  ALLOWED_WORKERS: 3, // số luồng song song tối đa hiện cho phép
  STOPPING_STEMS: new Set(), // tập các stem đã bấm Dừng và đang đợi luồng dừng hẳn
  RUNNING_STEPS: {}, // {stem: "inspect" | "label" | "extract" | "abstract" | "judge"}
  BATCH_ACTIVE: false,
  BATCH_STOPPED: false,
  BATCH_PROGRESS_TEXT: "",
  CUSTOM_EDITED: false,
  SORT_ORDER: "desc", // Mặc định: file ở cuối sẽ lên đầu, file đầu sẽ về cuối
};

export function isStemRunning(stem) {
  return Boolean(stem && (store.RUNNING_STEMS.has(stem) || store.LOCAL_RERUN_STEMS.has(stem)));
}

export function isStemQueued(stem) {
  return Boolean(
    stem &&
      !isStemRunning(stem) &&
      (store.QUEUED_STEMS.has(stem) || store.LOCAL_QUEUED_STEMS.has(stem))
  );
}

export function isStemBusy(stem) {
  return isStemRunning(stem) || isStemQueued(stem);
}
