# Sổ Lỗi & Bất Biến Cần Bảo Vệ (Known Bugs Ledger)

> [!IMPORTANT]
> Trước khi sửa bất kỳ module nào trong `TomTatBienSoan`, hãy kiểm tra các mục `BUG-XXX` liên quan dưới đây để không vô tình phá vỡ bất biến đã khóa. Khi fix lỗi mới, bổ sung một mục mới theo đúng mẫu và gắn với test tự động tương ứng.

## Bảng Tra Cứu Nhanh

| ID | Ngày | Khu vực | Tóm tắt lỗi đã fix | Test bảo vệ |
| :--- | :--- | :--- | :--- | :--- |
| BUG-001 | 2026-11-18 | `checks/suspicious.py` | Nhận diện nhầm văn bản tài chính/trading (`M54341`) là biên lai chuyển tiền | `tests/test_checks.py::test_check_suspicious_document` |
| BUG-002 | 2026-11-18 | `config.py`, `llm/gemini.py` | Key cũ trong `.env` đè `api_keys.txt`; lỗi `401/403` không loại ngay key khỏi toàn bộ model | `tests/test_regressions.py::test_bug_002_*` |
| BUG-003 | 2026-11-18 | `services/`, `web/` | Chạy batch từ trình duyệt gây cạn socket; chuyển sang server thread pool + SSE | `tests/test_services.py::test_api_batch_endpoints`, `test_batch_sse_stream_until_done` |
| BUG-004 | 2026-11-18 | `config.yaml` | Model đã khai tử/không hợp lệ (`2.5`, `3.5-pro`, `3.7`) lọt vào danh sách fallback | `tests/test_regressions.py::test_bug_004_config_models_valid_and_consistent` |
| BUG-005 | 2026-11-18 | `updater.py` | Cập nhật qua ZIP trên máy không cài Git có nguy cơ ghi đè dữ liệu người dùng | `tests/test_regressions.py::test_bug_005_updater_zip_protects_user_entries` |
| BUG-006 | 2026-11-18 | `llm/base.py`, `llm/gemini.py` | Reset quota theo nửa đêm giờ VN thay vì `00:00` Pacific Time (`15:00` VN) | `tests/test_llm.py::test_quota_cycle_pacific_time` |
| BUG-007 | 2026-11-18 | `setup.bat` | Khối `if (...)` lồng dấu ngoặc trong batch gây thoát đột ngột trên Windows | `tests/test_regressions.py::test_bug_007_setup_bat_avoids_nested_paren_blocks_and_checks_paths` |
| BUG-008 | 2026-11-18 | `khoi_dong.bat`, `updater.py` | Tự động cập nhật đồng bộ chặn khởi động Web UI & lỗi SSL trên Windows thiếu root CA | `tests/test_regressions.py::test_bug_008_khoi_dong_bat_non_blocking_startup` |
| BUG-009 | 2026-11-19 | `updater.py` | Báo nhầm có bản cập nhật mới khi commit local đang đi trước GitHub (`origin/main`) | `tests/test_regressions.py::test_bug_009_updater_does_not_report_update_when_local_ahead` |
| BUG-010 | 2026-11-19 | `llm/gemini.py` | Lỗi `429` RPM tạm thời khóa oan toàn bộ `(key, model)` suốt cả ngày | `tests/test_llm.py::test_rpm_429_sets_cooldown_instead_of_exhausted`, `test_three_429_strikes_per_key_and_model` |
| BUG-011 | 2026-11-19 | `services/file_service.py`, `llm/gemini.py`, `web/routes/files.py` | Ngưỡng phát hiện log treo dùng số cố định hoặc báo lỗi giả khi tiến trình đang chạy trong `BatchManager`; chạy lại cùng file bị trùng; output rỗng do `max_output_tokens` không rõ lý do | `tests/test_services.py::test_get_file_logs_stale_threshold_respects_model_timeout`, `test_api_get_logs_never_marks_actively_running_file_as_stale`, `test_rerun_prevents_concurrent_same_file`, `tests/test_llm.py::test_incomplete_empty_output_logs_clear_reason_and_retries` |
| BUG-012 | 2026-11-19 | `checks/abstract_checks.py`, `prompts/abstract.md` | Cho phép bản biên soạn dài hơn trích chọn khi trích chọn ngắn, vi phạm quy tắc `B/T <= 70%` | `tests/test_checks.py::test_abstract_must_be_shorter_than_70_percent` |
| BUG-013 | 2026-11-19 | `web/static/`, `web/templates/index.html` | Lỗi cú pháp hoặc lệch tên `import`/`export` giữa các ES modules sau khi tách `app.js` | `tests/test_regressions.py::test_bug_013_frontend_es_modules_syntax_and_imports` |
| BUG-014 | 2026-11-20 | `config.yaml`, `config.py`, `services/batch_service.py`, `cli.py`, `web/` | Nâng lên xử lý 2 file song song; chống chọn nhầm 4–16 luồng (UI/API/CLI) và chống vượt RPM/key khi nhiều luồng | `tests/test_regressions.py::test_bug_014_*` |
| BUG-015 | 2026-11-20 | `pipeline.py`, `web/routes/process.py`, `web/static/modules/editor.js` | Nút "AI: Chạy lại cả bài" báo xong ngay lập tức mà không gọi AI hay ghi log mới | `tests/test_regressions.py::test_bug_015_*` |
| BUG-016 | 2026-11-20 | `llm/gemini.py`, `llm/base.py`, `services/file_service.py` | Lỗi 429 QPS payload trùng lặp khóa oan toàn bộ model fallback (`3.8`/`3.6`/`lite`) và `get_file_logs` hiển thị sai `net_try=1` thành `#59` | `tests/test_regressions.py::test_bug_016_*` |
| BUG-017 | 2026-11-20 | `state.py`, `pipeline.py`, `services/file_service.py`, `web/`, `cli.py` | Thiếu UI lưu/rollback version; `autoSaveSilent` spam `vNNN.json`; `restore_draft` không xóa `judge` cũ và không bảo toàn bản sửa tay trước AI/rollback | `tests/test_regressions.py::test_bug_017_*` |
| BUG-018 | 2026-11-20 | `state.py`, `services/file_service.py`, `web/static/modules/` | Bấm Duyệt ở `v002` (không sửa nội dung) bị tạo thêm `v003` trùng hệt thay vì giữ nguyên `v002` | `tests/test_regressions.py::test_bug_018_approve_keeps_current_version_when_unchanged_and_creates_new_when_edited` |
| BUG-019 | 2026-11-20 | `pipeline.py`, `web/routes/process.py`, `services/file_service.py`, `web/` | Nút "AI: Gán nhãn lại" luôn báo `stale_steps` (`extract, abstract, judge`) kể cả khi nhãn không đổi | `tests/test_regressions.py::test_bug_019_*` |
| BUG-020 | 2026-11-20 | `web/templates/index.html`, `web/static/`, `web/routes/process.py`, `services/file_service.py`, `pipeline.py`, `prompts/` | Loại bỏ hoàn toàn phần "Ghi chú gửi cho AI khi chạy lại" (`#ai-note` / `user_note`), giữ nguyên `#version-note` và không để sót tham chiếu DOM gây lỗi JS | `tests/test_regressions.py::test_bug_020_ai_rerun_note_removed_cleanly` |
| BUG-021 | 2026-11-20 | `llm/`, `pipeline.py`, `services/batch_service.py`, `web/` | Bấm 1 trong 5 nút AI chạy lại hiện đang chạy bên trái nhưng không có nút Dừng và không hủy được luồng AI đang chờ/gọi API | `tests/test_regressions.py::test_bug_021_*` |
| BUG-023 | 2026-11-20 | `updater.py`, `web/app.py`, `web/routes/system.py`, `web/static/modules/modals.js`, `cli.py`, `dong_goi_chia_se.bat` | Nhận diện mọi commit GitHub qua Smart HTTP & UTF-8 BOM, nhưng chỉ thông báo cập nhật theo phiên khởi động (không báo giữa phiên) | `tests/test_regressions.py::test_bug_023_*` |
| BUG-024 | 2026-11-20 | `updater.py`, `config.py`, `web/app.py`, `web/routes/system.py`, `web/static/modules/modals.js`, `*.bat`, `.gitignore` | Tối ưu cập nhật: tự khởi động lại server (exit 42), dọn file cũ theo manifest ZIP, hiện commit message, gộp cache khởi động, `config.local.yaml`, guard trước khi push | `tests/test_regressions.py::test_bug_024_*` |
| BUG-022 | 2026-11-20 | `config.yaml`, `llm/base.py`, `llm/gemini.py`, `services/batch_service.py`, `services/file_service.py` | Lỗi `APITimeoutError` ở `gemini-3.8-flash` và các model khác do timeout mặc định `90s` cắt ngang làm phí RPD; đặt trần an toàn rộng theo từng model (`600s`/`420s`/`360s`/`180s`), nới khi retry, ngắt mạch trượt model và hỗ trợ `null` khi cần bỏ giới hạn | `tests/test_regressions.py::test_bug_022_*` |
| BUG-025 | 2026-11-20 | `services/file_service.py` | Rollback về phiên bản cũ kế thừa nhầm `stale_steps` / `stale_reason` ("Bản trích chọn vừa được AI tạo lại...") của snapshot lịch sử | `tests/test_regressions.py::test_bug_025_rollback_clears_stale_steps_and_stale_reason` |
| BUG-026 | 2026-11-20 | `pipeline.py`, `services/file_service.py`, `web/routes/process.py`, `report.py`, `memory.py`, `web/static/modules/editor.js` | Chỉnh sửa phong cách thủ công (và chạy lại Trích chọn & Biên soạn) vẫn giữ nguyên ghi chú giải thích `label.reason`, độ chắc chắn và cờ cảnh báo của phong cách AI cũ | `tests/test_regressions.py::test_bug_026_*` |
| BUG-027 | 2026-11-20 | `config.yaml`, `llm/{base,gemini}.py`, `memory.py`, `web/templates/index.html` | Nâng lên xử lý song song 3 luồng an toàn với mạch ngắt bão `503` tiết kiệm RPD, giãn nhịp cổng toàn cục & hạ nhiệt dùng chung chống `429 QPS` | `tests/test_regressions.py::test_bug_027_*` |
| BUG-029 | 2026-11-20 | `services/batch_service.py`, `web/routes/{process,files,review,system}.py`, `web/static/` | Khi đã chạy đủ số luồng cho phép (vd 3/3), bấm 1 trong 5 nút AI ở file đang xem phải xếp hàng chờ lượt thay vì tạo thêm luồng thứ N+1 | `tests/test_regressions.py::test_bug_029_*` |
| BUG-028 | 2026-11-20 | `textutil.py`, `checks/abstract_checks.py`, `pipeline.py`, `prompts.py`, `prompts/abstract.md` | Bản biên soạn xuất hiện ký tự `\n` viết thô (`S89777`) do LLM double-escape trong JSON; thêm làm sạch ở cả đơn/đa văn bản và lỗi cơ học `literal_escape` | `tests/test_regressions.py::test_bug_028_*` |
| BUG-030 | 2026-11-20 | `web/static/{app.css,modules/{utils,sidebar,editor}.js}`, `web/templates/index.html` | Ấn chọn file ở cột bên trái làm danh sách tự cuộn xuống dưới ngay lập tức (nhất là khi zoom trình duyệt `< 100%`) | `tests/test_regressions.py::test_bug_030_sidebar_click_preserves_scroll_contract_and_behavior` |
| BUG-031 | 2026-11-20 | `llm/gemini.py` | Jitter trong `_reserve_gateway_wait` cộng sau vòng lặp `slots` làm slot mới có thể nhảy đè lên slot đã đặt kế tiếp (`test_bug_027_anti_qps_stagger...` fail ngẫu nhiên) | `tests/test_regressions.py::test_bug_031_reserve_gateway_wait_jitter_never_collides_with_existing_slots` |
| BUG-032 | 2026-11-20 | `config.yaml`, `llm/{base,gemini}.py`, `prompts.py`, `prompts/judge.md`, `pipeline.py`, `web/` | Chuẩn hóa G-Eval (`JUDGE_SCHEMA` lý do trước điểm, rubric + query + đa văn bản) và tách model riêng theo bước (`model_by_step.judge` dùng Flash-Lite 500 RPD/key) kèm gatekeeper `only_if_checks_ok` | `tests/test_regressions.py::test_bug_032_*` |
| BUG-033 | 2026-11-20 | `day_cap_nhat.bat`, `.agents/push_helper.py`, `.gitignore` | Nâng cấp script đẩy GitHub 1-click: tránh wildcard `*.txt`, không làm bẩn `.github_repo_url`, kiểm tra nhánh `main`/rebase trước khi push, chặn bí mật stage và tự sinh commit message | `tests/test_regressions.py::test_bug_033_*` |

