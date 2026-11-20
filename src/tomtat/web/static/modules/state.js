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
  STOPPING_STEMS: new Set(), // tập các stem đã bấm Dừng và đang đợi luồng dừng hẳn
  RUNNING_STEPS: {}, // {stem: "inspect" | "label" | "extract" | "abstract" | "judge"}
  BATCH_ACTIVE: false,
  BATCH_STOPPED: false,
  BATCH_PROGRESS_TEXT: "",
  CUSTOM_EDITED: false,
  SORT_ORDER: "desc", // Mặc định: file ở cuối sẽ lên đầu, file đầu sẽ về cuối
};
