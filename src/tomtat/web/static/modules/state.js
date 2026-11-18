// Quản lý trạng thái dùng chung toàn ứng dụng (shared state store)

export const store = {
  FILES: [],
  CURRENT_STEM: null,
  CURRENT_STATE: null,
  CURRENT_UNITS: null,
  SELECTIONS: {}, // {doc_n: Set(id)}
  DONE_SELECTED: new Set(), // tập các stem được tick trong danh sách "Đã xong"
  RUNNING_STEMS: new Set(), // tập các stem đang được AI xử lý
  CUSTOM_EDITED: false,
  SORT_ORDER: "desc", // Mặc định: file ở cuối sẽ lên đầu, file đầu sẽ về cuối
};