---

## Chi Tiết Các Lỗi & Bất Biến

### BUG-001: Nhận diện nhầm văn bản tài chính (`M54341`) là biên lai chuyển tiền
- **Commit / Nguồn**: `5fdf30a`
- **File / Symbol**: `src/tomtat/checks/suspicious.py` (`check_suspicious_document`)
- **Triệu chứng**: Văn bản bình luận/tài chính có nhắc đến "ngân hàng", "giao dịch" bị gắn cờ nghi hủy sai.
- **Nguyên nhân gốc**: Điều kiện match từ khóa ngân hàng/giao dịch quá rộng, không yêu cầu tổ hợp dấu hiệu biên lai (số tài khoản, số tiền, mã giao dịch, chuyển khoản...).
- **Bất biến cần bảo vệ**: Văn bản tài chính/trading thông thường không bao giờ bị gắn cờ trong `check_suspicious_document`.
- **Regression Test**: `tests/test_checks.py::test_check_suspicious_document`

### BUG-002: Key cũ trong `.env` đè `api_keys.txt` và key `401/403` không được loại ngay
- **Commit / Nguồn**: `4d234ee`, `src/tomtat/config.py`, `src/tomtat/llm/gemini.py`
- **File / Symbol**: `get_api_keys`, `GeminiClient.generate_json`
- **Triệu chứng**: Người dùng thêm key mới vào `api_keys.txt` nhưng hệ thống vẫn dùng key cũ trong `.env`; gặp `401/403` vẫn quay lại gọi tiếp key hỏng ở model fallback.
- **Nguyên nhân gốc**: `.env` được đọc trước hoặc trộn lẫn; nhánh xử lý `401/403` chỉ tiếp tục vòng lặp mà không khóa key trên toàn `models_chain`.
- **Bất biến cần bảo vệ**: `api_keys.txt` (khi không rỗng) luôn thắng tuyệt đối `GEMINI_API_KEY` / `.env`; gặp `401/403` (hoặc `"unauthenticated"` / `"not valid"`) phải đánh dấu exhausted cho mọi model trong `models_chain` và chuyển key kế tiếp ngay.
- **Regression Test**: `tests/test_regressions.py::test_bug_002_api_keys_txt_overrides_env`, `tests/test_regressions.py::test_bug_002_401_403_invalid_key_exhausts_all_models`

### BUG-003: Chạy batch song song từ trình duyệt làm nghẽn socket
- **Commit / Nguồn**: `8d0235d`, `c89bbd6`
- **File / Symbol**: `src/tomtat/services/batch_service.py`, `src/tomtat/web/routes/process.py`, `src/tomtat/web/static/modules/batch.js`
- **Triệu chứng**: Mở hàng loạt kết nối từ trình duyệt làm treo/timeout request.
- **Nguyên nhân gốc**: Trình duyệt tự gọi từng request đồng thời thay vì để backend điều phối bằng thread pool + SSE.
- **Bất biến cần bảo vệ**: Batch chạy qua `/api/batch/start` (background pool trên server) và theo dõi qua `/api/batch/stream` hoặc `/api/batch/status`.
- **Regression Test**: `tests/test_services.py::test_api_batch_endpoints`, `tests/test_services.py::test_batch_sse_stream_until_done`

### BUG-004: Model đã khai tử hoặc không hợp lệ lọt vào `config.yaml`
- **Commit / Nguồn**: `3d44ee2`, `16e4e49`
- **File / Symbol**: `config.yaml` (`gemini.model`, `gemini.fallback_models`, `rpd_limits`, `rpm_limits`, `min_interval_s`)
- **Triệu chứng**: Gọi API gặp `404` liên tục vì chain chứa model `2.5` đã khai tử hoặc `3.5-pro`/`3.7` không có trong Free Tier.
- **Nguyên nhân gốc**: Danh sách fallback không được kiểm tra tính nhất quán với bảng `rpd_limits`/`rpm_limits` và danh sách model hợp lệ.
- **Bất biến cần bảo vệ**: Không chứa model `gemini-2.5*`, `gemini-3.7*`, hoặc `gemini-3.5-pro*` trong chain mặc định; mọi model trong `models_chain` đều phải có cấu hình `rpd_limits`, `rpm_limits`, `min_interval_s`.
- **Regression Test**: `tests/test_regressions.py::test_bug_004_config_models_valid_and_consistent`

### BUG-005: Cập nhật ZIP trên máy không cài Git ghi đè nhầm dữ liệu người dùng
- **Commit / Nguồn**: `0cd3922`
- **File / Symbol**: `src/tomtat/updater.py` (`PROTECTED_ENTRIES`, `run_update`)
- **Triệu chứng**: Máy người dùng cuối không cài `git` bị lỗi cập nhật hoặc mất `api_keys.txt` / `.env` / `work` / `input` / `output` nếu giải nén đè.
- **Nguyên nhân gốc**: Thiếu cơ chế fallback tải archive ZIP từ GitHub và bỏ qua các mục dữ liệu riêng tư.
- **Bất biến cần bảo vệ**: `run_update` qua ZIP luôn giữ nguyên 100% nội dung trong `PROTECTED_ENTRIES` (`input`, `output`, `work`, `.venv`, `.env`, `api_keys.txt`).
- **Regression Test**: `tests/test_regressions.py::test_bug_005_updater_zip_protects_user_entries`

### BUG-006: Reset hạn mức ngày theo giờ địa phương thay vì Pacific Time
- **Commit / Nguồn**: `e4ec1e5`
- **File / Symbol**: `src/tomtat/llm/base.py` (`_get_quota_cycle_date`), `src/tomtat/llm/gemini.py`
- **Triệu chứng**: Bộ đếm RPD và `quota_exhausted.json` reset sai vào `00:00` giờ VN thay vì `15:00` giờ VN (`00:00` Pacific Time).
- **Nguyên nhân gốc**: Dùng `date.today()` địa phương thay vì múi giờ `America/Los_Angeles` (hoặc UTC-8 fallback).
- **Bất biến cần bảo vệ**: Chu kỳ reset phải tính theo Pacific Time và tự xóa cache khi sang ngày PT mới.
- **Regression Test**: `tests/test_llm.py::test_quota_cycle_pacific_time`

### BUG-007: `setup.bat` thoát đột ngột vì khối dấu ngoặc batch
- **Commit / Nguồn**: `d5a18b4`
- **File / Symbol**: `setup.bat`
- **Triệu chứng**: Chạy `setup.bat` trên Windows tự đóng cửa sổ đột ngột ở bước tìm Python hoặc khi biến môi trường/đường dẫn chứa dấu `(`.
- **Nguyên nhân gốc**: Dùng khối lệnh `if ... ( ... )` cho nhánh tìm/cài Python có chứa `%PATH%` / chuỗi dấu ngoặc khiến parser `cmd.exe` lỗi cú pháp lúc runtime.
- **Bất biến cần bảo vệ**: `setup.bat` dùng nhãn `goto :label` cho luồng tìm/cài Python, kiểm tra `requirements.txt` chống chạy trực tiếp trong ZIP, và không bọc khối `if (...)` quanh đoạn dò Python đa đường dẫn.
- **Regression Test**: `tests/test_regressions.py::test_bug_007_setup_bat_avoids_nested_paren_blocks_and_checks_paths`

### BUG-008: `khoi_dong.bat` tự động cập nhật chặn khởi động và lỗi SSL khi tải từ GitHub
- **Commit / Nguồn**: `ec19bab`
- **File / Symbol**: `khoi_dong.bat`, `src/tomtat/updater.py` (`run_update`)
- **Triệu chứng**: Khởi động công cụ bị kẹt khi không có mạng hoặc chứng chỉ SSL máy Windows bị lỗi.
- **Nguyên nhân gốc**: `khoi_dong.bat` chạy `run_update` đồng bộ trước khi mở server; `urllib` chỉ thử 1 context SSL mặc định.
- **Bất biến cần bảo vệ**: `khoi_dong.bat` chỉ kiểm tra phiên bản nhẹ (không chặn bằng `run_update`) rồi gọi `tomtat.bat review`; `run_update` có đa tầng fallback tải ZIP.
- **Regression Test**: `tests/test_regressions.py::test_bug_008_khoi_dong_bat_non_blocking_startup`

### BUG-009: Updater báo nhầm có bản cập nhật khi commit local mới hơn GitHub
- **Commit / Nguồn**: `97b6a8a`
- **File / Symbol**: `src/tomtat/updater.py` (`_is_git_ancestor`, `check_update`, `run_update`)
- **Triệu chứng**: Sau khi commit cục bộ chưa push lên GitHub, giao diện vẫn báo "Có bản cập nhật mới" vì `HEAD != remote_sha`.
- **Nguyên nhân gốc**: Chỉ so sánh bằng SHA (`current != remote`) mà không kiểm tra quan hệ tổ tiên (`git merge-base --is-ancestor`).
- **Bất biến cần bảo vệ**: Nếu `remote` là tổ tiên của `current`, `check_update` phải trả `has_update=False`, `status="up_to_date"`.
- **Regression Test**: `tests/test_regressions.py::test_bug_009_updater_does_not_report_update_when_local_ahead`

### BUG-010: Lỗi `429` RPM khóa oan `(key, model)` suốt cả ngày
- **Commit / Nguồn**: `8cc488d` và chỉnh sửa mới nhất ở `src/tomtat/llm/gemini.py`
- **File / Symbol**: `src/tomtat/llm/gemini.py` (`GeminiClient.generate_json`, `_idle_429_strikes`)
- **Triệu chứng**: Chạm giới hạn tốc độ phút (RPM) khiến các cặp `(key, model)` bị ghi vào `quota_exhausted.json` và bỏ phí hạn mức ngày.
- **Nguyên nhân gốc**: Đánh dấu exhausted ngay ở lần `429` đầu tiên thay vì đặt cooldown tạm thời và đếm ngưỡng strike.
- **Bất biến cần bảo vệ**: Lỗi `429` RPM đặt cooldown tạm; mỗi cặp `(key, model)` cần đủ 3 lần `429` mới đánh dấu exhausted trong ngày, và thành công ở giữa sẽ reset bộ đếm strike.
- **Regression Test**: `tests/test_llm.py::test_rpm_429_sets_cooldown_instead_of_exhausted`, `tests/test_llm.py::test_three_429_strikes_per_key_and_model`

### BUG-011: Ngưỡng phát hiện log treo sai theo model, chạy lại trùng file, và báo lỗi rỗng `max_output_tokens`
- **Commit / Nguồn**: Các sửa đổi ngày 2026-11-19 trong `file_service.py`, `gemini.py`, `process.py`
- **File / Symbol**: `FileService.get_file_logs`, `BatchManager.try_acquire_file`, `GeminiClient.generate_json`
- **Triệu chứng**: Model chậm như `gemini-4-hydrogen` (timeout 420s) bị báo treo sớm và hiển thị `0s`; bấm chạy lại cùng file đồng thời gây đè trạng thái; cắt thinking trả rỗng chỉ hiện `JSONDecodeError`.
- **Nguyên nhân gốc**: Ngưỡng treo dùng hằng số nhỏ thay vì `get_model_timeout_s(model) + buffer`; thiếu khóa file đồng thời; không kiểm tra `status == "incomplete"` trước `_parse_json`.
- **Bất biến cần bảo vệ**: Ngưỡng log treo tính theo `timeout + wait_s + buffer` từng model, và **tuyệt đối không** báo lỗi treo khi `stem` vẫn đang chạy thật trong `BatchManager` ở tiến trình server hiện tại (`started_epoch >= TOMTAT_STARTED_EPOCH`); không thể chạy đồng thời 2 tiến trình trên cùng `stem`; `incomplete`/empty output ghi rõ `max_output_tokens` + `think` tokens.
- **Regression Test**: `tests/test_services.py::test_get_file_logs_stale_threshold_respects_model_timeout`, `tests/test_services.py::test_api_get_logs_never_marks_actively_running_file_as_stale`, `tests/test_services.py::test_rerun_prevents_concurrent_same_file`, `tests/test_llm.py::test_incomplete_empty_output_logs_clear_reason_and_retries`

### BUG-012: Bản biên soạn vượt quá 70% bản trích chọn khi trích chọn ngắn
- **Commit / Nguồn**: Các sửa đổi ngày 2026-11-19 trong `checks/abstract_checks.py`, `prompts/abstract.md`
- **File / Symbol**: `check_length`, `abstract_targets`
- **Triệu chứng**: Với đoạn trích chọn ngắn, hệ thống cho phép hướng "longer" (`T/B`), dẫn tới bản biên soạn dài hơn bản trích chọn.
- **Nguyên nhân gốc**: Nhánh `longer` vẫn được bật khi `n_ext` nhỏ.
- **Bất biến cần bảo vệ**: Luôn đánh giá theo `B/T <= 0.70` (`direction == "shorter"`, `allow_longer == False`); mọi trường hợp `n_abs / n_ext > 0.70` đều báo lỗi `ratio`.
- **Regression Test**: `tests/test_checks.py::test_abstract_must_be_shorter_than_70_percent`

### BUG-013: Lỗi cú pháp hoặc lệch `import`/`export` giữa các ES modules frontend
- **Commit / Nguồn**: `f2377c2` và các module trong `src/tomtat/web/static/modules/`
- **File / Symbol**: `src/tomtat/web/static/app.js`, `src/tomtat/web/static/modules/*.js`, `src/tomtat/web/templates/index.html`
- **Triệu chứng**: Sửa hoặc đổi tên hàm export trong một module JS gây lỗi trắng trang hoặc chết nút trên trình duyệt mà `pytest` Python thuần không phát hiện ra.
- **Nguyên nhân gốc**: Thiếu kiểm tra cú pháp JS và đối chiếu đồ thị `import`/`export` tự động trong bộ test.
- **Bất biến cần bảo vệ**: Toàn bộ `.js` qua `node --check` (nếu có Node) và mọi `import { sym } from './...'` phải khớp chính xác với `export` trong file đích; tài nguyên tĩnh trong `index.html` phải tồn tại trên đĩa.
- **Regression Test**: `tests/test_regressions.py::test_bug_013_frontend_es_modules_syntax_and_imports`

### BUG-014: Xử lý song song 2 file — trần luồng và an toàn RPM
- **Commit / Nguồn**: Thay đổi ngày 2026-11-20 (`workers: 2`, `max_workers: 2`)
- **File / Symbol**: `config.yaml` (`workers`, `max_workers`), `src/tomtat/config.py` (`get_pool_scaling`, `clamp_workers`, `Config.reload`), `src/tomtat/services/batch_service.py` (`BatchManager.start`), `src/tomtat/web/routes/process.py` (`api_batch_start`), `src/tomtat/cli.py` (`cmd_process`), `src/tomtat/llm/gemini.py` (`_earliest_ready_ts_unlocked`, `_update_call_timestamp`), `src/tomtat/web/templates/index.html` (`#sel-concurrency`), `src/tomtat/web/static/modules/sidebar.js` (`syncPoolScalingUI`)
- **Triệu chứng (rủi ro được khóa)**: Dropdown "Luồng" cho chọn 4–16 luồng và `/api/batch/start` nhận bất kỳ `concurrency` nào, nên có thể vô tình chạy quá nhiều file cùng lúc, đốt RPD nhanh khi có bão 503. Khi chạy nhiều luồng, nếu pacing không được đặt chỗ dưới khóa, 2 luồng có thể cùng gọi 1 `(key, model)` và chạm trần RPM.
- **Nguyên nhân gốc**: Số luồng chỉ được đọc từ `workers` mà không có trần cứng; UI viết cứng các mức luồng lớn.
- **Bất biến cần bảo vệ**:
  - `1 <= workers <= max_workers`; trần `max_workers` được áp ở cả 3 tầng: `get_pool_scaling` (`recommended_workers`, `concurrency_options` cho UI), `BatchManager.start` / `clamp_workers` (API + CLI), và `sidebar.js` (gỡ option vượt trần). `index.html` không viết cứng mức luồng vượt trần.
  - `_acquire_paced_key_client_with_ts` đặt chỗ `scheduled_ts` dưới `_key_lock`, nên mỗi `(key, model)` luôn có các lượt cách nhau `>= min_interval_s` và `<= rpm_limit` lượt trong mọi cửa sổ 60 s, **bất kể số luồng**.
  - Mọi model trong chain thỏa `60 / min_interval_s < rpm_limits[model]` (Hydrogen/3.8/3.6: 4,62 < 5; Lite: 13,3 < 15).
  - Khi thêm hoặc bớt key: pacing theo từng key (mỗi key là 1 project riêng) nên không cần chỉnh gì; trần 2 luồng giữ nguyên.
- **Regression Test**: `tests/test_regressions.py::test_bug_014_parallel_acquire_never_exceeds_per_key_rpm`, `test_bug_014_batch_runs_exactly_two_files_concurrently`, `test_bug_014_max_workers_caps_ui_api_and_scaling`, `test_bug_014_config_concurrency_contract`, `test_bug_014_api_batch_start_respects_reload_and_cap`, `test_bug_014_out_of_order_timestamp_update_keeps_rpm_sorted`

### BUG-015: Nút "AI: Chạy lại cả bài" báo xong ngay mà không chạy AI hay sinh log mới
- **Commit / Nguồn**: Sửa đổi ngày 2026-11-20 trong `pipeline.py`, `web/routes/process.py`, `web/static/modules/editor.js`
- **File / Symbol**: `src/tomtat/web/static/modules/editor.js` (`rerunStep`), `src/tomtat/web/routes/process.py` (`api_rerun`), `src/tomtat/pipeline.py` (`process_file`)
- **Triệu chứng**: Khi bấm nút `AI: Chạy lại cả bài` trên file đã có nháp (`da_co_nhap`), giao diện hiện `Đang chạy 'Chạy lại cả bài'` rồi ngay lập tức báo `Đã chạy xong 'Chạy lại cả bài'`, tạo phiên bản nháp trùng lặp nhưng không gọi Gemini và không có log mới nào được ghi.
- **Nguyên nhân gốc**: `rerunStep("all")` trong `editor.js` không gửi cờ `restart: true`, đồng thời `api_rerun` đặt mặc định `restart = False` khi `step == "all"`. Khi vào `process_file(..., restart=False)`, logic kế thừa thông minh thấy `label`, `extract`, `abstract` cũ đã đạt chuẩn nên bỏ qua toàn bộ bước gọi AI và trả về ngay. Ngoài ra nhánh `if restart:` trong `process_file` chưa xóa `stale_steps`, chưa reset về state sạch và chưa chạy lại `step_inspect`.
- **Bất biến cần bảo vệ**:
  - `rerunStep("all")` gửi `restart: step === "all"` và `api_rerun` mặc định `restart = True` khi `step in (None, "all")` (trừ khi caller truyền tường minh `"restart": false`).
  - Khi `step in (None, "all")` (với `restart=True`) hoặc `step == "label"`, `api_rerun` không ghi đè trước `label.value` từ màn hình vào `state.json`.
  - Khi `process_file(..., restart=True)` chạy: reset `status = STATUS_PENDING`, `label`/`extract`/`abstract` mặc định, `judge = None`, `checks = None`, `flags = []`, `stale_steps = []`, `model_used = ""`, và chạy lại `step_inspect` (khi `.docx` tồn tại) trước khi chạy đủ `label -> extract -> abstract` (và `judge` nếu bật).
- **Regression Test**: `tests/test_regressions.py::test_bug_015_rerun_all_actually_reruns_llm_and_writes_logs`, `tests/test_regressions.py::test_bug_015_rerun_all_failure_does_not_overwrite_saved_label`

### BUG-016: Lỗi 429 QPS payload trùng lặp khóa oan toàn bộ model fallback và hiển thị sai `net_try=1`
- **Commit / Nguồn**: Sửa đổi ngày 2026-11-20 trong `llm/base.py`, `llm/gemini.py`, `services/file_service.py`
- **File / Symbol**: `src/tomtat/llm/base.py` (`_is_payload_qps_429`, `_heal_qps_exhausted_items`, `get_rpd_status`), `src/tomtat/llm/gemini.py` (`GeminiClient._load_exhausted`, `GeminiClient._reserve_payload_wait`, `GeminiClient._schedule_payload_qps_backoff`, `GeminiClient.generate_json`), `src/tomtat/services/file_service.py` (`FileService.get_file_logs`)
- **Triệu chứng**: Khi model `gemini-4-hydrogen` hết RPD trên hầu hết các key nhưng `gemini-3.8-flash` / `gemini-3.6-flash` / `flash-lite` vẫn còn RPD, hệ thống chỉ thử key còn sót trên `4-hydrogen` rồi báo hết quota trên mọi model mà không trượt sang `3.8`/`3.6`, đồng thời log lần gọi mới hiển thị nhảy cóc `thử lại API #59` rồi `#2`.
- **Nguyên nhân gốc**: Khi gọi lại cùng một file (`S95587`) nhanh liên tiếp qua 48 API key không có độ trễ theo payload, Google trả về `429: The query-per-second (QPS) limit for identical or near-identical request payloads has been exceeded`. Code cũ đếm lỗi QPS toàn cục này vào `_idle_429_strikes` của từng `(key, model)` và quét hết 48 key × 3 vòng trong ~90 giây/model, khóa oan toàn bộ key của `3.8`, `3.6`, `3.5-flash-lite`, `3.1-flash-lite` trong `work/quota_exhausted.json`. Ngoài ra `FileService.get_file_logs` kiểm tra `int(saved_net_try) > 1` nên bỏ qua `net_try=1` và suy luận nối tiếp từ chuỗi lỗi cũ.
- **Bất biến cần bảo vệ**:
  - Lỗi `429` do QPS payload trùng lặp (`_is_payload_qps_429`) **tuyệt đối không** tăng `_idle_429_strikes`, không đặt cooldown khóa key, và không thêm `(key, model)` vào `_exhausted`; chỉ lùi chờ lũy thừa theo payload (`_schedule_payload_qps_backoff`) và chuyển model sau 3 lần QPS liên tiếp trên cùng model (`action="backoff_payload_qps"`).
  - Các request có payload giống hệt nhau được giãn nhịp tối thiểu (`_reserve_payload_wait`) khi retry nhanh để không kích hoạt QPS limit trên toàn bộ pool.
  - `quota_exhausted.json` phiên bản cũ (`schema_version < 2`) tự động được đối soát qua `_heal_qps_exhausted_items` để mở khóa các `(key, model)` chỉ bị khóa do QPS (vẫn giữ nguyên các key hết RPD thật / 401 / 403).
  - `FileService.get_file_logs` luôn tôn trọng mọi giá trị `net_try >= 1` đã lưu trong file log.
- **Regression Test**: `tests/test_regressions.py::test_bug_016_payload_qps_429_does_not_exhaust_keys_and_falls_back_cleanly`, `tests/test_regressions.py::test_bug_016_qps_backoff_schedules_increasing_wait_and_payload_pacing`, `tests/test_regressions.py::test_bug_016_self_heals_legacy_qps_exhausted_quota_file`, `tests/test_regressions.py::test_bug_016_get_file_logs_respects_saved_net_try_one_after_previous_errors`

### BUG-017: Lưu version và rollback về version trước của mỗi file
- **Commit / Nguồn**: Sửa đổi ngày 2026-11-20 trong `state.py`, `pipeline.py`, `services/file_service.py`, `web/routes/review.py`, `cli.py`, `web/templates/index.html`, `web/static/`
- **File / Symbol**: `src/tomtat/state.py` (`FileWorkspace.save_draft_version`, `list_draft_versions`, `differs_from_latest_version`, `find_previous_version`), `src/tomtat/pipeline.py` (`process_file`), `src/tomtat/services/file_service.py` (`FileService.save_draft`, `restore_draft`, `get_file_versions`), `src/tomtat/web/routes/review.py` (`api_versions`, `api_save`, `api_restore`), `src/tomtat/cli.py` (`cmd_versions`), `src/tomtat/web/static/modules/editor.js`, `src/tomtat/web/static/modules/modals.js`
- **Triệu chứng**: Giao diện không có nút lưu mốc version, danh sách lịch sử (`renderVersions` bị bỏ trống) và nút rollback; `autoSaveSilent` gọi `/save` liên tục mỗi 300ms sinh hàng loạt `vNNN.json` rác; `process_file(..., restart=False)` tạo bản chụp trùng khi không gọi AI; `restore_draft` không xóa `judge` cũ / `output_path`, không tự sao lưu bản sửa tay trước khi khôi phục hoặc chạy lại AI, và sắp xếp sai theo chuỗi nếu vượt quá `v999`.
- **Nguyên nhân gốc**: Tầng `state.py` / `file_service.py` mới dừng ở bản phác thảo ban đầu, frontend chưa gắn HTML/JS và chưa phân tách cờ `auto_save` với `create_version`.
- **Bất biến cần bảo vệ**:
  - `autoSaveSilent` luôn gửi `auto_save: true, create_version: false` để chỉ cập nhật `state.json` mà không tạo `vNNN.json`; chỉ tạo mốc version khi người dùng bấm lưu thủ công, khi AI thực sự chạy xong bước/pipeline (`ran_ai=True`), khi duyệt xuất (`approve`), khi rollback (`restore`), hoặc khi sao lưu tự động trước AI/rollback (`auto:before_ai`, `auto:before_restore`).
  - `only_step="inspect"` không tạo version rỗng; `process_file(..., restart=False)` khi không gọi AI và không đổi nội dung không tạo version trùng lặp.
  - Rollback luôn không phá hủy dữ liệu (append-only): nếu state hiện tại (hoặc payload sửa tay kèm theo) khác với version mới nhất thì tự động lưu `auto:before_restore` trước khi khôi phục, sau đó cập nhật đủ `label`, `extract`, `abstract`, `judge`, `checks`, `status`, `output_path` và ghi mốc `restore` (`restored_from`).
  - `/save` và `/restore` luôn bị chặn `409` nếu `stem` đang chạy AI trong `BatchManager`.
  - `list_draft_versions` luôn sắp xếp theo số nguyên `version` giảm dần (kể cả `>= 1000`) và tương thích ngược với snapshot cũ thiếu trường.
- **Regression Test**: `tests/test_regressions.py::test_bug_017_version_save_auto_save_and_rollback_flow`, `tests/test_regressions.py::test_bug_017_pre_ai_backup_and_no_duplicate_resume_versions`, `tests/test_regressions.py::test_bug_017_numeric_version_sort_legacy_snapshot_and_concurrency_guard`, `tests/test_regressions.py::test_bug_017_cli_versions_and_frontend_contract`

### BUG-018: Duyệt bài ở một version (vd `v002`) tạo thêm version trùng lặp (`v003`)
- **Commit / Nguồn**: Sửa đổi ngày 2026-11-20 trong `state.py`, `services/file_service.py`, `web/static/modules/editor.js`, `modals.js`
- **File / Symbol**: `src/tomtat/state.py` (`FileWorkspace.mark_version_approved`, `save_draft_version`), `src/tomtat/services/file_service.py` (`FileService.approve_and_export`), `src/tomtat/web/static/modules/editor.js` (`renderVersions`), `src/tomtat/web/static/modules/modals.js` (`openVersionsModal`)
- **Triệu chứng**: Khi người dùng đang ở `v002` và bấm **Duyệt & Ghi .docx** mà không sửa thêm gì, hệ thống tạo thêm `v003` có nội dung giống hệt `v002` và nhảy số version hiện tại sang `v003`.
- **Nguyên nhân gốc**: `FileService.approve_and_export` luôn gọi `ws.save_draft_version(..., source="approve")` bất kể nội dung có thay đổi so với version mới nhất hay không.
- **Bất biến cần bảo vệ**:
  - Khi bấm duyệt mà nội dung khớp với `latest_draft_version` (`not ws.differs_from_latest_version(st)`), `approve_and_export` **tuyệt đối không** tạo version mới; chỉ cập nhật `approved=True`, `approved_at`, `status` ngay trên `latest_draft_version` qua `mark_version_approved` và giữ nguyên `current_version`.
  - Chỉ tạo version mới (`source="approve"`, `approved=True`) khi chưa có version nào hoặc nội dung vừa duyệt thực sự khác với version mới nhất.
- **Regression Test**: `tests/test_regressions.py::test_bug_018_approve_keeps_current_version_when_unchanged_and_creates_new_when_edited`

### BUG-019: "AI: Gán nhãn lại" luôn báo cần sửa bước sau ngay cả khi nhãn không đổi
- **Commit / Nguồn**: Sửa đổi ngày 2026-11-20 trong `pipeline.py`, `models.py`, `state.py`, `services/file_service.py`, `web/routes/process.py`, `cli.py`, `web/templates/index.html`, `web/static/`
- **File / Symbol**: `src/tomtat/pipeline.py` (`process_file`, `_mark_stale_after`, `_clear_stale`, `_step_has_content`), `src/tomtat/web/routes/process.py` (`api_rerun`), `src/tomtat/services/file_service.py` (`FileService.save_draft`, `restore_draft`, `approve_and_export`), `src/tomtat/web/static/modules/editor.js` (`renderEditor`, `rerunStep`)
- **Triệu chứng**: Khi bấm **AI: Gán nhãn lại** (`only_step="label"`), hệ thống luôn đặt `stale_steps = ["extract", "abstract", "judge"]` và hiển thị cảnh báo cần chạy lại các bước sau ngay cả khi AI trả về nhãn giống hệt nhãn trước đó, hoặc báo cả `judge` dù `judge` chưa từng chạy.
- **Nguyên nhân gốc**: `process_file(..., only_step=...)` gọi `_mark_stale_after(st, only_step)` vô điều kiện mà không đối chiếu `new_label` với `prev_label`, và `_mark_stale_after` không kiểm tra các bước sau đã có nội dung hay chưa.
- **Bất biến cần bảo vệ**:
  - Khi chạy `only_step="label"`, chỉ đánh dấu `stale_steps` và `stale_reason` nếu `new_label != prev_label` (sau chuẩn hóa `nfc(...).strip()`) và chỉ cho các bước sau thực sự đã có nội dung (`_step_has_content`).
  - Khi `new_label == prev_label` (hoặc file mới chưa có nhãn trước và chưa có bước sau), **tuyệt đối không** đánh dấu thêm `stale_steps` và không làm mất `stale_steps` đã có từ bước khác.
  - Khi phong cách thay đổi (cả qua `AI: Gán nhãn lại` hoặc đổi tay trên dropdown `#sel-style`), UI phải hỏi người dùng có muốn chỉnh lại luôn `Bản trích chọn` và `Bản biên soạn` không và hiển thị nút bấm kích hoạt `step="extract_abstract"` (chạy lại liên tiếp `extract` → `abstract` với phong cách mới mà không chạy lại `label`), cùng nút bỏ qua (`dismiss_stale`).
  - `/api/file/<stem>/rerun` khi `step == "label"` phải trả về `label_changed`, `previous_label`, `new_label` để UI thông báo rõ cho người dùng và làm nổi bật các thẻ cần chỉnh sửa lại.
- **Regression Test**: `tests/test_regressions.py::test_bug_019_relabel_marks_stale_only_when_label_differs`, `tests/test_regressions.py::test_bug_019_style_change_prompts_and_extract_abstract_action`, `tests/test_regressions.py::test_bug_019_frontend_relabel_stale_contract`

### BUG-020: Loại bỏ phần "Ghi chú gửi cho AI khi chạy lại" (`#ai-note` / `user_note`)
- **Commit / Nguồn**: Sửa đổi ngày 2026-11-20 trong `web/templates/index.html`, `web/static/app.css`, `web/static/modules/editor.js`, `web/routes/process.py`, `services/file_service.py`, `pipeline.py`, `prompts/{label,extract,abstract}.md`
- **File / Symbol**: `index.html` (`.ai-rerun-bar`), `editor.js` (`rerunStep`), `process.py` (`api_rerun`), `FileService.process`, `pipeline.py` (`process_file`, `step_label`, `step_extract`, `step_abstract`, `_generate_abstract_loop`), `prompts/*.md`
- **Triệu chứng**: Ô nhập `Ghi chú gửi cho AI khi chạy lại` (`#ai-note`) không mang lại hiệu quả thực tế cho người duyệt; nếu xóa ở HTML mà quên gỡ ở `editor.js` sẽ gây lỗi `$("ai-note").value` trên trình duyệt hoặc nhầm với `#version-note`.
- **Nguyên nhân gốc**: Luồng `ai-note` -> `user_note` trải qua UI, API `/rerun`, `FileService`, `pipeline.py` và 3 template prompt.
- **Bất biến cần bảo vệ**:
  - Không còn `#ai-note`, CSS `.ai-rerun-bar input`, hay biến `user_note` trong UI/JS/prompts/pipeline; giữ nguyên toàn vẹn `#version-note` và cả 5 nút `data-rerun` (`label`, `extract`, `abstract`, `judge`, `all`).
  - `POST /api/file/<stem>/rerun` hoạt động bình thường cả khi không gửi `note` lẫn khi client cũ cache gửi dư trường `note`.
- **Regression Test**: `tests/test_regressions.py::test_bug_020_ai_rerun_note_removed_cleanly`

### BUG-021: Thiếu nút Dừng và cơ chế hủy luồng AI khi bấm 1 trong 5 nút AI chạy lại
- **Commit / Nguồn**: Sửa đổi ngày 2026-11-20 trong `llm/{base,gemini,mock,__init__}.py`, `pipeline.py`, `services/{batch_service,file_service}.py`, `web/routes/{process,files}.py`, `web/templates/index.html`, `web/static/`
- **File / Symbol**: `src/tomtat/llm/base.py` (`LLMCancelledError`, `bind_cancel_event`, `check_not_cancelled`), `src/tomtat/llm/gemini.py` (`GeminiClient._call_once_cancellable`, `_release_reserved_slot`), `src/tomtat/pipeline.py` (`process_file`), `src/tomtat/services/batch_service.py` (`BatchManager.stop_file`, `BatchManager.stop`), `src/tomtat/web/routes/process.py` (`api_rerun`, `api_stop_file`), `src/tomtat/web/static/modules/{editor,sidebar,batch,state,modals}.js`, `src/tomtat/web/static/app.js`
- **Triệu chứng**: Khi người dùng bấm 1 trong 5 nút AI (`AI: Gán nhãn lại`, `AI: Trích chọn lại`, `AI: Biên soạn lại`, `AI: Chấm G-Eval`, `AI: Chạy lại cả bài`), cột bên trái hiển thị `⏳ Đang chạy (...)...` nhưng nút `⏹ Dừng` (`#btn-stop-batch`) bị ẩn vì chỉ dành cho batch, thanh AI không có nút dừng riêng, và backend chưa có endpoint hay cơ chế hủy luồng đang ngủ chờ RPM / đang gọi Gemini API.
- **Nguyên nhân gốc**: `rerunStep` gửi `/api/file/<stem>/rerun` đơn lẻ không kích hoạt `st.active` của `BatchManager`, UI ẩn `#btn-stop-batch` & `#batch-progress` khi `!st.active`, chưa có `#btn-stop-file-ai` / `.btn-stop-item`, và `GeminiClient.generate_json` dùng `time.sleep` + `_call_once` chặn đồng bộ không kiểm tra `threading.Event` hủy.
- **Bất biến cần bảo vệ**:
  - Bất cứ khi nào có file đang chạy AI (kể cả chạy đơn lẻ từ 5 nút AI lẫn chạy batch), giao diện **bắt buộc** hiển thị nút dừng ở thanh AI (`#btn-stop-file-ai`), ở thanh tiến độ cột trái (`#btn-stop-batch` + `#batch-progress` qua `syncRunningProgressBar`), và nút dừng nhanh trên dòng file đang chạy (`.btn-stop-item`).
  - `POST /api/file/<stem>/stop` và `POST /api/batch/stop` đều kích hoạt `cancel_event` để ngắt tức thì cả lúc ngủ chờ RPM (`cancelled_while_waiting`, phải trả slot RPM và **không** trừ RPD) lẫn lúc đang chờ `_call_once` (`cancelled_in_flight`, giải phóng semaphore và đóng log `status="cancelled"`).
  - Hủy chạy AI **tuyệt đối không** đẩy file sang trạng thái `loi` (`STATUS_ERROR`); file đã có nháp hợp lệ vẫn giữ `da_co_nhap` (hoặc `da_duyet`/`da_xuat` nếu chưa sửa gì), file mới/restart về `chua_xu_ly`, và khóa `BatchManager` được giải phóng ngay để có thể chạy lại.
- **Regression Test**: `tests/test_regressions.py::test_bug_021_stop_single_file_rerun_via_file_stop_and_batch_stop`, `tests/test_regressions.py::test_bug_021_gemini_client_cancels_during_rpm_wait_and_inflight_call`, `tests/test_regressions.py::test_bug_021_batch_stop_cancels_inflight_file_cleanly`, `tests/test_regressions.py::test_bug_021_frontend_stop_buttons_contract`

### BUG-022: Lỗi `APITimeoutError` ở `gemini-3.8-flash` và các model khác trong chain
- **Commit / Nguồn**: Sửa đổi ngày 2026-11-20 trong `config.yaml`, `src/tomtat/llm/base.py`, `src/tomtat/llm/gemini.py`, `src/tomtat/services/batch_service.py`, `src/tomtat/services/file_service.py`, `src/tomtat/pipeline.py`
- **File / Symbol**: `config.yaml` (`timeout_s: 300`, `timeout_s_by_model`, `max_timeouts_per_model`), `src/tomtat/llm/base.py` (`DEFAULT_TIMEOUT_S`, `_parse_timeout_value`, `get_model_timeout_s`, `_is_timeout_error`, `is_transient`), `src/tomtat/llm/gemini.py` (`GeminiClient._build_http_options`, `sync_config`, `_evict_client`, `_compute_call_timeout_s`, `_call_once`, `generate_json`), `src/tomtat/services/batch_service.py` (`make_client`), `src/tomtat/services/file_service.py` (`FileService.get_file_logs`)
- **Triệu chứng**: `gemini-3.8-flash` (và `gemini-3.6-flash`, `gemini-4-hydrogen`, `gemini-3.5-flash-lite`) bị `APITimeoutError: Request timed out` từ phía client (`90s`/`240s`), khiến mỗi lần bị cắt ngang vừa mất kết quả vừa tiêu tốn oan 1 RPD; đồng thời nếu bỏ hoàn toàn timeout (`null`) thì nguy cơ kẹt 1 luồng `workers: 2` khi gặp kết nối treo câm.
- **Nguyên nhân gốc**: Ngưỡng đọc HTTP `90s` trong `config.yaml` thấp hơn thực tế xử lý của model suy nghĩ (`gemini-4-hydrogen`, `gemini-3.8-flash`, `gemini-3.6-flash`), khi timeout không tự loại client HTTP cũ hay trượt sang model kế tiếp sau vài lần thất bại; ngoài ra khi người dùng cấu hình `null`/`0` qua `interactions.create(timeout=...)`, truyền `httpx.Timeout` trực tiếp bị `BaseSDK._coerce_timeout_ms` ép về giá trị hữu hạn nhỏ hơn thay vì giữ `read=None`.
- **Bất biến cần bảo vệ**:
  - `config.yaml` và `DEFAULT_TIMEOUT_S` đặt trần an toàn rất rộng theo từng model (`gemini-4-hydrogen: 600s`, `gemini-3.8-flash: 420s`, `gemini-3.6-flash: 360s`, `gemini-3.5-flash-lite: 180s`, `gemini-3.1-flash-lite: 180s`, mặc định `timeout_s: 300s`) để tránh cắt ngang làm phí RPD nhưng vẫn thoát luồng nếu socket treo câm.
  - `_compute_call_timeout_s` tự cộng đệm cho prompt dài (>25.000 ký tự) và nới timeout khi thử lại (trần mở rộng `>= 900s`); `_evict_client` loại client HTTP cũ khỏi cache và `max_timeouts_per_model` (mặc định `2`) trượt sang model kế tiếp thay vì kẹt hàng chục key.
  - Khi cấu hình `null`/`0`/`"none"` (toàn cục hoặc qua `timeout_s_by_model`), `get_model_timeout_s` trả `None` và `GeminiClient._build_http_options` giữ `read=None` với `connect=30s` (truyền `timeout=None` vào `interactions.create`); `sync_config()` đồng bộ nóng thay đổi từ `config.yaml`/`config.local.yaml`.
  - `FileService.get_file_logs` tôn trọng `timeout_s` ghi trong log / `get_model_timeout_s` / `DEFAULT_TIMEOUT_S` và tuyệt đối không đánh dấu treo khi tiến trình đang chạy trong `BatchManager` hiện tại.
- **Regression Test**: `tests/test_regressions.py::test_bug_022_config_and_default_timeouts_use_wide_safety_ceilings`, `test_bug_022_timeout_escalates_evicts_client_and_falls_back_across_models`, `test_bug_022_sync_config_updates_cached_client_on_reload`

### BUG-023: Mọi commit lên GitHub là một bản cập nhật mới — chỉ thông báo theo phiên khởi động
- **Commit / Nguồn**: Sửa đổi ngày 2026-11-20 trong `updater.py`, `web/app.py`, `web/routes/system.py`, `web/static/modules/modals.js`, `cli.py`, `dong_goi_chia_se.bat`
- **File / Symbol**: `src/tomtat/updater.py` (`get_remote_commit`, `_parse_git_info_refs_sha`, `_read_version_commit`, `run_update`), `src/tomtat/web/app.py` (`create_app`, `get_startup_update_info`), `src/tomtat/web/routes/system.py` (`api_check_update`, `api_self_update`), `src/tomtat/cli.py` (`cmd_review`), `src/tomtat/web/static/modules/modals.js` (`initUpdateChecker`), `dong_goi_chia_se.bat`
- **Triệu chứng**:
  1. Máy dùng bản đóng gói ZIP (`dong_goi_chia_se.bat` với PowerShell 5.1) ghi `version.json` có UTF-8 BOM làm `_read_version_commit` giải mã JSON thất bại; đồng thời `get_remote_commit` phụ thuộc REST API `api.github.com` (dễ dính cache CDN 60s hoặc limit 60 req/giờ) và bỏ qua `git ls-remote` khi thiếu `.git`.
  2. `GET /api/check-update` gọi thẳng `check_update` mỗi lần tải/làm mới trình duyệt, nên nếu có commit mới trong lúc người dùng đang chạy công cụ thì có thể tự hiện thông báo giữa phiên thay vì đợi tới phiên khởi động tiếp theo.
- **Nguyên nhân gốc**: Thiếu kiểm tra qua Git Smart HTTP (`info/refs?service=git-upload-pack`), thiếu `utf-8-sig` khi đọc `version.json`, `run_update` trên repo `.git` làm bẩn `version.json`, và `create_app` chưa đóng băng kết quả kiểm tra cập nhật cho riêng phiên khởi động.
- **Bất biến cần bảo vệ**:
  - `get_remote_commit` ưu tiên Git Smart HTTP (`info/refs?service=git-upload-pack`, không giới hạn REST API và phản ánh tức thì mọi commit mới push lên `main`), kiểm tra SHA hợp lệ qua `_is_valid_sha`, và hỗ trợ đầy đủ fallback (REST API, `git ls-remote`, `curl.exe`).
  - `_read_version_commit` dùng `encoding="utf-8-sig"` để đọc an toàn cả file có BOM lẫn không BOM; `dong_goi_chia_se.bat` ghi `$stage\version.json` không BOM và không làm bẩn repo gốc.
  - `run_update` trên repo `.git` thành công không ghi đè bẩn `version.json` (và khôi phục `version.json` nếu `git pull` thất bại trước khi rơi về ZIP).
  - `GET /api/check-update` mặc định (`live=False`) chỉ trả về snapshot của phiên khởi động (`TOMTAT_STARTUP_UPDATE_INFO`), tuyệt đối không hiện thông báo cập nhật tự động giữa phiên khi có commit mới sau lúc khởi động (chỉ báo ở phiên khởi động tiếp theo); sau `POST /api/self-update` thành công mới cập nhật snapshot phiên về `up_to_date`.
- **Regression Test**: `tests/test_regressions.py::test_bug_023_every_commit_detected_across_smart_http_bom_and_git`, `test_bug_023_startup_only_update_notification_ignores_mid_session_commits`, `test_bug_023_frontend_and_packaging_update_contract`

### BUG-024: Tối ưu toàn diện hệ thống cập nhật (restart server Python, manifest dọn file cũ, commit summary, startup cache, `config.local.yaml`, guard trước khi push)
- **Commit / Nguồn**: Sửa đổi ngày 2026-11-20 trong `src/tomtat/{updater.py,config.py,web/app.py,web/routes/system.py,web/static/modules/modals.js}`, `khoi_dong.bat`, `dong_goi_chia_se.bat`, `day_cap_nhat.bat`, `.gitignore`
- **File / Symbol**: `src/tomtat/updater.py` (`LOCAL_ONLY_FILES`, `run_update_detailed`, `_fetch_update_summary`, `consume_startup_update_cache`, `_read_manifest`, `_write_manifest`), `src/tomtat/config.py` (`load_config`, `_deep_merge`, `Config.reload`), `src/tomtat/web/routes/system.py` (`api_self_update`), `src/tomtat/web/app.py` (`get_startup_update_info`), `src/tomtat/web/static/modules/modals.js` (`initUpdateChecker`), `khoi_dong.bat`, `dong_goi_chia_se.bat`, `day_cap_nhat.bat`
- **Triệu chứng**:
  1. Cập nhật code `.py` qua Web UI chỉ reload trình duyệt nên Python trong RAM vẫn chạy bản cũ.
  2. Cập nhật ZIP không xóa được các file mã nguồn/prompt cũ đã bị xóa trên GitHub.
  3. Banner web chưa hiện nội dung thay đổi của commit mới; `khoi_dong.bat` + `cmd_review` gọi kiểm tra GitHub 2 lần lúc mở máy.
  4. `day_cap_nhat.bat` chưa chạy guard/test trước khi push và thiếu `pyproject.toml`/`.agents`; máy người dùng sửa `config.yaml` dễ mất cấu hình khi cập nhật ZIP.
- **Nguyên nhân gốc**: Thiếu vòng lặp tự khởi động lại theo mã thoát `42`, thiếu manifest file gói, thiếu đọc tóm tắt commit và bàn giao cache khởi động, thiếu file cấu hình đè cục bộ `config.local.yaml`.
- **Bất biến cần bảo vệ**:
  - `PROTECTED_ENTRIES` giữ nguyên chính xác 6 mục (`BUG-005`); `LOCAL_ONLY_FILES = {"config.local.yaml"}` được bảo vệ độc lập khi giải nén và dọn manifest ZIP, đồng thời `config.local.yaml` được `load_config` / `reload()` deep-merge lên `config.yaml`.
  - `POST /api/self-update` phải chặn `409` khi có tiến trình AI đang chạy; nếu `changed_python=True` và chạy trong `khoi_dong.bat` (`TOMTAT_RESTART_ON_EXIT=1`, không phải `testing`), tự thoát mã `42` để `khoi_dong.bat` khởi động lại với `--no-browser` và frontend chờ server lên lại rồi reload; nếu không auto-restart được thì trả `restart_required=True`.
  - Cập nhật ZIP qua `run_update_detailed` dọn sạch file thừa dựa trên `work/.updater_manifest.json` mà tuyệt đối không chạm tới `PROTECTED_ENTRIES` hay `LOCAL_ONLY_FILES`.
  - `day_cap_nhat.bat` phải chạy `.agents/verify_guard.py` và `pytest -q` trước khi commit/push lên `main`.
- **Regression Test**: `tests/test_regressions.py::test_bug_024_zip_update_cleans_obsolete_files_and_protects_local_config`, `test_bug_024_config_local_yaml_deep_merge_and_reload`, `test_bug_024_update_summary_and_startup_cache_handoff`, `test_bug_024_self_update_ai_lock_restart_flags_and_scripts`

### BUG-025: Rollback về phiên bản cũ hiển thị nhầm cảnh báo `stale_steps` / `stale_reason` từ snapshot lịch sử
- **Commit / Nguồn**: Sửa đổi ngày 2026-11-20 trong `src/tomtat/services/file_service.py`
- **File / Symbol**: `src/tomtat/services/file_service.py` (`FileService.restore_draft`)
- **Triệu chứng**: Khi người dùng rollback về một phiên bản cũ từng được lưu ngay sau một bước AI đơn lẻ (vd `v005` có `source="ai:extract"`), giao diện hiển thị lại cảnh báo `"Bản trích chọn vừa được AI tạo lại. Bạn có muốn AI chỉnh lại luôn Bản biên soạn không?"` mặc dù người dùng vừa rollback chứ không phải vừa chạy AI.
- **Nguyên nhân gốc**: `FileService.restore_draft` sao chép `snap.get("stale_steps")` và `snap.get("stale_reason")` từ snapshot cũ sang state hiện tại và bản ghi `restore` mới.
- **Bất biến cần bảo vệ**:
  - `FileService.restore_draft` **tuyệt đối không** sao chép `stale_steps` hoặc `stale_reason` từ snapshot được khôi phục hay giữ lại từ state trước rollback; sau khi rollback, `st["stale_steps"] == []` và `st["stale_reason"] is None` (cả trong `state.json` lẫn bản chụp `restore` mới).
- **Regression Test**: `tests/test_regressions.py::test_bug_025_rollback_clears_stale_steps_and_stale_reason`

### BUG-026: Chỉnh sửa phong cách thủ công vẫn giữ nguyên ghi chú giải thích của phong cách AI cũ
- **Commit / Nguồn**: Sửa đổi ngày 2026-11-20 trong `src/tomtat/{pipeline.py,models.py,report.py,memory.py,services/file_service.py,web/routes/process.py,web/static/modules/editor.js}`
- **File / Symbol**: `src/tomtat/pipeline.py` (`sync_manual_label_metadata`, `LABEL_REVIEW_FLAG`, `step_label`, `process_file`), `src/tomtat/services/file_service.py` (`_apply_draft_fields`, `restore_draft`, `get_file_detail`), `src/tomtat/web/routes/process.py` (`api_rerun`), `src/tomtat/report.py` (`infer_step_models_from_logs`), `src/tomtat/memory.py` (`record_approved_sample`), `src/tomtat/web/static/modules/editor.js` (`renderLabelSection`, `renderSummaryMeta`, `autoSaveSilent`, `onStyleChange`)
- **Triệu chứng**: Khi người duyệt chỉnh sửa `#sel-style` (vd từ `"báo chí - công luận"` sang `"khoa học"`) và chạy lại Bản trích chọn & Bản biên soạn (`extract_abstract`), phiên bản mới được tạo và nội dung trích chọn/biên soạn đổi sang `"khoa học"`, nhưng phần ghi chú `#label-reason` bên dưới mục phong cách vẫn hiển thị giải thích của phong cách cũ (`"Văn bản thuộc phong cách báo chí - công luận do..."`), kèm badge `Độ chắc chắn: trung_binh`, model badge AI cũ và cờ cảnh báo xem lại nhãn.
- **Nguyên nhân gốc**: `_apply_draft_fields` và `api_rerun` chỉ gán lại `st["label"]["value"]` mà không đồng bộ `reason`, `confidence`, `per_doc`, `model_used` và `LABEL_REVIEW_FLAG`; `infer_step_models_from_logs` suy ngược `model_used` từ log cũ nếu trường trống; và `autoSaveSilent` trong `editor.js` không cập nhật lại `#label-reason`, `#label-conf`, `#model-badge-label`, `#label-per-doc` và `flags`.
- **Bất biến cần bảo vệ**:
  - Mọi đường cập nhật phong cách thủ công (`save_draft`, `approve_and_export`, `restore_draft`, `api_rerun`, `get_file_detail` cho state lệch cũ) đều phải đi qua `sync_manual_label_metadata`: khi phong cách khác gợi ý AI gốc, lưu dữ liệu AI ban đầu vào `ai_*` (bảo toàn `model_label` / `ai_confidence` cho `memory.py`), đặt `manual_edited=True`, `confidence="thu_cong"` (vẫn truthy cho `process_file(..., restart=False)`), `per_doc=[]`, `model_used=None`, cập nhật `reason` rõ ràng theo phong cách mới và gỡ `LABEL_REVIEW_FLAG`.
  - Khi người dùng chọn quay lại đúng phong cách AI ban đầu, khôi phục nguyên vẹn `reason`, `confidence`, `per_doc`, `model_used` và `LABEL_REVIEW_FLAG` gốc; khi chạy `only_step="label"` hoặc `restart=True`, reset `manual_edited=False`.
  - `infer_step_models_from_logs` không được ghi đè `model_used` từ log cũ lên `label` khi `label.manual_edited` đang bật; `autoSaveSilent` ở frontend luôn đồng bộ lại vùng nhãn và `flags`.
- **Regression Test**: `tests/test_regressions.py::test_bug_026_manual_style_edit_updates_label_reason_and_preserves_through_rerun`, `tests/test_regressions.py::test_bug_026_frontend_label_sync_contract`

### BUG-027: Nâng lên xử lý song song 3 luồng an toàn — tiết kiệm RPD khi bão 503 và chống 429 QPS
- **Commit / Nguồn**: Sửa đổi ngày 2026-11-20 trong `config.yaml`, `src/tomtat/llm/{base,gemini}.py`, `src/tomtat/memory.py`, `src/tomtat/web/templates/index.html`, `AGENTS.md`
- **File / Symbol**: `config.yaml` (`workers: 3`, `max_workers: 3`, `max_transient_per_model`, `model_outage_threshold`, `model_outage_cooldown_s`, `global_min_dispatch_interval_s`, `qps_global_cooldown_s`), `src/tomtat/llm/gemini.py` (`GeminiClient._reserve_gateway_wait`, `_reserve_payload_wait`, `_schedule_payload_qps_backoff`, `_ordered_models_for_call`, `_has_non_outage_fallback_unlocked`, `generate_json`), `src/tomtat/llm/base.py` (`BaseClient._sem_lock`, `ensure_concurrency_limit`), `src/tomtat/memory.py` (`_MEMORY_LOCK`, `load_memory`, `record_approved_sample`), `src/tomtat/web/templates/index.html` (`#sel-concurrency`)
- **Triệu chứng (rủi ro khi mở lên 3 luồng)**: Chạy nhiều luồng trước đây dễ tạo burst đồng thời kích hoạt lỗi `429 QPS payload trùng lặp/gần giống nhau` trên cổng Google (rồi retry 15 lần với payload giống hệt nhau khiến cửa sổ QPS bị khóa kéo dài liên tiếp qua nhiều file), và khi model chính gặp bão `503` (vốn vẫn bị trừ 1 RPD/lần), 3 luồng quét 3 vòng × 58 keys có nguy cơ đốt tới 522 RPD.
- **Nguyên nhân gốc**: Thiếu bộ giãn nhịp gửi request toàn cục giữa các luồng (`_dispatch_slots`), thiếu cooldown dùng chung ở cổng API khi 1 luồng gặp `429 QPS` (`_global_qps_next_ts`), retry `429 QPS` gửi nguyên xi cùng chuỗi payload, và retry `503` không có ngưỡng ngắt mạch hay hạ nhiệt dùng chung theo model (`_model_503_streak` / `_model_outage_until`).
- **Bất biến cần bảo vệ**:
  - `workers == 3` và `max_workers == 3` (trần cứng giữ nguyên ở 3 tầng qua `clamp_workers` / `get_pool_scaling` / `#sel-concurrency`); toàn bộ 3 luồng vẫn giữ pacing `<= 60/min_interval_s < rpm_limit` RPM/key.
  - Khi `backoff_max > 0.5`: `_reserve_gateway_wait` giãn nhịp các lượt gửi nối tiếp tối thiểu `global_min_dispatch_interval_s` (`1.5s`) mà không chặn nhầm luồng đang chờ RPM dài; khi bất kỳ luồng nào gặp `429 QPS`, `_global_qps_next_ts` hạ nhiệt cổng chung (`qps_global_cooldown_s`), lùi lũy thừa tới 30s kèm jitter và biến đổi nhẹ hậu tố `call_prompt` ở lượt thử lại (giữ nguyên `record["prompt"]` trong log và `payload_sig`).
  - Lỗi `503` / tạm thời bị giới hạn theo cả từng lượt (`max_transient_per_model: 8`) lẫn mạch hạ nhiệt dùng chung cho 3 luồng (`model_outage_threshold: 5`, `model_outage_cooldown_s: 30s`) khi còn model dự phòng khả dụng, ngăn đốt RPD hàng loạt mà vẫn tự phục hồi model chính sau cooldown.
  - `BaseClient._sem_lock` và `memory._MEMORY_LOCK` bảo vệ tài nguyên dùng chung khi thao tác đa luồng.
- **Regression Test**: `tests/test_regressions.py::test_bug_027_three_workers_safe_and_pacing_contract`, `test_bug_027_503_circuit_breaker_and_outage_cooldown_saves_rpd`, `test_bug_027_anti_qps_stagger_shared_cooldown_and_retry_differentiation`, `test_bug_027_memory_and_semaphore_thread_safety`

### BUG-028: Bản biên soạn xuất hiện ký tự `\n` viết thô (`S89777`)
- **Commit / Nguồn**: Sửa đổi ngày 2026-11-20 trong `src/tomtat/{textutil.py,checks/abstract_checks.py,checks/__init__.py,pipeline.py,prompts.py}`, `prompts/abstract.md`, và dữ liệu `work/S89777`
- **File / Symbol**: `src/tomtat/textutil.py` (`find_literal_escapes`, `unescape_escaped_newlines`), `src/tomtat/checks/abstract_checks.py` (`clean_abstract_text`, `clean_multi_doc_abstract`, `check_abstract`), `src/tomtat/pipeline.py` (`_generate_abstract_loop`), `src/tomtat/prompts.py` (`ABSTRACT_SCHEMA`), `prompts/abstract.md`
- **Triệu chứng**: Ở văn bản `S89777.docx`, phần "Bản biên soạn" hiển thị liền một khối có dính chữ `\n` thô (`...tim mạch.\nTheo khuyến cáo...`) thay vì xuống dòng tách đoạn.
- **Nguyên nhân gốc**: `prompts/abstract.md` và `ABSTRACT_SCHEMA` mô tả `"các đoạn cách nhau bằng \\n"`, khiến model đôi lúc double-escape thành `\\n` trong JSON trả về; đồng thời `_generate_abstract_loop` chỉ chạy `clean_multi_doc_abstract` khi `n_docs > 1` (bỏ qua đơn văn bản) và `check_abstract` chưa bắt lỗi chuỗi thoát viết thô (`\n`, `\r`, `\t`).
- **Bất biến cần bảo vệ**:
  - `prompts/abstract.md` và `ABSTRACT_SCHEMA` yêu cầu xuống dòng thực sự trong chuỗi JSON và nghiêm cấm viết ký tự `\n` / `\n\n` theo nghĩa đen.
  - `_generate_abstract_loop` luôn chạy `clean_abstract_text(..., n_docs=n_docs, preserve_source=src_plain)` cho cả **đơn văn bản lẫn đa văn bản** để tự chuyển `\\n`/`\\r\\n`/`\\t` viết thô thành xuống dòng thực mà không làm hỏng đường dẫn Windows hay token có sẵn trong văn bản gốc.
  - `check_abstract` / `run_checks` bắt buộc báo `ERROR` mã `literal_escape` nếu bản biên soạn còn sót ký tự `\n`, `\r`, `\t` viết thô (chặn duyệt/xuất `.docx` và khiến `process_file(..., restart=False)` tự sửa lại state cũ lỗi).
- **Regression Test**: `tests/test_regressions.py::test_bug_028_unescape_and_detect_literal_escapes`, `tests/test_regressions.py::test_bug_028_checks_pipeline_and_prompt_prevent_literal_newline_export`

### BUG-029: Bấm 1 trong 5 nút AI khi đang chạy đủ số luồng cho phép phải chờ lượt, không tạo thêm luồng thứ N+1
- **Commit / Nguồn**: Sửa đổi ngày 2026-11-20 trong `src/tomtat/services/batch_service.py`, `src/tomtat/web/routes/{process,files,review,system}.py`, `src/tomtat/web/static/`, `src/tomtat/web/templates/index.html`
- **File / Symbol**: `src/tomtat/services/batch_service.py` (`BatchManager.reserve_rerun`, `run_rerun_job`, `_run_all`, `_allowed_workers_unlocked`, `_active_slots_unlocked`, `stop_file`, `stop`, `status`), `src/tomtat/web/routes/process.py` (`api_rerun`, `api_process_batch`), `src/tomtat/web/routes/files.py` (`api_files`, `api_get_logs`), `src/tomtat/web/routes/review.py` (`_check_not_running`, `api_approve`), `src/tomtat/web/routes/system.py` (`api_self_update`), `src/tomtat/web/static/{app.js,app.css,modules/{state,sidebar,editor,batch,modals}.js}`
- **Triệu chứng**: Khi hệ thống đang chạy đủ số luồng cho phép (ví dụ 3/3 luồng batch hoặc 3/3 luồng rerun), nếu người dùng mở xem một file khác và bấm 1 trong 5 nút AI (`Gán nhãn lại`, `Trích chọn lại`, `Biên soạn lại`, `Chấm G-Eval`, `Chạy lại cả bài`), request `/api/file/<stem>/rerun` chạy ngay lập tức trên luồng Flask riêng, tạo thành luồng AI thứ 4 vượt quá trần `workers` / `max_workers` và dễ gây burst QPS/RPM.
- **Nguyên nhân gốc**: `BatchManager.start` dùng `ThreadPoolExecutor` tách biệt khỏi `try_acquire_file` của `api_rerun`; `try_acquire_file` chỉ kiểm tra trùng cùng một `stem` mà không dùng chung ngân sách slot luồng hay hàng chờ với các tiến trình đang chạy.
- **Bất biến cần bảo vệ**:
  - Tổng số file đang thực sự chạy AI đồng thời (`len(running_stems | set(rerun_stems))`) **tuyệt đối không bao giờ** vượt quá `allowed_workers` (bị kẹp bởi `workers` / `max_workers` và `concurrency` của batch đang chạy).
  - Khi tất cả slot đã kín, `POST /api/file/<stem>/rerun` (cả `async=True` lẫn `async=False`) phải đưa file vào hàng chờ FIFO `_queued_reruns` (nhường lượt ngay cho file này khi 1 trong các luồng đang chạy hoàn thành, trước các file chưa bắt đầu trong `_batch_pending`), không bao giờ khởi chạy luồng AI thứ `N+1`.
  - File đang trong hàng chờ (`queued_rerun_stems`) vẫn bị chặn `409` ở `/save`, `/restore`, `/approve`, `/self-update`, không thể bấm chạy trùng và có thể bấm `⏹ Hủy chờ AI` / `⏹ Dừng` ngay lập tức mà không tốn lượt gọi API.
- **Regression Test**: `tests/test_regressions.py::test_bug_029_single_file_rerun_waits_when_batch_full_and_never_creates_extra_thread`, `test_bug_029_multiple_single_reruns_and_explicit_batch_concurrency_respected`, `test_bug_029_cancel_queued_rerun_and_frontend_queue_contract`

### BUG-030: Ấn chọn file ở cột bên trái làm danh sách tự cuộn xuống dưới ngay lập tức
- **Commit / Nguồn**: Sửa đổi ngày 2026-11-20 trong `src/tomtat/web/static/{app.css,modules/{utils,sidebar,editor}.js}`, `src/tomtat/web/templates/index.html`
- **File / Symbol**: `src/tomtat/web/static/app.css` (`.file-list`, `.docs-container`, `.versions-list`), `src/tomtat/web/static/modules/utils.js` (`withPreservedScroll`), `src/tomtat/web/static/modules/sidebar.js` (`syncActiveFileItem`, `renderFileList`, `renderDoneList`), `src/tomtat/web/static/modules/editor.js` (`selectFile`, `renderEditor`, `renderDocsUnits`, `renderVersions`, `assembleFromSelections`)
- **Triệu chứng**: Khi người dùng cuộn cột `Chờ duyệt` xuống vùng giữa (vd ở `S89592`) rồi bấm vào một file bất kỳ (vd `S89590`), khung danh sách bên trái lập tức tự động cuộn mạnh xuống dưới (tới tận cuối danh sách `S89512`), làm khuất chính file vừa chọn.
- **Nguyên nhân gốc**: `selectFile` gọi lại `renderFileList()` vốn xóa sạch DOM qua `ul.innerHTML = ""` rồi tạo lại toàn bộ các thẻ `<li>` chỉ để đổi class `.active`; đồng thời `.file-list`, `.docs-container`, `.versions-list` giữ mặc định `overflow-anchor: auto`. Khi trình duyệt Chromium (Chrome/Edge) ở mức zoom `< 100%` (như `90%`), cơ chế Scroll Anchoring của Blink tính lệch hệ số zoom mỗi lần DOM trong vùng cuộn bị phá hủy và dựng lại, đẩy `scrollTop` nhảy tăng vọt xuống dưới; ngoài ra mọi lần `renderFileList` từ SSE/polling cũng đối mặt nguy cơ tương tự hoặc reset về `0`.
- **Bất biến cần bảo vệ**:
  - `.file-list`, `.docs-container` và `.versions-list` trong `app.css` phải đặt `overflow-anchor: none` để tắt Scroll Anchoring gây nhảy `scrollTop` ở các vùng cuộn động khi zoom khác `100%`.
  - `selectFile` không được xóa/dựng lại DOM danh sách bên trái khi file đã có sẵn trong `#file-list` / `#done-list`; phải dùng `syncActiveFileItem()` cập nhật tại chỗ class `.active`.
  - Mọi lần buộc phải dựng lại DOM (`renderFileList`, `renderDoneList`, `renderDocsUnits({ preserveScroll: true })`, `renderVersions`) đều phải bọc qua `withPreservedScroll` để bảo toàn `scrollTop`/`scrollLeft` (chỉ reset về `0` cho `docs-container` và `versions-list` khi chuyển sang file khác).
- **Regression Test**: `tests/test_regressions.py::test_bug_030_sidebar_click_preserves_scroll_contract_and_behavior`

### BUG-031: Jitter trong `_reserve_gateway_wait` cộng sau vòng lặp `slots` làm slot mới đè lên slot đã đặt
- **Commit / Nguồn**: Sửa đổi ngày 2026-11-20 trong `src/tomtat/llm/gemini.py`
- **File / Symbol**: `src/tomtat/llm/gemini.py` (`GeminiClient._reserve_gateway_wait`)
- **Triệu chứng**: Khi nhiều luồng cùng đặt chỗ gửi API qua `_reserve_gateway_wait`, luồng sau có thể nhận thời điểm gửi trùng hoặc cách slot liền sau nhỏ hơn `global_min_dispatch_interval_s` (khiến `test_bug_027_anti_qps_stagger_shared_cooldown_and_retry_differentiation` fail ngẫu nhiên trên Windows và làm 2 luồng có thể bắn API sát nhau).
- **Nguyên nhân gốc**: Sau vòng lặp `for s in slots:` tìm khoảng trống `t_cand`, hàm mới cộng thêm `random.uniform(0.05, 0.25)` khi `t_cand > earliest` mà không kiểm tra lại khoảng cách với các slot tiếp theo (nên có thể nhảy đè vào slot thứ 2 khi có khoảng trống vừa khít hoặc vượt qua biên `s - dispatch_gap`).
- **Bất biến cần bảo vệ**:
  - Jitter phải được cộng **ngay trong bước trượt slot** (`t_cand = s + dispatch_gap + random.uniform(jitter_min, jitter_cap)`) khi phát hiện va chạm, để mọi vòng lặp tiếp theo tiếp tục kiểm tra và đảm bảo `|t_new - s| >= global_min_dispatch_interval_s` với **toàn bộ** các slot đang hiệu lực.
  - `jitter_cap` không vượt quá `0.25s` và tỉ lệ hợp lý theo `dispatch_gap` (`min(0.25, max(0.02, dispatch_gap * 0.5))`).
- **Regression Test**: `tests/test_regressions.py::test_bug_031_reserve_gateway_wait_jitter_never_collides_with_existing_slots`

### BUG-032: Tách model riêng cho `judge` (`model_by_step`) và chuẩn hóa G-Eval (`JUDGE_SCHEMA`, `judge.md`, `step_judge`)
- **Commit / Nguồn**: Sửa đổi ngày 2026-11-20 trong `config.yaml`, `src/tomtat/{llm/{base,gemini}.py,prompts.py,pipeline.py,web/templates/index.html,web/static/modules/editor.js}`, `prompts/judge.md`, `.agents/verify_guard.py`
- **File / Symbol**: `config.yaml` (`gemini.model_by_step`, `gemini.fallback_models_by_step`, `judge.only_if_checks_ok`), `src/tomtat/llm/gemini.py` (`GeminiClient._normalize_step_model_maps`, `models_chain_for_step`, `_all_configured_models`, `generate_json`), `src/tomtat/llm/base.py` (`get_rpd_status`), `src/tomtat/prompts.py` (`JUDGE_SCHEMA`), `prompts/judge.md`, `src/tomtat/pipeline.py` (`step_judge`, `process_file`), `src/tomtat/web/templates/index.html` (`#model-badge-judge`), `src/tomtat/web/static/modules/editor.js` (`renderJudge`, `renderSummaryMeta`)
- **Triệu chứng**:
  1. `step_judge` dùng chung chuỗi model chính bắt đầu từ `gemini-4-hydrogen` (chỉ 20 RPD/key) nên bật `judge.enabled` làm hao quota quý giá của bước biên soạn/trích chọn.
  2. `JUDGE_SCHEMA` đặt 4 điểm số *trước* `issues`/`summary`, `judge.md` thiếu thang neo điểm 1–5 chi tiết và quy tắc đa văn bản độc lập, đồng thời `step_judge` không truyền `query`/`docs`, không lưu `model_used` lên `st["judge"]`, không dọn cờ điểm thấp cũ khi chạy lại, và vẫn đốt API gọi `judge` trong batch kể cả khi bản nháp còn lỗi cơ học (`checks.ok == False`).
- **Nguyên nhân gốc**: Thiếu cơ chế định tuyến chuỗi model theo bước trong `GeminiClient`, schema JSON chưa tuân theo thứ tự suy luận trước điểm số của G-Eval, và `step_judge` chưa đồng bộ tham số ngữ cảnh với `abstract.md`.
- **Bất biến cần bảo vệ**:
  - `model_by_step.judge` (`gemini-3.5-flash-lite`) và `fallback_models_by_step.judge` (`["gemini-3.1-flash-lite"]`) dùng riêng pool 500 RPD/key cho bước `judge` và không nhảy ngược lên `gemini-4-hydrogen`; khi gặp `401/403`, key lỗi vẫn bị loại khỏi toàn bộ `_all_configured_models()`.
  - `JUDGE_SCHEMA` luôn đặt `issues` và `summary` **trước** 4 trường điểm số (`relevance`, `coherence`, `consistency`, `fluency`).
  - `step_judge` truyền đủ `docs` và `query` vào `judge.md`, kẹp điểm trong `[1.0, 5.0]`, lưu `st["judge"]["model_used"]`, và dọn sạch cờ điểm thấp/bỏ qua G-Eval cũ; ở chế độ batch (`process_file`), khi `judge.only_if_checks_ok=True` không gọi G-Eval cho bản nháp chưa đạt `checks.ok` (nhưng chạy riêng `only_step="judge"` vẫn hoạt động theo yêu cầu).
- **Regression Test**: `tests/test_regressions.py::test_bug_032_judge_step_model_routing_and_config_contract`, `test_bug_032_judge_schema_reasoning_before_score_and_prompt_context`, `test_bug_032_step_judge_passes_query_docs_saves_model_and_respects_checks_gate`

### BUG-033: Nâng cấp `day_cap_nhat.bat` đẩy lên GitHub 1-click an toàn và đồng bộ cho mọi máy cập nhật
- **Commit / Nguồn**: Sửa đổi ngày 2026-11-20 trong `day_cap_nhat.bat`, `.agents/push_helper.py`, `.gitignore`
- **File / Symbol**: `day_cap_nhat.bat`, `.agents/push_helper.py` (`is_forbidden_staged_path`, `check_staged`, `build_commit_message`), `.gitignore`
- **Triệu chứng**:
  1. `day_cap_nhat.bat` dùng wildcard `*.txt` khi `git add` (dễ dính file ghi chú tạm hoặc đụng `api_keys.txt`), mỗi lần chạy lại ghi đè `.github_repo_url` gây diff giả, bỏ qua `version.json` / `.gitkeep` nếu mới tạo, và dùng commit message cố định không phản ánh thay đổi trên banner cập nhật web.
  2. Thiếu kiểm tra nhánh `main` / detached HEAD, thiếu đồng bộ `git fetch` + `pull --rebase` khi `origin/main` có commit mới, thiếu kiểm tra lỗi `git commit` và thiếu khóa kiểm tra bí mật đang stage trước khi push.
- **Nguyên nhân gốc**: Script batch chỉ dừng ở mức `git add ... && git commit && git push` tuần tự, chưa phòng vệ trạng thái Git, xung đột remote, hoặc file nhạy cảm.
- **Bất biến cần bảo vệ**:
  - `day_cap_nhat.bat` phải chạy thuần ASCII + CRLF, giữ nguyên kiểm tra `verify_guard.py`, `pytest.exe` và `pyproject.toml` (`BUG-024`), không dùng `*.txt` wildcard và không dùng khối `if (...)` chứa biến đường dẫn (`BUG-007`).
  - Chỉ ghi `.github_repo_url` khi thiếu hoặc khác `DEFAULT_REPO`; luôn đảm bảo đứng trên nhánh `main`, kiểm tra bí mật qua `push_helper.py check-staged` (cấm `api_keys.txt`, `.env`, `config.local.yaml`, `input/*.docx`, `output/*`, `work/*` trừ `work/learned_memory.json` và `.gitkeep`), tự động sinh commit message từ thay đổi (`BUG-XXX` / vùng sửa), và `git fetch` + `pull --rebase` trước khi `git push -u origin main`.
- **Regression Test**: `tests/test_regressions.py::test_bug_033_push_helper_staged_safety_and_commit_message`, `test_bug_033_day_cap_nhat_bat_contract_and_e2e`

---

## Mẫu Ghi Lỗi Mới (Copy khi fix bug mới)

```markdown
### BUG-XXX: <Tên lỗi ngắn gọn>
- **Commit / Nguồn**: `<commit hoặc ngày>`
- **File / Symbol**: `src/tomtat/...` (`ten_ham`)
- **Triệu chứng**: Mô tả lỗi quan sát thấy.
- **Nguyên nhân gốc**: Giải thích tại sao xảy ra ở tầng logic/dữ liệu.
- **Bất biến cần bảo vệ**: Điều kiện tuyệt đối không được phá vỡ về sau.
- **Regression Test**: `tests/test_regressions.py::test_bug_xxx_...`
```
